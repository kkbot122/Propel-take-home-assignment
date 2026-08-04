from propel.infra.dependencies import async_psycopg_url


def test_managed_postgres_urls_select_the_async_psycopg_driver() -> None:
    assert (
        async_psycopg_url("postgresql://user:password@database.example/propel")
        == "postgresql+psycopg://user:password@database.example/propel"
    )
    assert (
        async_psycopg_url("postgres://user:password@database.example/propel")
        == "postgresql+psycopg://user:password@database.example/propel"
    )
    assert (
        async_psycopg_url("postgresql+psycopg://user:password@database.example/propel")
        == "postgresql+psycopg://user:password@database.example/propel"
    )
