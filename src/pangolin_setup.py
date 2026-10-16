"""Interactive setup for a Pangolin resource access token."""

import argparse
import getpass
import os
import webbrowser

from dotenv import set_key

from src.config import ENV_FILE_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Save Pangolin resource credentials in .env")
    parser.add_argument(
        "--dashboard-url",
        default="https://connect.alternadevstudio.com",
        help="Pangolin dashboard URL to open for sign-in",
    )
    args = parser.parse_args()

    print("Sign in to Pangolin and create an access token for the Taiga public resource.")
    print(f"Dashboard: {args.dashboard_url}")
    webbrowser.open(args.dashboard_url)
    token_id = input("Pangolin access token ID: ").strip()
    token = getpass.getpass("Pangolin access token secret: ").strip()
    if not token_id or not token:
        parser.error("both token ID and secret are required; .env was not changed")

    ENV_FILE_PATH.touch(mode=0o600, exist_ok=True)
    os.chmod(ENV_FILE_PATH, 0o600)
    set_key(str(ENV_FILE_PATH), "PANGOLIN_ACCESS_TOKEN_ID", token_id)
    set_key(str(ENV_FILE_PATH), "PANGOLIN_ACCESS_TOKEN", token)
    os.chmod(ENV_FILE_PATH, 0o600)
    print("Saved Pangolin credentials to .env. Restart the MCP server to use them.")


if __name__ == "__main__":
    main()
