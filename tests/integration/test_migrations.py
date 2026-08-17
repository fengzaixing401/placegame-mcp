from alembic import command


def test_migrations_upgrade_and_match_metadata(postgres_url, alembic_config):
    config = alembic_config(database_url=postgres_url)

    command.upgrade(config, "head")
    command.check(config)
