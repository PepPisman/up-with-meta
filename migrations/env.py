from alembic import context

from dota_meta_bot.core.db import get_engine


def run_migrations() -> None:
    with get_engine().connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
