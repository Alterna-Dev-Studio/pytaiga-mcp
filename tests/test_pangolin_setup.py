"""The setup helper must preserve existing config and keep tokens private."""

import os
import sys
from unittest.mock import patch

from dotenv import dotenv_values

from src import pangolin_setup


def test_setup_saves_token_pair_without_printing_secret(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("TAIGA_API_URL=https://taiga.example\n")
    monkeypatch.setattr(pangolin_setup, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(sys, "argv", ["mcp-taiga-pangolin-setup"])
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
