"""The setup helper must preserve existing config and keep tokens private."""

import io
import os
import sys
from unittest.mock import patch

import pytest
from dotenv import dotenv_values

from src import pangolin_setup


def test_setup_saves_combined_token_without_printing_secret(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TAIGA_API_URL=https://taiga.example\n"
        "PANGOLIN_ACCESS_TOKEN_ID=old-id\nPANGOLIN_ACCESS_TOKEN=old-secret\n"
    )
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with patch.object(pangolin_setup.webbrowser, "open") as open_browser:
        with patch.object(pangolin_setup.getpass, "getpass", return_value="token-id.token-secret"):
            pangolin_setup.main()

    assert dotenv_values(env_file) == {
        "TAIGA_API_URL": "https://taiga.example",
        "PANGOLIN_RESOURCE_TOKEN": "token-id.token-secret",
    }
    assert os.stat(env_file).st_mode & 0o777 == 0o600
    assert "token-secret" not in capsys.readouterr().out
    open_browser.assert_called_once_with("https://connect.alternadevstudio.com")


def test_noninteractive_setup_explains_input_requirement(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup"])
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    with patch.object(pangolin_setup.webbrowser, "open") as open_browser:
        with pytest.raises(SystemExit) as error:
            pangolin_setup.main()
    assert error.value.code == 2
    assert "interactive input is unavailable" in capsys.readouterr().err
    assert not env_file.exists()
    open_browser.assert_not_called()


def test_noninteractive_setup_reads_combined_token_from_stdin(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup", "--token-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("id.secret\n"))
    with patch.object(pangolin_setup.webbrowser, "open") as open_browser:
        pangolin_setup.main()
    assert dotenv_values(env_file)["PANGOLIN_RESOURCE_TOKEN"] == "id.secret"
    assert os.stat(env_file).st_mode & 0o777 == 0o600
    open_browser.assert_not_called()


def test_setup_extracts_token_from_share_link(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(pangolin_setup.settings, "host", "https://taiga.example")
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup", "--token-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("https://taiga.example/?p_token=id.secret\n"))
    pangolin_setup.main()
    assert dotenv_values(env_file)["PANGOLIN_RESOURCE_TOKEN"] == "id.secret"


def test_setup_saves_dashboard_share_link(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(pangolin_setup.settings, "host", "https://taiga.example")
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup", "--token-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("https://connect.example/s/share-secret\n"))
    pangolin_setup.main()
    assert (
        dotenv_values(env_file)["PANGOLIN_RESOURCE_TOKEN"]
        == "https://connect.example/s/share-secret"
    )
    assert "share-secret" not in capsys.readouterr().out


def test_setup_rejects_link_for_different_resource(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(pangolin_setup.settings, "host", "https://taiga.example")
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup", "--token-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("https://different.example/?p_token=id.secret\n"))
    with pytest.raises(SystemExit) as error:
        pangolin_setup.main()
    assert error.value.code == 2
    assert "hostname must match" in capsys.readouterr().err
    assert not env_file.exists()


def test_interactive_eof_exits_without_traceback_or_write(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with patch.object(pangolin_setup.webbrowser, "open"):
        with patch.object(pangolin_setup.getpass, "getpass", side_effect=EOFError):
            with pytest.raises(SystemExit) as error:
                pangolin_setup.main()
    assert error.value.code == 2
    assert "Setup cancelled" in capsys.readouterr().err
    assert not env_file.exists()


def test_setup_rejects_ai_gateway_key(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup", "--token-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("pangolin-key-id.secret\n"))
    with pytest.raises(SystemExit) as error:
        pangolin_setup.main()
    assert error.value.code == 2
    assert "AI Gateway keys" in capsys.readouterr().err
    assert not env_file.exists()
