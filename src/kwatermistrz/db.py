"""Połączenie z bazą danych i obsługa sesji SQLAlchemy."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine, event
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


def utworz_schemat() -> None:
    from . import models  # noqa: F401 – rejestruje tabele w metadanych

    Base.metadata.create_all(engine)
