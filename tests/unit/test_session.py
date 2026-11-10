from backend.db.session import connect_args, make_engine


def test_prepared_statements_off_and_timeout():
    args = connect_args("postgresql+psycopg://u:p@ep-x.us-east-1.aws.neon.tech/db")
    assert args["prepare_threshold"] is None
    assert args["connect_timeout"] == 5


def test_tls_required_for_remote_hosts():
    assert connect_args("postgresql+psycopg://u:p@ep-x.neon.tech/db")["sslmode"] == "require"


def test_url_sslmode_is_respected():
    args = connect_args("postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=verify-full")
    assert "sslmode" not in args  # the URL's own setting wins


def test_local_postgres_has_no_tls():
    assert "sslmode" not in connect_args("postgresql+psycopg://u:p@localhost:5432/db")


def test_engine_uses_psycopg3_without_connecting():
    engine = make_engine("postgres://u:p@localhost:5432/db")
    assert engine.dialect.driver == "psycopg"
    assert engine.pool.size() == 5
