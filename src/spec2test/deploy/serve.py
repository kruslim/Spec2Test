"""Start the hosted app: check the deployment config, write the sign-in provider's settings
where Streamlit reads them, and serve the app on every interface.

Sign-in uses Streamlit's OpenID Connect login (`st.login`), which reads an `[auth]` table
from a secrets file. Hosts hand secrets over as environment variables, so `auth_secrets`
builds that table from the variables in `AUTH_ENV`, and `serve` writes it to a private
temporary folder, never into the repo or the data directory, and runs Streamlit from there
(Streamlit reads `.streamlit/secrets.toml` in its working directory).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from spec2test.deploy.config import CONFIG_ENV, load_config

APP = Path(__file__).with_name("app.py")

AUTH_ENV = {
    "redirect_uri": "SPEC2TEST_AUTH_REDIRECT_URI",
    "cookie_secret": "SPEC2TEST_AUTH_COOKIE_SECRET",
    "client_id": "SPEC2TEST_AUTH_CLIENT_ID",
    "client_secret": "SPEC2TEST_AUTH_CLIENT_SECRET",
    "server_metadata_url": "SPEC2TEST_AUTH_METADATA_URL",
}
"""The `[auth]` keys `st.login` needs, and the environment variable each comes from."""


class AuthConfigError(ValueError):
    """A sign-in setting is missing from the environment."""


def auth_secrets(env: Mapping[str, str]) -> str:
    """The secrets file's text: an `[auth]` table from the variables in `AUTH_ENV`."""
    missing = [var for var in AUTH_ENV.values() if not env.get(var)]
    if missing:
        raise AuthConfigError(f"set {', '.join(missing)} to enable sign-in")
    if len(env[AUTH_ENV["cookie_secret"]]) < 32:
        raise AuthConfigError(f"{AUTH_ENV['cookie_secret']} needs at least 32 characters")
    # A JSON string is a valid TOML basic string.
    lines = [f"{key} = {json.dumps(env[var])}" for key, var in AUTH_ENV.items()]
    return "[auth]\n" + "\n".join(lines) + "\n"


def serve(config_path: Path, *, port: int = 8501, address: str = "0.0.0.0") -> int:
    config = load_config(config_path)
    secrets = auth_secrets(os.environ)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spec2test-auth-") as tmp:
        folder = Path(tmp) / ".streamlit"
        folder.mkdir(mode=0o700)
        path = folder / "secrets.toml"
        path.touch(mode=0o600)
        path.write_text(secrets, encoding="utf-8")
        env = {**os.environ, CONFIG_ENV: str(config_path.resolve())}
        command = [sys.executable, "-m", "streamlit", "run", str(APP)]
        command += ["--browser.gatherUsageStats", "false", "--server.headless", "true"]
        command += ["--server.address", address, "--server.port", str(port)]
        # Partners need no developer menu (rerun, settings, Streamlit's own deploy button).
        command += ["--client.toolbarMode", "viewer"]
        return subprocess.call(command, env=env, cwd=tmp)
