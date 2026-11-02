import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from packages_py.config import Settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = None


def run_migrations_offline():
    context.configure(
        url=os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url")),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = os.getenv("DATABASE_URL", section["sqlalchemy.url"])
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        settings = Settings()
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{settings.database_schema}"')
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=os.getenv("DATABASE_SCHEMA", "app"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
