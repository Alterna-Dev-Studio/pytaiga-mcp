# taiga_client.py
import html
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from pytaigaclient import TaigaClient
from pytaigaclient.exceptions import TaigaAuthenticationError, TaigaException

from src.config import settings

logger = logging.getLogger(__name__)

# Endpoint mapping for all listable resources
_RESOURCE_ENDPOINTS = {
    "projects": "/projects",
    "user_stories": "/userstories",
    "tasks": "/tasks",
    "issues": "/issues",
    "epics": "/epics",
    "milestones": "/milestones",
    "wiki": "/wiki",
    "memberships": "/memberships",
    "userstory_statuses": "/userstory-statuses",
    "task_statuses": "/task-statuses",
    "issue_statuses": "/issue-statuses",
    "epic_statuses": "/epic-statuses",
    "issue_types": "/issue-types",
    "priorities": "/priorities",
    "severities": "/severities",
    "points": "/points",
    "swimlanes": "/swimlanes",
}

_NO_PAGINATION_HEADERS = {"x-disable-pagination": "True"}


class _PangolinSession(requests.Session):
    """Keep resource credentials on the Taiga origin during redirects."""

    def rebuild_auth(
        self, prepared_request: requests.PreparedRequest, response: requests.Response
    ) -> None:
        if self.should_strip_auth(response.request.url, prepared_request.url):
            prepared_request.headers.pop("P-Access-Token-Id", None)
            prepared_request.headers.pop("P-Access-Token", None)
        super().rebuild_auth(prepared_request, response)


def _start_pangolin_share_session(
    session: _PangolinSession,
    dashboard: str,
    secret: str,
    taiga_host: str,
    request_param: str,
) -> None:
    """Exchange a dashboard Share Link for a cookie on the Taiga resource."""
    if not request_param or not request_param.isidentifier():
        raise ValueError("Invalid Pangolin session request parameter")
    try:
        with requests.Session() as dashboard_session:
            response = dashboard_session.post(
                f"{dashboard}/api/v1/auth/access-token",
                json={"accessToken": secret},
                headers={"X-CSRF-Token": "x-csrf-protection"},
                allow_redirects=False,
                timeout=10,
            )
        if not response.ok:
            raise ValueError("Pangolin rejected the Share Link")
        data = response.json().get("data", {})
        redirect_url = data.get("redirectUrl")
        request_token = data.get("session")
        if not isinstance(redirect_url, str) or not isinstance(request_token, str):
            raise ValueError("Pangolin returned an invalid Share Link response")
        resource = urlsplit(redirect_url)
        if not _same_origin(resource, taiga_host):
            raise ValueError("Pangolin Share Link points to a different Taiga resource")
        query = parse_qsl(resource.query, keep_blank_values=True)
        query.append((request_param, request_token))
        _exchange_pangolin_request_url(
            session, urlunsplit(resource._replace(query=urlencode(query)))
        )
    except requests.RequestException:
        raise ValueError("Pangolin Share Link authentication request failed") from None


def _same_origin(url: SplitResult, origin: str) -> bool:
    expected = urlsplit(origin)
    return (
        url.scheme == expected.scheme
        and url.netloc == expected.netloc
        and not url.username
        and not url.password
    )


def _exchange_pangolin_request_url(session: _PangolinSession, exchange_url: str) -> None:
    """Visit a Taiga URL carrying a one-shot request token so Badger sets its cookie."""
    exchange = session.get(exchange_url, allow_redirects=False, timeout=10)
    if exchange.status_code not in _REDIRECT_CODES or not session.cookies:
        raise ValueError("Pangolin did not establish a Taiga resource session")


_REDIRECT_CODES = (301, 302, 303, 307, 308)
_BROWSER_HEADERS = {"Accept": "text/html,application/xhtml+xml", "User-Agent": "Mozilla/5.0"}


def _start_pangolin_user_session(
    session: _PangolinSession,
    dashboard: str,
    token: str,
    cookie_name: str,
    taiga_host: str,
) -> None:
    """Turn a Pangolin user session into a resource session on the Taiga origin."""
    try:
        with requests.Session() as probe:
            response = probe.get(
                f"{taiga_host}/", headers=_BROWSER_HEADERS, allow_redirects=False, timeout=10
            )
        if response.status_code not in _REDIRECT_CODES:
            if response.ok:
                logger.info("Taiga is not behind Pangolin from this network; skipping")
                return
            raise ValueError("Pangolin did not redirect to the configured dashboard")
        portal = urlsplit(response.headers.get("Location", ""))
        if not _same_origin(portal, dashboard) or not portal.path.startswith("/auth/resource/"):
            raise ValueError("Pangolin did not redirect to the configured dashboard")
        with requests.Session() as portal_session:
            portal_session.cookies.set(cookie_name, token, domain=portal.hostname)
            response = portal_session.get(
                urlunsplit(portal._replace(query=urlencode({"redirect": f"{taiga_host}/"}))),
                headers=_BROWSER_HEADERS,
                allow_redirects=False,
                timeout=10,
            )
        if response.status_code in _REDIRECT_CODES:
            location = response.headers.get("Location", "")
        elif response.ok:
            # The Next.js portal streams its page, so the redirect usually arrives as a
            # NEXT_REDIRECT marker inside a 200 body rather than as an HTTP 307.
            location = _next_redirect_target(response.text)
            if not location:
                raise ValueError(
                    "Pangolin session expired or has no access to this resource; "
                    "run pangolin-login again"
                )
        else:
            raise ValueError("Pangolin resource authentication failed")
        exchange = urlsplit(location)
        if not _same_origin(exchange, taiga_host) or len(parse_qsl(exchange.query)) != 1:
            raise ValueError("Pangolin did not return a Taiga resource session request")
        _exchange_pangolin_request_url(session, urlunsplit(exchange))
    except requests.RequestException:
        raise ValueError("Pangolin resource authentication request failed") from None


_NEXT_REDIRECT = re.compile(r"NEXT_REDIRECT;(?:push|replace);(https://[^;\"'\\\s<]+);")


def _next_redirect_target(body: str) -> str:
    """Return the URL from a streamed Next.js redirect marker, or an empty string."""
    match = _NEXT_REDIRECT.search(body)
    return html.unescape(match.group(1)).replace("\\u0026", "&") if match else ""


def safe_lower(value: Any) -> str:
    """Lowercase a value, tolerating None.

    Taiga returns JSON ``null`` for unset string fields (e.g. a membership's
    ``email``/``full_name`` on a pending invitation, or a resource ``name``).
    A bare ``value.lower()`` on those raised ``'NoneType' object has no
    attribute 'lower'`` (pytaiga-mcp#120). Coercing ``None`` -> ``""`` first
    keeps name/username matching robust across both server flavors.
    """
    return (value or "").lower()


def resolve_user_id(members: List[Dict[str, Any]], identifier: "int | str") -> int:
    """Resolve a user identifier to a Taiga user ID against a project's memberships.

    ``identifier`` may be an ``int`` (returned unchanged — treated as a user ID)
    or a string matched case-insensitively against each member's ``user_email``,
    ``email``, ``full_name``, or — on non-memberships payloads only — the
    ``user_extra_info`` username / display name. Matching is None-safe (see
    ``safe_lower``): a single member with a null field no longer aborts resolution
    before the intended user is reached.

    Membership rows carry NO ``user_extra_info`` and no ``username`` field, so
    those lookups are kept only for forward-compatibility with endpoints that do
    return them — they never match on a memberships payload. ``user_email`` is
    the reliable address: the flat ``email`` field is frequently blank on
    established members, which is why email resolution needs both.

    Raises ``ValueError`` if the identifier is empty or not found.
    """
    if isinstance(identifier, bool):  # bool is an int subclass — reject to avoid surprises
        raise ValueError("User identifier must be an int user ID or a name/email string.")
    if isinstance(identifier, int):
        return identifier
    needle = safe_lower(identifier)
    if not needle:
        raise ValueError("Empty username/identifier for user resolution.")
    for m in members:
        info = m.get("user_extra_info") or {}
        if needle in (
            safe_lower(info.get("username")),
            safe_lower(m.get("user_email")),
            safe_lower(m.get("email")),
            safe_lower(m.get("full_name")),
            safe_lower(info.get("full_name_display")),
        ):
            uid = m.get("user")
            if uid is not None:
                return uid
    available = [
        m.get("full_name") or m.get("user_email") or m.get("email") or "?" for m in members
    ]
    raise ValueError(f"User '{identifier}' not found in project. Members: {available}")


class _CompatTaigaClient(TaigaClient):
    """TaigaClient with a compatibility shim for pytaigaclient's `query_params` bug.

    Four Tasks methods — `list`, `get_by_ref`, `filters_data`, `list_attachments` —
    call ``self.client.get(endpoint, query_params=...)``, but ``TaigaClient.get``
    only accepts ``params=`` and forwards unknown kwargs into ``_request()``, which
    raises ``unexpected keyword argument 'query_params'``. Every other resource
    correctly uses ``params=``, so the defect is isolated to the Tasks resource
    (see issue #87, regression of #79). We can't patch the dependency — it's pinned
    to an upstream commit of talhaorak/pyTaigaClient — so we remap the stray kwarg
    here, at the one chokepoint every resource routes through. This is a no-op for
    correctly-written callers (no ``query_params`` kwarg → nothing to remap).
    """

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        if "query_params" in kwargs:
            query_params = kwargs.pop("query_params")
            if params is None:
                params = query_params
            elif query_params:
                # Explicit params win on key conflict; never reached from the
                # library (its buggy calls only pass query_params), but defensive.
                params = {**query_params, **params}
        return super().get(path, params=params, **kwargs)


class TaigaClientWrapper:
    """
    A wrapper around the pytaiga-client library to manage API instance
    and authentication state.
    """

    def __init__(self, host: str):
        if not host:
            raise ValueError("Taiga host URL cannot be empty.")
        # Store host, but initialize client later during login/token auth
        self.host = host
        # Use the new client type
        self.api: Optional[TaigaClient] = None
        logger.info(f"TaigaClientWrapper initialized for host: {self.host}")

    def _build_client(self) -> "_CompatTaigaClient":
        """Create an unauthenticated client, going through Pangolin first when configured."""
        pangolin_session = settings.get_pangolin_session()
        pangolin_share_link = settings.get_pangolin_share_link()
        pangolin_credentials = settings.get_pangolin_credentials()
        if not (pangolin_session or pangolin_share_link or pangolin_credentials):
            return _CompatTaigaClient(host=self.host)
        # pytaigaclient logs complete request headers at DEBUG level.
        logging.getLogger("pytaigaclient.client").setLevel(logging.INFO)
        session = _PangolinSession()
        if pangolin_session:
            _start_pangolin_user_session(session, *pangolin_session, self.host)
        elif pangolin_share_link:
            _start_pangolin_share_session(
                session,
                *pangolin_share_link,
                self.host,
                # Launchers may export an empty value for an unset variable.
                settings.pangolin_session_request_param or "resource_session_request_param",
            )
        else:
            session.headers.update(
                {
                    "P-Access-Token-Id": pangolin_credentials[0],
                    "P-Access-Token": pangolin_credentials[1],
                }
            )
        return _CompatTaigaClient(host=self.host, session=session)

    def login(self, username: str, password: str) -> bool:
        """
        Authenticates with the Taiga instance using username and password.
        Uses pytaigaclient.
        """
        try:
            # SECURITY: Don't log username to avoid credential exposure
            logger.info(f"Attempting login on {self.host}")
            api_instance = self._build_client()
            # Use the auth resource's login method
            api_instance.auth.login(username=username, password=password)
            self.api = api_instance
            logger.info("Login successful. Auth token acquired.")
            return True
        except TaigaException as e:
            # SECURITY: Don't log username in error messages
            logger.error(f"Taiga login failed: {e}", exc_info=False)
            self.api = None
            raise e
        except Exception as e:
            # SECURITY: Don't log username in error messages
            logger.error(f"An unexpected error occurred during login: {e}", exc_info=True)
            self.api = None
            # Wrap unexpected errors in TaigaException if needed, or re-raise
            raise TaigaException(f"Unexpected login error: {e}")

    def login_with_token(self, token: str, token_type: str = "Application") -> bool:
        """Authenticate with a Taiga token (an Application token from taiga-login, or a JWT)."""
        try:
            logger.info(f"Authenticating with a {token_type} token on {self.host}")
            api_instance = self._build_client()
            api_instance.update_token(token, token_type)
            try:
                api_instance.get("/users/me")
            except TaigaAuthenticationError:
                raise TaigaException(
                    "Taiga rejected the configured token; run taiga-login again"
                ) from None
            self.api = api_instance
            logger.info("Token authentication successful.")
            return True
        except TaigaException as e:
            logger.error(f"Taiga token authentication failed: {e}", exc_info=False)
            self.api = None
            raise e
        except Exception as e:
            logger.error(f"An unexpected error occurred during token login: {e}", exc_info=True)
            self.api = None
            raise TaigaException(f"Unexpected login error: {e}")

    @property
    def is_authenticated(self) -> bool:
        """Checks if the client is currently authenticated (has an API instance with a token)."""
        # Check if api exists and has a token
        return self.api is not None and self.api.auth_token is not None

    def _ensure_authenticated(self):
        """Internal helper to check authentication before API calls."""
        if not self.is_authenticated:
            logger.error("Action required authentication, but client is not logged in.")
            raise PermissionError("Client not authenticated. Please login first.")

    def get_resource(self, resource_type: str, resource_id: int) -> Dict[str, Any]:
        """Fetch a single resource by ID via the raw API.

        Used by name-based assignee resolution to look up an entity's
        ``project`` before matching a username against that project's members.
        """
        self._ensure_authenticated()
        endpoint = _RESOURCE_ENDPOINTS.get(resource_type)
        if endpoint is None:
            raise ValueError(
                f"Unknown resource type: {resource_type}. Valid: {sorted(_RESOURCE_ENDPOINTS)}"
            )
        return self.api.get(f"{endpoint}/{resource_id}")

    def list_resources(
        self, resource_type: str, project_id: Optional[int] = None, **filters
    ) -> List[Dict[str, Any]]:
        """
        Unified interface for listing resources via raw API with pagination disabled.

        Uses the x-disable-pagination header to bypass Taiga's default PAGE_SIZE=30
        limit, ensuring all results are returned in a single request.

        Args:
            resource_type: The type of resource (e.g., 'user_stories', 'tasks', 'issues')
            project_id: The project ID to filter by (required for most resources)
            **filters: Additional filters to apply

        Returns:
            List of resource dictionaries
        """
        self._ensure_authenticated()
        endpoint = _RESOURCE_ENDPOINTS.get(resource_type)
        if endpoint is None:
            raise ValueError(
                f"Unknown resource type: {resource_type}. Valid: {sorted(_RESOURCE_ENDPOINTS)}"
            )
        params = {}
        if project_id is not None:
            params["project"] = project_id
        params.update(filters)
        # Workaround for upstream taiga-back typo (TETRA-2023/pytaiga-mcp#68):
        # SwimlanesFilter declares param_name="swimnlane" (extra 'n'), so the
        # back-end ignores ?swimlane= and only honours ?swimnlane=. Sending
        # both keys keeps us correct before and after upstream fixes the typo,
        # since the dispatcher uses whichever it recognises and ignores the
        # other. Scoped to user_stories list — only that endpoint has the bug.
        # Upstream ref:
        # https://github.com/taigaio/taiga-back/blob/df14a4bdaee662962e343e3c4cd3fcd6a1339de7/taiga/projects/userstories/filters.py#L31-L34
        # exclude_swimlane is unaffected (no typo there) and intentionally not translated.
        if resource_type == "user_stories" and "swimlane" in params and "swimnlane" not in params:
            params["swimnlane"] = params["swimlane"]
        result = self.api.get(endpoint, params=params, headers=_NO_PAGINATION_HEADERS)
        return result if isinstance(result, list) else []
