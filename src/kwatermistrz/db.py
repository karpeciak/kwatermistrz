"""Połączenie z bazą danych i obsługa sesji SQLAlchemy."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

if engine.dialect.name == "sqlite":
    # SQLite domyślnie nie sprawdza kluczy obcych – włączamy, żeby testy zachowywały się jak PostgreSQL
    @event.listens_for(engine, "connect")
    def _wlacz_klucze_obce(polaczenie, _rekord) -> None:
        polaczenie.execute("PRAGMA foreign_keys=ON")


def teraz() -> datetime:
    """Bieżący czas UTC bez strefy (tak przechowujemy znaczniki czasu w bazie)."""
    return datetime.now(UTC).replace(tzinfo=None)


@contextmanager
def sesja() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


WERSJA_BAZOWA = "0001"  # schemat sprzed wprowadzenia migracji (utworzony przez create_all)


def migruj(silnik: Engine | None = None) -> None:
    """Doprowadza schemat bazy do najnowszej wersji (Alembic). Uruchamiane przy starcie API."""
    from alembic import command
    from alembic.config import Config

    silnik = silnik or engine
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migracje"))
    with silnik.connect() as polaczenie:
        if silnik.dialect.name == "sqlite":
            # przebudowa tabel w SQLite (batch) wymaga wyłączonych kluczy obcych na czas migracji
            polaczenie.exec_driver_sql("PRAGMA foreign_keys=OFF")
        cfg.attributes["polaczenie"] = polaczenie
        tabele = inspect(polaczenie).get_table_names()
        if "delegacja" in tabele and "alembic_version" not in tabele:
            command.stamp(cfg, WERSJA_BAZOWA)  # baza istniała przed migracjami – oznaczamy jej wersję
        command.upgrade(cfg, "head")
        polaczenie.commit()
