"""Unit tests for taiga_client.py — the _CompatTaigaClient query_params shim (#87)."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from pytaigaclient.exceptions import TaigaException

from src.config import TaigaSettings
from src.taiga_client import (
    TaigaClientWrapper,
    _CompatTaigaClient,
    _PangolinSession,
    resolve_user_id,
    safe_lower,
)


def _client():
    # Constructor does no network I/O — it only builds a session + resource objects.
    return _CompatTaigaClient(host="https://taiga.example")


class TestCompatTaigaClientGet:
    """Regression guard for #87 — pytaigaclient's Tasks methods call
    get(endpoint, query_params=...), which the base TaigaClient.get rejects. The
    shim remaps that stray kwarg onto params= for every resource at one chokepoint."""

    def test_remaps_query_params_to_params(self):
        c = _client()
        with patch.object(_CompatTaigaClient, "_request", return_value={"ok": True}) as req:
            result = c.get("/tasks/by_ref", query_params={"ref": 7, "project": 1})
        assert result == {"ok": True}
        assert req.call_args.kwargs["params"] == {"ref": 7, "project": 1}
        assert "query_params" not in req.call_args.kwargs

    def test_params_passthrough_unaffected(self):
        c = _client()
        with patch.object(_CompatTaigaClient, "_request", return_value=[]) as req:
            c.get("/userstories", params={"project": 1})
        assert req.call_args.kwargs["params"] == {"project": 1}
        assert "query_params" not in req.call_args.kwargs

    def test_no_params_at_all_passes_none(self):
        c = _client()
        with patch.object(_CompatTaigaClient, "_request", return_value=None) as req:
            c.get("/tasks")  # mirrors tasks.list() with query_params=None
        assert req.call_args.kwargs["params"] is None

    def test_explicit_params_win_on_conflict(self):
        c = _client()
        with patch.object(_CompatTaigaClient, "_request", return_value=None) as req:
            c.get("/x", params={"a": "explicit"}, query_params={"a": "stray", "b": 2})
        assert req.call_args.kwargs["params"] == {"a": "explicit", "b": 2}


class TestBuggyTasksMethodsNowWork:
    """Drive the actual pytaigaclient Tasks methods (the ones that pass
    query_params=) through the shim and confirm they no longer raise and route
    params correctly. These are the latent siblings of the get_by_ref bug."""

    def test_get_by_ref(self):
        c = _client()
        with patch.object(
            _CompatTaigaClient, "_request", return_value={"id": 200, "ref": 7}
        ) as req:
            result = c.tasks.get_by_ref(ref=7, project=1)
        assert result == {"id": 200, "ref": 7}
        assert req.call_args.args == ("GET", "/tasks/by_ref")
        assert req.call_args.kwargs["params"] == {"ref": 7, "project": 1}
        assert "query_params" not in req.call_args.kwargs

    def test_list(self):
        c = _client()
        with patch.object(_CompatTaigaClient, "_request", return_value=[]) as req:
            c.tasks.list(query_params={"project": 1, "user_story": 50})
        assert req.call_args.kwargs["params"] == {"project": 1, "user_story": 50}
        assert "query_params" not in req.call_args.kwargs

    def test_filters_data(self):
        c = _client()
        with patch.object(_CompatTaigaClient, "_request", return_value={}) as req:
            c.tasks.filters_data(project_id=1)
        assert "query_params" not in req.call_args.kwargs
        assert req.call_args.kwargs["params"] == {"project": 1}

    def test_list_attachments(self):
        c = _client()
        with patch.object(_CompatTaigaClient, "_request", return_value=[]) as req:
            c.tasks.list_attachments(project=1, object_id=200)
        assert "query_params" not in req.call_args.kwargs
        assert req.call_args.kwargs["params"] == {"project": 1, "object_id": 200}


class TestTaigaClientWrapperLogin:
    """Cover the auth path: empty-host guard, success, and the two failure modes."""

    @pytest.fixture(autouse=True)
    def _without_local_pangolin_config(self):
        with (
            patch.object(TaigaSettings, "get_pangolin_credentials", return_value=None),
            patch.object(TaigaSettings, "get_pangolin_share_link", return_value=None),
        ):
            yield

    def test_init_rejects_empty_host(self):
        with pytest.raises(ValueError, match="host URL cannot be empty"):
            TaigaClientWrapper(host="")

    def test_login_success_sets_api(self):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        fake_api = MagicMock()
        with patch("src.taiga_client._CompatTaigaClient", return_value=fake_api):
            ok = wrapper.login(username="u", password="p")
        assert ok is True
        assert wrapper.api is fake_api
        fake_api.auth.login.assert_called_once_with(username="u", password="p")

    def test_login_taiga_exception_propagates_and_clears_api(self):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        fake_api = MagicMock()
        fake_api.auth.login.side_effect = TaigaException("bad creds")
        with patch("src.taiga_client._CompatTaigaClient", return_value=fake_api):
            with pytest.raises(TaigaException, match="bad creds"):
                wrapper.login(username="u", password="p")
        assert wrapper.api is None

    def test_login_unexpected_error_wrapped(self):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        fake_api = MagicMock()
        fake_api.auth.login.side_effect = RuntimeError("boom")
        with patch("src.taiga_client._CompatTaigaClient", return_value=fake_api):
            with pytest.raises(TaigaException, match="Unexpected login error"):
                wrapper.login(username="u", password="p")
        assert wrapper.api is None

    def test_pangolin_headers_survive_login_and_api_request(self):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        with patch.object(TaigaSettings, "get_pangolin_credentials", return_value=("id", "secret")):
            with patch.object(_PangolinSession, "request") as request:
                auth_response = MagicMock(ok=True, status_code=200)
                auth_response.json.return_value = {"auth_token": "taiga"}
                api_response = MagicMock(ok=True, status_code=200)
                api_response.json.return_value = {"projects": []}
                request.side_effect = [auth_response, api_response]
                wrapper.login("u", "p")
                wrapper.api.get("/projects")
        assert request.call_count == 2
        assert request.call_args_list[0].kwargs["headers"]["P-Access-Token"] == "secret"
        assert request.call_args_list[1].kwargs["headers"]["P-Access-Token"] == "secret"
        assert request.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer taiga"
        assert wrapper.api.session.headers["P-Access-Token-Id"] == "id"
        assert wrapper.api.session.headers["P-Access-Token"] == "secret"

    def test_pangolin_headers_are_recreated_on_login(self):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        with patch.object(TaigaSettings, "get_pangolin_credentials", return_value=("id", "secret")):
            with patch.object(_CompatTaigaClient, "_request", return_value={"auth_token": "taiga"}):
                wrapper.login("u", "p")
                first_session = wrapper.api.session
                wrapper.login("u", "p")
        assert wrapper.api.session is not first_session
        assert wrapper.api.session.headers["P-Access-Token"] == "secret"

    def test_incomplete_pangolin_configuration_does_not_log_secret(self, caplog):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        with patch.object(
            TaigaSettings,
            "get_pangolin_credentials",
            side_effect=ValueError("Both Pangolin access token variables are required"),
        ):
            with pytest.raises(
                TaigaException, match="Both Pangolin access token variables are required"
            ):
                wrapper.login("u", "p")
        assert "secret" not in caplog.text

    def test_share_link_exchanges_for_resource_cookie_before_taiga_login(self):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        dashboard_response = MagicMock(ok=True)
        dashboard_response.json.return_value = {
            "data": {"session": "short-lived", "redirectUrl": "https://taiga.example/discover"}
        }
        exchange_response = MagicMock(status_code=302)
        fake_api = MagicMock()
        with (
            patch.object(
                TaigaSettings,
                "get_pangolin_share_link",
                return_value=("https://connect.example", "share-secret"),
            ),
            patch.object(requests.Session, "post", return_value=dashboard_response) as post,
            patch.object(_PangolinSession, "get", autospec=True) as get,
            patch("src.taiga_client._CompatTaigaClient", return_value=fake_api) as client,
        ):

            def exchange_cookie(self, *args, **kwargs):
                self.cookies.set("resource", "cookie", domain="taiga.example")
                return exchange_response

            get.side_effect = exchange_cookie
            assert wrapper.login("u", "p")
        post.assert_called_once()
        assert post.call_args.args[0] == "https://connect.example/api/v1/auth/access-token"
        assert post.call_args.kwargs["json"] == {"accessToken": "share-secret"}
        assert "resource_session_request_param=short-lived" in get.call_args.args[1]
        assert get.call_args.kwargs["allow_redirects"] is False
        assert client.call_args.kwargs["session"] is get.call_args.args[0]
        fake_api.auth.login.assert_called_once_with(username="u", password="p")

    def test_share_link_rejects_wrong_resource_without_exposing_secret(self, caplog):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        dashboard_response = MagicMock(ok=True)
        dashboard_response.json.return_value = {
            "data": {"session": "short-lived", "redirectUrl": "https://other.example/discover"}
        }
        with (
            patch.object(
                TaigaSettings,
                "get_pangolin_share_link",
                return_value=("https://connect.example", "share-secret"),
            ),
            patch.object(requests.Session, "post", return_value=dashboard_response),
            patch.object(_PangolinSession, "get") as get,
        ):
            with pytest.raises(TaigaException, match="different Taiga resource"):
                wrapper.login("u", "p")
        get.assert_not_called()
        assert "share-secret" not in caplog.text
        assert "short-lived" not in caplog.text


def test_pangolin_settings_require_both_values():
    complete = TaigaSettings(
        PANGOLIN_RESOURCE_TOKEN="", PANGOLIN_ACCESS_TOKEN_ID="id", PANGOLIN_ACCESS_TOKEN="secret"
    )
    assert complete.get_pangolin_credentials() == ("id", "secret")
    partial = TaigaSettings(
        PANGOLIN_RESOURCE_TOKEN="", PANGOLIN_ACCESS_TOKEN_ID="id", PANGOLIN_ACCESS_TOKEN=""
    )
    with pytest.raises(ValueError, match="Both Pangolin"):
        partial.get_pangolin_credentials()


def test_pangolin_settings_accept_combined_resource_token():
    settings = TaigaSettings(PANGOLIN_RESOURCE_TOKEN="id.secret")
    assert settings.get_pangolin_credentials() == ("id", "secret")
    malformed = TaigaSettings(PANGOLIN_RESOURCE_TOKEN="secret")
    with pytest.raises(ValueError, match="ID.token"):
        malformed.get_pangolin_credentials()


def test_pangolin_settings_accept_share_link_url():
    settings = TaigaSettings(
        TAIGA_API_URL="https://taiga.example",
        PANGOLIN_RESOURCE_TOKEN="https://taiga.example/?p_token=id.secret",
    )
    assert settings.get_pangolin_credentials() == ("id", "secret")


def test_pangolin_settings_accept_dashboard_share_link():
    settings = TaigaSettings(
        TAIGA_API_URL="https://taiga.example",
        PANGOLIN_RESOURCE_TOKEN="https://connect.example/s/share-secret",
    )
    assert settings.get_pangolin_share_link() == ("https://connect.example", "share-secret")
    assert settings.get_pangolin_credentials() is None


def test_pangolin_settings_accept_combined_token_in_legacy_variable():
    settings = TaigaSettings(PANGOLIN_RESOURCE_TOKEN="", PANGOLIN_ACCESS_TOKEN="id.secret")
    assert settings.get_pangolin_credentials() == ("id", "secret")


def test_pangolin_settings_reject_ai_gateway_key():
    settings = TaigaSettings(
        PANGOLIN_RESOURCE_TOKEN="", PANGOLIN_ACCESS_TOKEN="pangolin-key-id.secret"
    )
    with pytest.raises(ValueError, match="AI Gateway keys"):
        settings.get_pangolin_credentials()


def test_pangolin_session_strips_credentials_on_cross_origin_redirect():
    session = _PangolinSession()
    original = requests.Request("GET", "https://taiga.example/api/v1/projects").prepare()
    redirected = requests.Request("GET", "https://other.example/projects").prepare()
    redirected.headers["P-Access-Token-Id"] = "id"
    redirected.headers["P-Access-Token"] = "secret"
    response = requests.Response()
    response.request = original
    session.rebuild_auth(redirected, response)
    assert "P-Access-Token-Id" not in redirected.headers
    assert "P-Access-Token" not in redirected.headers


class TestSafeLower:
    """None-safe lowercasing (pytaiga-mcp#120)."""

    def test_none_becomes_empty_string(self):
        assert safe_lower(None) == ""

    def test_lowercases_string(self):
        assert safe_lower("Alice@Example.COM") == "alice@example.com"

    def test_empty_string_stays_empty(self):
        assert safe_lower("") == ""


class TestResolveUserId:
    """Shared None-safe user resolver used by both server flavors (pytaiga-mcp#120)."""

    def _members(self):
        """A REAL /memberships payload.

        Membership rows carry no ``user_extra_info`` and no ``username`` field at
        all, and the flat ``email`` is frequently blank on established members
        while ``user_email`` holds the real address. The previous fixture supplied
        ``user_extra_info``, so username/display-name tests passed against a
        payload Taiga never sends while those code paths were dead in production.
        """
        return [
            # pending-invite membership with null fields — must not abort matching
            {"user": None, "email": None, "user_email": None, "full_name": None},
            {
                "user": 42,
                "email": "",
                "user_email": "alice@example.com",
                "full_name": "Alice Martin",
            },
        ]

    def _members_with_extra_info(self):
        """A non-memberships payload that DOES carry ``user_extra_info``.

        Only for the forward-compatibility lookups: ``resolve_user_id`` accepts
        member lists from other endpoints, so it still probes
        ``user_extra_info.username`` / ``.full_name_display``. Kept separate so it
        is never mistaken for a memberships shape.
        """
        return [
            {
                "user": 7,
                "user_extra_info": {"username": "carol", "full_name_display": "Carol Danvers"},
            }
        ]

    def test_int_passes_through_without_lookup(self):
        # No members needed — an int is a user ID and is returned unchanged.
        assert resolve_user_id([], 42) == 42

    def test_bool_is_rejected(self):
        with pytest.raises(ValueError, match="int user ID or a name/email"):
            resolve_user_id(self._members(), True)

    def test_empty_identifier_rejected(self):
        with pytest.raises(ValueError, match="Empty username"):
            resolve_user_id(self._members(), "")

    def test_resolves_by_user_email_when_flat_email_is_blank(self):
        # THE regression this fix exists for: on a real memberships row `email` is
        # "" and the address lives in `user_email`. Matching only the flat `email`
        # made assignment-by-email fail for every established member.
        assert resolve_user_id(self._members(), "alice@example.com") == 42

    def test_resolves_by_user_email_case_insensitive(self):
        assert resolve_user_id(self._members(), "ALICE@EXAMPLE.COM") == 42

    def test_resolves_by_flat_email_when_populated(self):
        # Some payloads do fill `email`; that path must keep working.
        members = [{"user": 9, "email": "dave@example.com", "user_email": None}]
        assert resolve_user_id(members, "dave@example.com") == 9

    def test_resolves_by_full_name(self):
        assert resolve_user_id(self._members(), "Alice Martin") == 42

    def test_resolves_by_full_name_case_insensitive(self):
        assert resolve_user_id(self._members(), "alice martin") == 42

    def test_resolves_by_username_on_non_memberships_payload(self):
        # Forward-compat only — memberships never carry username. See
        # _members_with_extra_info.
        assert resolve_user_id(self._members_with_extra_info(), "CAROL") == 7

    def test_resolves_by_display_name_on_non_memberships_payload(self):
        # Forward-compat only — see above.
        assert resolve_user_id(self._members_with_extra_info(), "carol danvers") == 7

    def test_null_field_member_does_not_crash(self):
        # The regression: the leading null-field member previously raised
        # "'NoneType' object has no attribute 'lower'" before reaching Alice.
        assert resolve_user_id(self._members(), "alice@example.com") == 42

    def test_skips_match_when_member_user_is_none(self):
        # A member matching by email but with user=None must not return None;
        # resolution continues / raises not-found rather than yielding a null ID.
        members = [{"user": None, "email": "ghost@example.com", "full_name": None}]
        with pytest.raises(ValueError, match="not found"):
            resolve_user_id(members, "ghost@example.com")

    def test_raises_when_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            resolve_user_id(self._members(), "bob")

    def test_not_found_error_falls_back_to_user_email_in_listing(self):
        # A member whose only address is user_email must still be named in the
        # diagnostic — it previously rendered "?" because only flat `email` was
        # consulted, and that is blank on real rows.
        with pytest.raises(ValueError, match="alice@example.com"):
            resolve_user_id([{"user": 42, "email": "", "user_email": "alice@example.com"}], "bob")

    def test_not_found_error_prefers_full_name_in_listing(self):
        with pytest.raises(ValueError, match="Alice Martin"):
            resolve_user_id(self._members(), "bob")


class TestGetResource:
    """get_resource fetches a single entity by ID (used for name-based assignment)."""

    def test_fetches_single_resource_by_id(self):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        wrapper.api = MagicMock()
        wrapper.api.auth_token = "tok"
        wrapper.api.get.return_value = {"id": 5, "project": 1}
        result = wrapper.get_resource("user_stories", 5)
        assert result == {"id": 5, "project": 1}
        wrapper.api.get.assert_called_once_with("/userstories/5")

    def test_rejects_unknown_resource_type(self):
        wrapper = TaigaClientWrapper(host="https://taiga.example")
        wrapper.api = MagicMock()
        wrapper.api.auth_token = "tok"
        with pytest.raises(ValueError, match="Unknown resource type"):
            wrapper.get_resource("bogus", 5)
