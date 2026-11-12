"""Środowisko Alembic – adres bazy i metadane modeli bierzemy z aplikacji."""

from alembic import context

from kwatermistrz import models  # noqa: F401 – rejestruje tabele
from kwatermistrz.db import Base, engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=str(engine.url), target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    polaczenie = context.config.attributes.get("polaczenie")
    if polaczenie is not None:
        _migruj(polaczenie)
        return
    with engine.connect() as polaczenie:
        _migruj(polaczenie)


def _migruj(polaczenie) -> None:
    # render_as_batch – SQLite (testy) nie obsługuje większości ALTER TABLE; PostgreSQL wykonuje zwykłe ALTER
    context.configure(connection=polaczenie, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
