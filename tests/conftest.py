"""Test defaults, set before any app module is imported.

APP_ENV=test makes settings skip the local .env, so real secrets never reach tests.
CI sets the same values in the workflow env.
"""

import os

os.environ["APP_ENV"] = "test"
os.environ.setdefault("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
