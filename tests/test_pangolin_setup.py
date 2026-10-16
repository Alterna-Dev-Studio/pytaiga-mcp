"""The setup helper must preserve existing config and keep tokens private."""

import io
import os
import sys
from unittest.mock import patch

import pytest
from dotenv import dotenv_values

from src import pangolin_setup


def test_setup_saves_token_pair_without_printing_secret(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("TAIGA_API_URL=https://taiga.example\n")
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with patch.object(pangolin_setup.webbrowser, "open") as open_browser:
        with patch("builtins.input", return_value="token-id"):
            with patch.object(pangolin_setup.getpass, "getpass", return_value="token-secret"):
                pangolin_setup.main()

    assert dotenv_values(env_file) == {
        "TAIGA_API_URL": "https://taiga.example",
        "PANGOLIN_ACCESS_TOKEN_ID": "token-id",
        "PANGOLIN_ACCESS_TOKEN": "token-secret",
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


def test_noninteractive_setup_reads_secret_from_stdin(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(
        sys, "argv", ["mcp-taiga-pangolin-setup", "--token-id", "id", "--token-stdin"]
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("secret\n"))
    with patch.object(pangolin_setup.webbrowser, "open") as open_browser:
        pangolin_setup.main()
    assert dotenv_values(env_file)["PANGOLIN_ACCESS_TOKEN"] == "secret"
    assert os.stat(env_file).st_mode & 0o777 == 0o600
    open_browser.assert_not_called()


def test_interactive_eof_exits_without_traceback_or_write(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with patch.object(pangolin_setup.webbrowser, "open"):
        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(SystemExit) as error:
                pangolin_setup.main()
    assert error.value.code == 2
    assert "Setup cancelled" in capsys.readouterr().err
    assert not env_file.exists()
