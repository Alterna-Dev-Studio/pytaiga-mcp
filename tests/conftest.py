"""Shared pytest fixtures."""

import os

import pytest


@pytest.fixture(autouse=True)
def _no_pangolin_settings_from_the_shell(monkeypatch):
    """Keep a developer's PANGOLIN_* variables (e.g. from direnv) out of TaigaSettings."""
    for name in list(os.environ):
        if name.startswith("PANGOLIN_"):
            monkeypatch.delenv(name)
