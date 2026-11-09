import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from backend.core.config import SANDBOX_BASE_URL, load_settings, sqlalchemy_url

REQUIRED = {
    "PAYPAL_BASE_URL": SANDBOX_BASE_URL,
    "APP_ENV": "test",
    "DATABASE_URL": "postgresql://u:p@h/db",
}


@pytest.fixture
def env(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def test_sandbox_passes(env):
    s = load_settings(_env_file=None)
    assert s.paypal_base_url == SANDBOX_BASE_URL
    assert s.app_env == "test"


def test_trailing_slash_is_stripped(env):
    env.setenv("PAYPAL_BASE_URL", SANDBOX_BASE_URL + "/")
    assert load_settings(_env_file=None).paypal_base_url == SANDBOX_BASE_URL


def test_live_paypal_url_refused(env):
    env.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    with pytest.raises(ValidationError, match="sandbox"):
        load_settings(_env_file=None)


@pytest.mark.parametrize("bad", ["preview", "production", "PROD", ""])
def test_unknown_app_env_refused(env, bad):
    env.setenv("APP_ENV", bad)
    with pytest.raises(ValidationError):
        load_settings(_env_file=None)


def test_only_three_settings_required(env):
    s = load_settings(_env_file=None)
    assert s.admin_token is None and s.llm_api_key is None and s.app_base_url is None


def test_unknown_key_in_env_file_is_ignored(env, tmp_path):
    f = tmp_path / ".env"
    f.write_text("NEXT_PUBLIC_PAYPAL_CLIENT_ID=abc\nSOMETHING_ELSE=1\n")
    load_settings(_env_file=str(f))  # must not raise


def test_blank_value_means_unset(env):
    env.setenv("ADMIN_TOKEN", "")
    assert load_settings(_env_file=None).admin_token is None


def test_secrets_hidden_in_repr(env):
    env.setenv("ADMIN_TOKEN", "super-secret-admin")
    env.setenv("LLM_API_KEY", "super-secret-llm")
    s = load_settings(_env_file=None)
    text = repr(s) + str(s)
    assert "super-secret" not in text
    assert "u:p@h" not in text  # DATABASE_URL carries a password
    assert s.admin_token.get_secret_value() == "super-secret-admin"


def test_importing_app_config_with_live_url_fails_to_start():
    env = {**os.environ, **REQUIRED, "PAYPAL_BASE_URL": "https://api-m.paypal.com"}
    r = subprocess.run(
        [sys.executable, "-c", "import backend.core.config"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "sandbox" in r.stderr


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_sqlalchemy_url(given, expected):
    assert sqlalchemy_url(given) == expected


def test_sqlalchemy_url_rejects_other_schemes():
    with pytest.raises(ValueError):
        sqlalchemy_url("mysql://u:p@h/db")
