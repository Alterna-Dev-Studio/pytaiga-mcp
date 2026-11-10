"""Configuration management with secure credential handling."""

from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings

# Get the project root directory (parent of src/)
PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"


def split_pangolin_resource_token(
    value: str, expected_host: Optional[str] = None
) -> tuple[str, str]:
    """Parse a Share Link URL or ID.token without accepting AI Gateway keys."""
    if value.startswith(("https://", "http://")):
        link = urlsplit(value)
        if expected_host and link.hostname != urlsplit(expected_host).hostname:
            raise ValueError("Pangolin Share Link hostname must match TAIGA_API_URL")
        tokens = parse_qs(link.query, keep_blank_values=True).get("p_token", [])
        if len(tokens) != 1:
            raise ValueError("Pangolin Share Link URL must contain one p_token parameter")
        value = tokens[0]
    if value.startswith("pangolin-key-"):
        raise ValueError(
            "Pangolin AI Gateway keys cannot authorize a Taiga HTTP resource; "
            "create a Share Link token for the Taiga resource"
        )
    parts = value.split(".")
    if len(parts) != 2 or not all(parts):
        raise ValueError("Pangolin Share Link token must have the form ID.token")
    return parts[0], parts[1]


def parse_pangolin_share_link(value: str) -> Optional[tuple[str, str]]:
    """Return the dashboard origin and secret from Pangolin's /s/ link."""
    link = urlsplit(value)
    if not link.path.startswith("/s/"):
        return None
    secret = link.path.removeprefix("/s/")
    if (
        link.scheme != "https"
        or not link.hostname
        or link.username
        or link.password
        or not secret
        or "/" in secret
        or link.query
        or link.fragment
    ):
        raise ValueError("Pangolin Share Link must be an HTTPS /s/<token> URL")
    return f"{link.scheme}://{link.netloc}", secret


# Load .env file from project root
load_dotenv(dotenv_path=ENV_FILE_PATH)


class TaigaSettings(BaseSettings):
    """Taiga MCP server settings with secure credential handling."""

    # Connection settings
    host: str = Field(
        default="http://localhost:9000",
        alias="TAIGA_API_URL",
        description="Taiga API base URL",
    )

    # Credentials (using SecretStr for security)
    username: Optional[SecretStr] = Field(
        default=None,
        alias="TAIGA_USERNAME",
        description="Taiga username for auto-authentication",
    )
    password: Optional[SecretStr] = Field(
        default=None,
        alias="TAIGA_PASSWORD",
        description="Taiga password for auto-authentication",
    )
    pangolin_access_token_id: Optional[SecretStr] = Field(
        default=None,
        alias="PANGOLIN_ACCESS_TOKEN_ID",
        description="Pangolin resource access token ID",
    )
    pangolin_access_token: Optional[SecretStr] = Field(
        default=None,
        alias="PANGOLIN_ACCESS_TOKEN",
        description="Pangolin resource access token secret",
    )
    pangolin_resource_token: Optional[SecretStr] = Field(
        default=None,
        alias="PANGOLIN_RESOURCE_TOKEN",
        description="Pangolin /s/ Share Link URL or combined token (ID.token)",
    )
    pangolin_session_request_param: str = Field(
        default="resource_session_request_param",
        alias="PANGOLIN_SESSION_REQUEST_PARAM",
        description="Pangolin's resource session request query parameter",
    )
    pangolin_dashboard_url: Optional[str] = Field(
        default=None,
        alias="PANGOLIN_DASHBOARD_URL",
        description="Pangolin dashboard origin that issued the user session token",
    )
    pangolin_session_token: Optional[SecretStr] = Field(
        default=None,
        alias="PANGOLIN_SESSION_TOKEN",
        description="Pangolin user session token from a device code login",
    )
    pangolin_session_cookie: str = Field(
        default="p_session_token",
        alias="PANGOLIN_SESSION_COOKIE",
        description="Pangolin's user session cookie name",
    )

    class Config:
        env_file = str(ENV_FILE_PATH)
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    auth_token: Optional[SecretStr] = Field(
        default=None,
        alias="TAIGA_AUTH_TOKEN",
        description="Taiga token used instead of a password (from taiga-login)",
    )
    token_type: str = Field(
        default="Application",
        alias="TAIGA_TOKEN_TYPE",
        description="Authorization scheme for TAIGA_AUTH_TOKEN: Application or Bearer",
    )
    oidc_issuer: Optional[str] = Field(
        default=None,
        alias="TAIGA_OIDC_ISSUER",
        description="OIDC issuer that Taiga trusts, used by taiga-login's device code flow",
    )
    oidc_client_id: Optional[str] = Field(
        default=None,
        alias="TAIGA_OIDC_CLIENT_ID",
        description="Public (native) OIDC client id for taiga-login",
    )
    application_id: Optional[str] = Field(
        default=None,
        alias="TAIGA_APPLICATION_ID",
        description="Taiga external application id whose tokens taiga-login mints",
    )

    @property
    def has_credentials(self) -> bool:
        """Check if credentials are available for auto-auth."""
        return bool(self.get_auth_token() or (self.username and self.password))

    def get_auth_token(self) -> Optional[tuple[str, str]]:
        """Return (token, type) when a Taiga token is configured; empty values count as unset."""
        token = self.auth_token.get_secret_value() if self.auth_token else ""
        if not token:
            return None
        token_type = (self.token_type or "Application").strip()
        if token_type.lower() not in ("application", "bearer"):
            raise ValueError("TAIGA_TOKEN_TYPE must be Application or Bearer")
        return token, token_type.capitalize()

    def get_oidc_login_settings(self) -> tuple[str, str, str]:
        """Return (issuer, client id, application id) for taiga-login, or raise naming the gap."""
        values = {
            "TAIGA_OIDC_ISSUER": (self.oidc_issuer or "").strip().rstrip("/"),
            "TAIGA_OIDC_CLIENT_ID": (self.oidc_client_id or "").strip(),
            "TAIGA_APPLICATION_ID": (self.application_id or "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError("taiga-login needs " + ", ".join(missing))
        if not values["TAIGA_OIDC_ISSUER"].startswith("https://"):
            raise ValueError("TAIGA_OIDC_ISSUER must be an https:// origin")
        return tuple(values.values())  # type: ignore[return-value]

    def get_username_value(self) -> Optional[str]:
        """Safely get username value."""
        return self.username.get_secret_value() if self.username else None

    def get_password_value(self) -> Optional[str]:
        """Safely get password value."""
        return self.password.get_secret_value() if self.password else None

    def get_pangolin_credentials(self) -> Optional[tuple[str, str]]:
        """Return the resource token pair, rejecting incomplete configuration."""
        if self.pangolin_resource_token:
            combined = self.pangolin_resource_token.get_secret_value()
            if parse_pangolin_share_link(combined):
                return None
            return split_pangolin_resource_token(combined, self.host)
        token_id = (
            self.pangolin_access_token_id.get_secret_value()
            if self.pangolin_access_token_id
            else None
        )
        token = (
            self.pangolin_access_token.get_secret_value() if self.pangolin_access_token else None
        )
        if token and not token_id and ("." in token or token.startswith("pangolin-key-")):
            return split_pangolin_resource_token(token, self.host)
        if bool(token_id) != bool(token):
            raise ValueError("Both Pangolin access token variables are required")
        return (token_id, token) if token_id and token else None

    def get_pangolin_share_link(self) -> Optional[tuple[str, str]]:
        if not self.pangolin_resource_token:
            return None
        return parse_pangolin_share_link(self.pangolin_resource_token.get_secret_value())

    def get_pangolin_session(self) -> Optional[tuple[str, str, str]]:
        """Return (dashboard origin, user session token, cookie name) if configured."""
        token = (
            self.pangolin_session_token.get_secret_value() if self.pangolin_session_token else ""
        )
        if not token:
            return None
        if not self.pangolin_dashboard_url:
            raise ValueError("PANGOLIN_DASHBOARD_URL is required with PANGOLIN_SESSION_TOKEN")
        dashboard = urlsplit(self.pangolin_dashboard_url)
        if (
            dashboard.scheme != "https"
            or not dashboard.hostname
            or dashboard.username
            or dashboard.password
            or dashboard.path not in ("", "/")
            or dashboard.query
            or dashboard.fragment
        ):
            raise ValueError("PANGOLIN_DASHBOARD_URL must be an HTTPS origin")
        if token.split() != [token] or "." in token or "://" in token:
            raise ValueError(
                "PANGOLIN_SESSION_TOKEN must be a Pangolin user session token from pangolin-login"
            )
        # Launchers may export an empty value for an unset variable; keep the default then.
        cookie = self.pangolin_session_cookie or "p_session_token"
        if not cookie.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Invalid Pangolin session cookie name")
        return f"https://{dashboard.netloc}", token, cookie


def mask_credential(value: str, visible_chars: int = 2) -> str:
    """Mask a credential for safe logging.

    Args:
        value: The credential to mask
        visible_chars: Number of characters to show at start/end

    Returns:
        Masked string like "us****rd"
    """
    if not value:
        return "<empty>"
    if len(value) <= visible_chars * 2:
        return "*" * len(value)
    return (
        f"{value[:visible_chars]}{'*' * (len(value) - visible_chars * 2)}{value[-visible_chars:]}"
    )


# Global settings instance
settings = TaigaSettings()
