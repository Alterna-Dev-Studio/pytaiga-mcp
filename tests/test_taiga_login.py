"""Unit tests for taiga_login.py — device-code sign-in that ends in a Taiga Application token."""

import argparse
from unittest.mock import MagicMock, patch

import pytest
from pytaigaclient.exceptions import TaigaBadRequestError

from src import taiga_login
from src.config import TaigaSettings

ISSUER = "https://login.example"


def _response(ok, payload, status=200):
    response = MagicMock(ok=ok, status_code=status, url=f"{ISSUER}/oauth/v2/token")
    response.json.return_value = payload
    return response


def _bad_request(detail):
    error = TaigaBadRequestError.__new__(TaigaBadRequestError)
    Exception.__init__(error, detail)
    error.status_code = 400
    error.error_detail = detail
    return error


def test_start_device_authorization_posts_client_and_scope():
    http = MagicMock()
    http.post.return_value = _response(
        True, {"device_code": "dev", "user_code": "AB12-CD34", "interval": 5, "expires_in": 300}
    )
    device = taiga_login.start_device_authorization(ISSUER, "cli-id", http)
    assert device["user_code"] == "AB12-CD34"
    assert http.post.call_args.args[0] == f"{ISSUER}/oauth/v2/device_authorization"
    assert http.post.call_args.kwargs["data"] == {
        "client_id": "cli-id",
        "scope": "openid email profile",
    }


def test_start_device_authorization_reports_refusal_without_secrets():
    http = MagicMock()
    http.post.return_value = _response(False, {"error": "invalid_client"}, 401)
    with pytest.raises(taiga_login.LoginError, match="invalid_client"):
        taiga_login.start_device_authorization(ISSUER, "cli-id", http)


def test_poll_waits_then_returns_access_token_and_honours_slow_down():
    http = MagicMock()
    http.post.side_effect = [
        _response(False, {"error": "authorization_pending"}, 400),
        _response(False, {"error": "slow_down"}, 400),
        _response(True, {"access_token": "zitadel-access"}),
    ]
    sleeps = []
    token = taiga_login.poll_device_token(
        ISSUER,
        "cli-id",
        {"device_code": "dev", "interval": 2, "expires_in": 300},
        http,
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    assert token == "zitadel-access"
    assert sleeps == [2.0, 7.0]
    assert http.post.call_args.kwargs["data"]["grant_type"] == taiga_login.DEVICE_GRANT


@pytest.mark.parametrize(
    "error, message",
    [
        ("expired_token", "expired"),
        ("access_denied", "declined"),
        ("invalid_grant", "invalid_grant"),
    ],
)
def test_poll_stops_on_terminal_errors(error, message):
    http = MagicMock()
    http.post.return_value = _response(False, {"error": error}, 400)
    with pytest.raises(taiga_login.LoginError, match=message):
        taiga_login.poll_device_token(
            ISSUER, "cli-id", {"device_code": "dev"}, http, sleep=lambda s: None, clock=lambda: 0.0
        )


def test_poll_gives_up_at_the_deadline():
    http = MagicMock()
    http.post.return_value = _response(False, {"error": "authorization_pending"}, 400)
    ticks = iter([0.0, 400.0])
    with pytest.raises(taiga_login.LoginError, match="expired"):
        taiga_login.poll_device_token(
            ISSUER,
            "cli-id",
            {"device_code": "dev", "expires_in": 300},
            http,
            sleep=lambda s: None,
            clock=lambda: next(ticks),
        )


def test_taiga_login_posts_openid_access_token_and_keeps_session():
    api = MagicMock()
    api.post.return_value = {"auth_token": "taiga-jwt", "refresh": "r"}
    taiga_login.taiga_login_with_access_token(api, "zitadel-access", "https://taiga.example")
    assert api.post.call_args.args[0] == "/auth"
    assert api.post.call_args.kwargs["json"] == {
        "type": "openid",
        "access_token": "zitadel-access",
        "url": "https://taiga.example/login",
    }
    api.update_token.assert_called_once_with("taiga-jwt", "Bearer")


def test_taiga_login_explains_unlinked_user_and_missing_plugin():
    api = MagicMock()
    api.post.side_effect = _bad_request("unable to locate user")
    with pytest.raises(taiga_login.LoginError, match="sign in to Taiga once"):
        taiga_login.taiga_login_with_access_token(api, "t", "https://taiga.example")
    api.post.side_effect = _bad_request("invalid login type")
    with pytest.raises(taiga_login.LoginError, match="no OpenID login plugin"):
        taiga_login.taiga_login_with_access_token(api, "t", "https://taiga.example")


def test_mint_application_token_round_trips_state():
    api = MagicMock()

    def post(path, json):
        if path == "/application-tokens/authorize":
            return {"auth_code": "code", "state": json["state"], "next_url": "x"}
        assert json["auth_code"] == "code"
        return {"token": "app-token"}

    api.post.side_effect = post
    assert taiga_login.mint_application_token(api, "app-uuid") == "app-token"
    assert api.post.call_args_list[0].kwargs["json"]["application"] == "app-uuid"


def test_mint_application_token_rejects_state_mismatch():
    api = MagicMock()
    api.post.return_value = {"auth_code": "code", "state": "other"}
    with pytest.raises(taiga_login.LoginError, match="TAIGA_APPLICATION_ID"):
        taiga_login.mint_application_token(api, "app-uuid")


def test_revoke_deletes_each_token():
    api = MagicMock()
    api.get.return_value = [{"id": 1}, {"id": 2}]
    assert taiga_login.revoke_application_tokens(api, "app-uuid") == 2
    assert [c.args[0] for c in api.delete.call_args_list] == [
        "/application-tokens/1",
        "/application-tokens/2",
    ]


def test_main_prints_only_the_token(capsys):
    with (
        patch.object(taiga_login, "run", return_value="app-token"),
        patch.object(taiga_login.sys.stdout, "isatty", return_value=False),
    ):
        assert taiga_login.main(["--no-browser"]) == 0
    out, err = capsys.readouterr()
    assert out == "app-token\n"
    assert "app-token" not in err


def test_main_reports_missing_settings():
    with patch.object(taiga_login, "run", side_effect=ValueError("taiga-login needs X")):
        assert taiga_login.main(["--no-browser"]) == 1


def test_run_wires_device_flow_to_taiga(capsys):
    http = MagicMock()
    http.post.side_effect = [
        _response(
            True,
            {
                "device_code": "dev",
                "user_code": "AB12-CD34",
                "verification_uri_complete": f"{ISSUER}/device?user_code=AB12-CD34",
                "interval": 0,
                "expires_in": 300,
            },
        ),
        _response(True, {"access_token": "zitadel-access"}),
    ]
    api = MagicMock()
    api.post.side_effect = [
        {"auth_token": "taiga-jwt"},
        {"auth_code": "code", "state": None},
        {"token": "app-token"},
    ]
    good = TaigaSettings(
        TAIGA_API_URL="https://taiga.example",
        TAIGA_OIDC_ISSUER=ISSUER,
        TAIGA_OIDC_CLIENT_ID="cli-id",
        TAIGA_APPLICATION_ID="app-uuid",
    )
    with (
        patch.object(taiga_login, "settings", good),
        patch.object(taiga_login.TaigaClientWrapper, "_build_client", return_value=api),
        patch.object(taiga_login.time, "sleep"),
        patch.object(taiga_login, "mint_application_token", return_value="app-token") as mint,
    ):
        token = taiga_login.run(argparse.Namespace(no_browser=True, revoke=False), http=http)
    assert token == "app-token"
    mint.assert_called_once_with(api, "app-uuid")
    api.update_token.assert_called_once_with("taiga-jwt", "Bearer")
    err = capsys.readouterr().err
    assert "AB12-CD34" in err and "zitadel-access" not in err and "taiga-jwt" not in err


def test_oidc_settings_name_missing_values_and_require_https():
    with pytest.raises(ValueError, match="TAIGA_OIDC_ISSUER, TAIGA_OIDC_CLIENT_ID"):
        TaigaSettings(TAIGA_APPLICATION_ID="x").get_oidc_login_settings()
    with pytest.raises(ValueError, match="https://"):
        TaigaSettings(
            TAIGA_OIDC_ISSUER="http://login.example",
            TAIGA_OIDC_CLIENT_ID="c",
            TAIGA_APPLICATION_ID="a",
        ).get_oidc_login_settings()
    assert TaigaSettings(
        TAIGA_OIDC_ISSUER="https://login.example/",
        TAIGA_OIDC_CLIENT_ID="c",
        TAIGA_APPLICATION_ID="a",
    ).get_oidc_login_settings() == ("https://login.example", "c", "a")
