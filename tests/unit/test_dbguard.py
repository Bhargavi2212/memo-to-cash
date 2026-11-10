import pytest

from tests.dbguard import UnsafeTestDatabaseError, pick_test_db_url

DEV_POOLED = "postgresql://u:p@ep-dev-123-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require"
DEV_DIRECT = "postgresql://u:p@ep-dev-123.us-east-1.aws.neon.tech/neondb?sslmode=require"
TEST_DIRECT = "postgresql://u:p@ep-test-456.us-east-1.aws.neon.tech/neondb?sslmode=require"
DOTENV = {"DATABASE_URL": DEV_POOLED, "DATABASE_URL_UNPOOLED": DEV_DIRECT}


def test_none_when_unset():
    assert pick_test_db_url({}, DOTENV) is None


def test_shell_env_wins_over_dotenv():
    assert pick_test_db_url({"TEST_DATABASE_URL": TEST_DIRECT}, {**DOTENV}) == TEST_DIRECT


def test_read_from_dotenv():
    assert pick_test_db_url({}, {**DOTENV, "TEST_DATABASE_URL": TEST_DIRECT}) == TEST_DIRECT


@pytest.mark.parametrize("url", [DEV_DIRECT, DEV_POOLED])
def test_refuses_dev_database(url):
    with pytest.raises(UnsafeTestDatabaseError):
        pick_test_db_url({"TEST_DATABASE_URL": url}, DOTENV)


def test_ci_without_dotenv_is_allowed():
    ci = "postgresql://ci:ci@localhost:5432/ci"
    assert pick_test_db_url({"TEST_DATABASE_URL": ci}, {}) == ci
