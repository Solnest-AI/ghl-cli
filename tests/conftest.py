"""Every test gets its own settings home.

The CLI now saves a Google pass (workflow-pass.json) in the settings home. Without
this, a test that reaches the token manager could read or write the real
~/.ghl-cli on the machine running the tests. Tests that set GHL_CLI_HOME
themselves still win, because their monkeypatch runs after this one.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_settings_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("GHL_CLI_HOME", str(tmp_path_factory.mktemp("ghl-cli-home")))
