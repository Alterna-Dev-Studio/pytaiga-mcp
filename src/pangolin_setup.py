"""Interactive setup for a Pangolin resource access token."""

import argparse
import getpass
import os
import sys
import webbrowser

from dotenv import dotenv_values, set_key, unset_key

from src.config import (
    ENV_FILE_PATH,
    parse_pangolin_share_link,
    settings,
    split_pangolin_resource_token,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Save Pangolin resource credentials in .env")
    parser.add_argument(
        "--dashboard-url",
        default="https://connect.alternadevstudio.com",
        help="Pangolin dashboard URL to open for sign-in",
    )
    parser.add_argument("--token-id", help="Token ID when stdin contains only the secret")
    parser.add_argument(
        "--token-stdin", action="store_true", help="Read the token secret from standard input"
    )
    args = parser.parse_args()

    if args.token_id and not args.token_stdin:
        parser.error("--token-id requires --token-stdin")
    if not args.token_stdin and not sys.stdin.isatty():
        parser.error(
            "interactive input is unavailable. Run this command in a terminal, "
            "or use --token-stdin and pipe the Share Link URL on stdin"
        )

    print("Sign in to Pangolin, select View as Admin, then Settings > Share Links.")
    print("Search the resource picker for the Taiga hostname, then create a Share Link.")
    print("Copy the dashboard /s/ Share Link URL or its ID.token value.")
    print(f"Dashboard: {args.dashboard_url}")
    if args.token_stdin:
        value = sys.stdin.readline().strip()
        combined = f"{args.token_id}.{value}" if args.token_id else value
    else:
        webbrowser.open(args.dashboard_url)
        try:
            combined = getpass.getpass("Pangolin Share Link URL or ID.token: ").strip()
        except (EOFError, KeyboardInterrupt):
            parser.exit(2, "\nSetup cancelled; .env was not changed.\n")
    try:
        if parse_pangolin_share_link(combined):
            saved_value = combined
        else:
            token_id, token = split_pangolin_resource_token(combined, settings.host)
            saved_value = f"{token_id}.{token}"
    except ValueError as error:
        parser.error(f"{error}; .env was not changed")

    ENV_FILE_PATH.touch(mode=0o600, exist_ok=True)
    os.chmod(ENV_FILE_PATH, 0o600)
    set_key(str(ENV_FILE_PATH), "PANGOLIN_RESOURCE_TOKEN", saved_value)
    existing = dotenv_values(ENV_FILE_PATH)
    for key in ("PANGOLIN_ACCESS_TOKEN_ID", "PANGOLIN_ACCESS_TOKEN"):
        if key in existing:
            unset_key(str(ENV_FILE_PATH), key)
    os.chmod(ENV_FILE_PATH, 0o600)
    print("Saved Pangolin credentials to .env. Restart the MCP server to use them.")


if __name__ == "__main__":
    main()
