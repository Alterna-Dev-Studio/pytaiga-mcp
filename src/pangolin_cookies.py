"""Write a Netscape cookie jar that lets REST scripts reach Taiga through Pangolin.

Usage: python -m src.pangolin_cookies --cookie-jar PATH [--host URL]

Pangolin settings come from the environment, as for the MCP server. The jar is written
with mode 0600; nothing is printed on stdout, and errors never include secrets. The jar is
empty (header only) when Pangolin is not configured, when --host is not the configured
Taiga, or when Taiga is not behind Pangolin from this network. Exit status: 0 written,
1 error, 2 Pangolin access-token headers, which a cookie jar cannot carry.
"""

import argparse
import http.cookiejar
import logging
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

HEADER = "# Netscape HTTP Cookie File\n"


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


def _write(path: Path, jar: http.cookiejar.MozillaCookieJar | None) -> None:
    # mkstemp creates the file 0600; replacing PATH keeps that mode even if PATH existed.
    fd, temporary = tempfile.mkstemp(prefix=".cookies-", dir=path.parent)
    try:
        os.close(fd)
        if jar is None:
            Path(temporary).write_text(HEADER)
        else:
            # Pangolin's resource cookie is a session cookie, which save() drops by default.
            jar.save(temporary, ignore_discard=True, ignore_expires=True)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pangolin_cookies", description=__doc__.split("\n")[0])
    parser.add_argument("--cookie-jar", required=True, type=Path)
    parser.add_argument("--host", help="Taiga origin (default: $TAIGA_API_URL)")
    args = parser.parse_args(argv)
    path = args.cookie_jar.expanduser()
    if not path.parent.is_dir():
        print(f"pangolin_cookies: directory {path.parent} does not exist", file=sys.stderr)
        return 1

    # Only warnings and errors, on stderr; the client never logs secret values.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    from src.config import settings
    from src.taiga_client import start_pangolin_session

    host = (settings.host or "").rstrip("/")
    target = (args.host or host).rstrip("/")
    if not host or _origin(target) != _origin(host):
        _write(path, None)
        return 0
    try:
        if settings.get_pangolin_credentials():
            print(
                "pangolin_cookies: Pangolin access-token headers cannot be used from a cookie "
                "jar; store a session with dv taiga-login instead",
                file=sys.stderr,
            )
            return 2
        session = start_pangolin_session(host)
    except ValueError as error:
        # These messages come from the handshake and never contain token values.
        print(f"pangolin_cookies: {error}", file=sys.stderr)
        return 1
    if session is None:
        _write(path, None)
        return 0
    jar = http.cookiejar.MozillaCookieJar()
    for cookie in session.cookies:
        jar.set_cookie(cookie)
    _write(path, jar)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
