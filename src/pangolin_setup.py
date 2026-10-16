"""Interactive setup for a Pangolin resource access token."""

import argparse
import getpass
import os
import sys
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
    parser.add_argument("--token-id", help="Token ID for non-interactive setup")
    parser.add_argument(
        "--token-stdin", action="store_true", help="Read the token secret from standard input"
    )
    args = parser.parse_args()

    if args.token_stdin != bool(args.token_id):
        parser.error("--token-id and --token-stdin must be used together")
    if not args.token_stdin and not sys.stdin.isatty():
        parser.error(
            "interactive input is unavailable. Run this command in a terminal, "
            "or use --token-id ID --token-stdin and pipe the token secret on stdin"
        )

    print("Sign in to Pangolin and create an access token for the Taiga public resource.")
    print(f"Dashboard: {args.dashboard_url}")
    if args.token_stdin:
        token_id = args.token_id.strip()
        token = sys.stdin.readline().strip()
    else:
        webbrowser.open(args.dashboard_url)
        try:
            token_id = input("Pangolin access token ID: ").strip()
            token = getpass.getpass("Pangolin access token secret: ").strip()
        except (EOFError, KeyboardInterrupt):
            parser.exit(2, "\nSetup cancelled; .env was not changed.\n")
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
