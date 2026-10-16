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
        description="Combined Pangolin Share Link token (ID.token)",
    )

    class Config:
        env_file = str(ENV_FILE_PATH)
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    @property
    def has_credentials(self) -> bool:
        """Check if credentials are available for auto-auth."""
        return bool(self.username and self.password)

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
