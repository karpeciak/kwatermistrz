import os
import tempfile
from pathlib import Path

# Testy używają SQLite i atrapy brokera – nie potrzebują PostgreSQL ani RabbitMQ
_baza = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_baza.as_posix()}"
os.environ["POLACZ_BROKER"] = "false"

import pytest  # noqa: E402

from kwatermistrz import models  # noqa: E402, F401
from kwatermistrz.db import Base, engine, migruj, sesja  # noqa: E402
from kwatermistrz.messaging import Komunikat  # noqa: E402
from kwatermistrz.seed import zaladuj_dane_poczatkowe  # noqa: E402


class AtrapaBrokera:
    def __init__(self) -> None:
        self.wyslane: list[Komunikat] = []
        self.ponowienia: list[tuple[str, Komunikat]] = []

    async def publikuj(self, k: Komunikat) -> None:
        self.wyslane.append(k)

    async def do_kolejki(self, kolejka: str, k: Komunikat) -> None:
        self.ponowienia.append((kolejka, k))

    def typy(self) -> list[str]:
        return [k.typ for k in self.wyslane]

    async def dostarczaj(self, agenci) -> None:
        """Rozsyła wysłane komunikaty do agentów, jak zrobiłby to RabbitMQ, aż kolejka się opróżni."""
        mapa: dict[str, list] = {}
        for agent in agenci:
            for typ, (funkcja, operacja) in agent.obslugi().items():
                mapa.setdefault(typ, []).append((agent, funkcja, operacja))
        while self.wyslane:
            k = self.wyslane.pop(0)
            for agent, funkcja, operacja in mapa.get(k.typ, []):
                await agent.wykonaj(k, funkcja, operacja)


@pytest.fixture(autouse=True)
def baza():
    Base.metadata.drop_all(engine)
    with engine.begin() as polaczenie:
        polaczenie.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    migruj()  # baza testowa powstaje tymi samymi migracjami co w Azure
    with sesja() as s:
        zaladuj_dane_poczatkowe(s)


@pytest.fixture
def broker() -> AtrapaBrokera:
    return AtrapaBrokera()


@pytest.fixture(autouse=True)
def bez_zewnetrznego_api(monkeypatch):
    """Testy nie mogą przez przypadek wysłać zapytania do Photon/OpenStreetMap."""

    def zabronione(*args, **kwargs):
        raise AssertionError("Zewnętrzne API w teście: podmień wyszukiwanie miejscowości lub transport HTTP")

    monkeypatch.setattr("kwatermistrz.miejscowosci.urlopen", zabronione)
