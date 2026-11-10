"""Sign in to Taiga with your identity-provider account and print a Taiga Application token.

Flow: OAuth 2.0 device authorization against the OIDC issuer Taiga trusts -> the access
token is posted to Taiga's ``/auth`` endpoint (``type: openid``, handled by
taiga-contrib-openid-auth) -> that Taiga session authorizes the configured external
application and the resulting long-lived Application token is printed on stdout.
"""

import argparse
import logging
import secrets
import sys
import time
import webbrowser
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests
from pytaigaclient.exceptions import TaigaBadRequestError, TaigaException

from src.config import settings
from src.taiga_client import TaigaClientWrapper

logger = logging.getLogger(__name__)

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SCOPE = "openid email profile"


class LoginError(Exception):
    """A failure the user can act on; the message never contains a token."""


def _json(response: requests.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        raise LoginError(f"{response.url.rsplit('/', 1)[-1]}: unexpected response") from None
    return data if isinstance(data, dict) else {}


def start_device_authorization(
    issuer: str, client_id: str, http: requests.Session
) -> Dict[str, Any]:
    response = http.post(
        f"{issuer}/oauth/v2/device_authorization",
        data={"client_id": client_id, "scope": SCOPE},
        timeout=15,
    )
    data = _json(response)
    if not response.ok or not all(
        isinstance(data.get(key), str) for key in ("device_code", "user_code")
    ):
        raise LoginError(
            f"identity provider refused the device authorization ({response.status_code}): "
            f"{data.get('error_description') or data.get('error') or 'check TAIGA_OIDC_CLIENT_ID'}"
        )
    return data


def poll_device_token(
    issuer: str,
    client_id: str,
    device: Dict[str, Any],
    http: requests.Session,
    sleep=time.sleep,
    clock=time.monotonic,
) -> str:
    interval = float(device.get("interval") or 5)
    deadline = clock() + float(device.get("expires_in") or 300)
    while True:
        response = http.post(
            f"{issuer}/oauth/v2/token",
            data={
                "grant_type": DEVICE_GRANT,
                "device_code": device["device_code"],
                "client_id": client_id,
            },
            timeout=15,
        )
        data = _json(response)
        if response.ok and isinstance(data.get("access_token"), str):
            return data["access_token"]
        error = data.get("error", "")
        if error == "slow_down":
            interval += 5
        elif error == "authorization_pending":
            pass
        elif error == "expired_token":
            raise LoginError(
                "the device code expired before it was confirmed; run taiga-login again"
            )
        elif error == "access_denied":
            raise LoginError("the sign-in was declined in the browser")
        else:
            raise LoginError(
                f"identity provider error: {data.get('error_description') or error or response.status_code}"
            )
        if clock() >= deadline:
            raise LoginError(
                "the device code expired before it was confirmed; run taiga-login again"
            )
        sleep(interval)


def taiga_login_with_access_token(api, access_token: str, host: str) -> None:
    """Exchange an identity-provider access token for a Taiga session on ``api``."""
    try:
        data = api.post(
            "/auth", json={"type": "openid", "access_token": access_token, "url": f"{host}/login"}
        )
    except TaigaBadRequestError as e:
        detail = str(getattr(e, "error_detail", "") or e)
        if "invalid login type" in detail:
            raise LoginError("this Taiga server has no OpenID login plugin enabled") from None
        raise LoginError(
            "Taiga could not match your identity-provider account to a Taiga user; "
            "sign in to Taiga once in the browser with the same account, then retry"
        ) from None
    token = (data or {}).get("auth_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise LoginError("Taiga did not return a session for the OpenID login")
    api.update_token(token, "Bearer")


def mint_application_token(api, application_id: str) -> str:
    """Authorize the external application for the signed-in user and return its token."""
    state = secrets.token_urlsafe(16)
    authorized = api.post(
        "/application-tokens/authorize", json={"application": application_id, "state": state}
    )
    auth_code = (authorized or {}).get("auth_code") if isinstance(authorized, dict) else None
    if not isinstance(auth_code, str) or (authorized or {}).get("state") != state:
        raise LoginError("Taiga did not authorize the application; check TAIGA_APPLICATION_ID")
    validated = api.post(
        "/application-tokens/validate",
        json={"application": application_id, "auth_code": auth_code, "state": state},
    )
    token = (validated or {}).get("token") if isinstance(validated, dict) else None
    if not isinstance(token, str) or not token:
        raise LoginError("Taiga did not issue an application token")
    return token


def revoke_application_tokens(api, application_id: str) -> int:
    """Delete this user's tokens for the application; returns how many were removed."""
    tokens = api.get("/application-tokens", params={"application": application_id}) or []
    removed = 0
    for item in tokens:
        if isinstance(item, dict) and item.get("id") is not None:
            api.delete(f"/application-tokens/{item['id']}")
            removed += 1
    return removed


def run(args: argparse.Namespace, http: Optional[requests.Session] = None) -> str:
    issuer, client_id, application_id = settings.get_oidc_login_settings()
    http = http or requests.Session()
    device = start_device_authorization(issuer, client_id, http)
    confirm_url = device.get("verification_uri_complete") or (
        f"{device.get('verification_uri', issuer)}?{urlencode({'user_code': device['user_code']})}"
    )
    print(f"Device code: {device['user_code']}", file=sys.stderr)
    print(
        f"Sign in with your own account and confirm this code at:\n  {confirm_url}", file=sys.stderr
    )
    if not args.no_browser:
        webbrowser.open(confirm_url)
    access_token = poll_device_token(issuer, client_id, device, http)

    wrapper = TaigaClientWrapper(host=settings.host)
    api = wrapper._build_client()
    taiga_login_with_access_token(api, access_token, settings.host)
    if args.revoke:
        removed = revoke_application_tokens(api, application_id)
        print(f"Revoked {removed} application token(s).", file=sys.stderr)
    return mint_application_token(api, application_id)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-browser", action="store_true", help="Do not open the confirm page")
    parser.add_argument(
        "--revoke", action="store_true", help="Revoke existing tokens before issuing a new one"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        token = run(args)
    except (LoginError, ValueError) as error:
        print(f"taiga-login: {error}", file=sys.stderr)
        return 1
    except TaigaException as error:
        print(f"taiga-login: Taiga request failed: {error}", file=sys.stderr)
        return 1
    except requests.RequestException:
        print("taiga-login: could not reach the identity provider", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("taiga-login: cancelled", file=sys.stderr)
        return 1
    if sys.stdout.isatty():
        print("Pipe this command into taiga-configure to avoid showing the token.", file=sys.stderr)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
