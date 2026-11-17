"""Unit tests for pangolin_cookies.py — the cookie jar REST scripts use to pass Pangolin."""

import http.cookiejar
import stat

import pytest
import requests

from src import pangolin_cookies
from src.config import TaigaSettings, settings

HOST = "https://taiga.example"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "host", HOST)
    monkeypatch.setattr(TaigaSettings, "get_pangolin_credentials", lambda self: None)


def _session_with_cookie():
    session = requests.Session()
    session.cookies.set("p_session_token_s.1", "resource-cookie", domain=".taiga.example")
    return session


def _run(tmp_path, monkeypatch, start, *args):
    calls = []

    def fake_start(host):
        calls.append(host)
        return start(host)

    monkeypatch.setattr("src.taiga_client.start_pangolin_session", fake_start)
    jar = tmp_path / "cookies.txt"
    status = pangolin_cookies.main(["--cookie-jar", str(jar), *args])
    return status, jar, calls


def test_session_cookie_is_written_mode_0600(tmp_path, monkeypatch, capsys, configured):
    (tmp_path / "cookies.txt").write_text("old")
    (tmp_path / "cookies.txt").chmod(0o644)
    status, jar, calls = _run(tmp_path, monkeypatch, lambda host: _session_with_cookie())
    assert status == 0 and calls == [HOST]
    assert stat.S_IMODE(jar.stat().st_mode) == 0o600
    loaded = http.cookiejar.MozillaCookieJar()
    loaded.load(str(jar), ignore_discard=True, ignore_expires=True)
    assert [(c.domain, c.name, c.value) for c in loaded] == [
        (".taiga.example", "p_session_token_s.1", "resource-cookie")
    ]
    out, err = capsys.readouterr()
    assert out == "" and "resource-cookie" not in err


@pytest.mark.parametrize("session", [None, requests.Session()], ids=["no-config", "lan"])
def test_empty_jar_without_pangolin_cookies(tmp_path, monkeypatch, configured, session):
    status, jar, _ = _run(tmp_path, monkeypatch, lambda host: session)
    assert status == 0
    lines = jar.read_text().splitlines()
    assert lines[0] == pangolin_cookies.HEADER.strip()
    assert [line for line in lines if line and not line.startswith("#")] == []


def test_other_host_gets_empty_jar_without_handshake(tmp_path, monkeypatch, configured):
    status, jar, calls = _run(
        tmp_path,
        monkeypatch,
        lambda host: _session_with_cookie(),
        "--host",
        "https://other.example",
    )
    assert status == 0 and calls == []
    assert jar.read_text() == pangolin_cookies.HEADER


def test_handshake_failure_exits_1_and_writes_nothing(tmp_path, monkeypatch, capsys, configured):
    def fail(host):
        raise ValueError(
            "Pangolin session expired or has no access to this resource; sign in again with dv taiga-login"
        )

    status, jar, _ = _run(tmp_path, monkeypatch, fail)
    assert status == 1 and not jar.exists()
    assert "sign in again with dv taiga-login" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_access_token_headers_exit_2(tmp_path, monkeypatch, configured):
    monkeypatch.setattr(TaigaSettings, "get_pangolin_credentials", lambda self: ("id", "secret"))
    status, jar, calls = _run(tmp_path, monkeypatch, lambda host: _session_with_cookie())
    assert status == 2 and calls == [] and not jar.exists()


def test_missing_directory_exits_1(tmp_path, capsys):
    status = pangolin_cookies.main(["--cookie-jar", str(tmp_path / "nope/cookies.txt")])
    assert status == 1
    assert "does not exist" in capsys.readouterr().err
