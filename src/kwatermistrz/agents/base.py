"""Klasa bazowa agenta: odbiór komunikatów, transakcja, log operacji i decyzji, ponowienia."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy.orm import Session

from ..audit import Decyzja, zapisz_decyzje, zapisz_operacje
from ..config import settings
from ..db import sesja
from ..messaging import Broker, Komunikat
from ..models import Delegacja

log = logging.getLogger(__name__)


class BladProcesu(Exception):
    """Błąd biznesowy – ponowienie nic nie zmieni (np. brak przełożonego w strukturze)."""


@dataclass
class Kontekst:
    """Stan jednej operacji agenta; po zakończeniu trafia do LogOperacji i LogDecyzji."""

    komunikat: Komunikat
    operacja: str
    wynik: str = "Sukces"
    opis: str = ""
    decyzje: list[Decyzja] = field(default_factory=list)
    do_wyslania: list[Komunikat] = field(default_factory=list)

    def ostrzezenie(self, opis: str) -> None:
        self.wynik = "Ostrzeżenie"
        self.opis = opis

    def decyzja(self, d: Decyzja) -> None:
        self.decyzje.append(d)

    def wyslij(self, typ: str, dane: dict | None = None) -> None:
        """Komunikaty są wysyłane dopiero po zatwierdzeniu transakcji."""
        k = self.komunikat
        self.do_wyslania.append(
            Komunikat(typ=typ, id_delegacji=k.id_delegacji, dane=dane or {}, id_korelacji=k.id_korelacji)
        )


Obsluga = Callable[[Session, Komunikat, Kontekst], None]


def pobierz_delegacje(s: Session, id_delegacji: str | None) -> Delegacja:
    d = s.get(Delegacja, id_delegacji) if id_delegacji else None
    if d is None:
        raise BladProcesu(f"Nie znaleziono delegacji {id_delegacji}")
    return d


def do_zrobienia(ktx: Kontekst, historyjka: str) -> None:
    """Zaślepka zadania, które jest dopiero implementowane – przyjmuje je i odnotowuje w logu operacji."""
    ktx.ostrzezenie(f"Zadanie przyjęte – implementacja w ramach {historyjka}")


class Agent(ABC):
    id_agenta: str
    nazwa: str
    etap: str
    wersja_modelu: str = "rules-1.0"
    interwal_cyklu: float | None = None  # sekundy; None = agent bez zadań cyklicznych

    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    @property
    def kolejka(self) -> str:
        return f"kwatermistrz.{self.nazwa}"

    @abstractmethod
    def obslugi(self) -> dict[str, tuple[Obsluga, str]]:
        """Typ komunikatu → (metoda obsługi, nazwa operacji do logu)."""

    def zadania_cykliczne(self) -> list[tuple[Komunikat, Obsluga, str]]:
        return []

    async def na_komunikat(self, message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            k = Komunikat.z_json(message.body)
            obsluga = self.obslugi().get(k.typ)
            if obsluga is None:
                log.warning("%s: brak obsługi komunikatu %s", self.nazwa, k.typ)
                return
            await self.wykonaj(k, *obsluga)

    async def wykonaj(self, k: Komunikat, funkcja: Obsluga, operacja: str) -> Kontekst:
        ktx = Kontekst(k, operacja)
        ponowienie: Komunikat | None = None
        start = time.perf_counter()
        try:
            with sesja() as s:
                funkcja(s, k, ktx)
        except BladProcesu as e:
            ktx.wynik, ktx.opis = "Błąd", str(e)
            ktx.decyzje.clear()
            ktx.do_wyslania.clear()
        except Exception as e:
            log.exception("%s: błąd przy obsłudze %s", self.nazwa, k.typ)
            ktx.wynik, ktx.opis = "Błąd", f"{type(e).__name__}: {e}"
            ktx.decyzje.clear()
            ktx.do_wyslania.clear()
            if k.proba < settings.max_prob:
                ponowienie = replace(k, proba=k.proba + 1)
                ktx.opis += f" – ponowienie (próba {ponowienie.proba})"
        czas_ms = int((time.perf_counter() - start) * 1000)

        with sesja() as s:
            op = zapisz_operacje(
                s,
                id_korelacji=k.id_korelacji,
                id_delegacji=k.id_delegacji,
                id_agenta=self.id_agenta,
                etap=self.etap,
                operacja=ktx.operacja,
                wynik=ktx.wynik,
                czas_trwania_ms=czas_ms,
                nr_proby=k.proba,
                komunikat=ktx.opis or None,
                id_komunikatu=k.id,
            )
            for d in ktx.decyzje:
                zapisz_decyzje(s, op, d, self.wersja_modelu)

        for wyjscie in ktx.do_wyslania:
            await self.broker.publikuj(wyjscie)
        if ponowienie:
            await self.broker.do_kolejki(self.kolejka, ponowienie)
        return ktx

    async def petla_cykliczna(self) -> None:
        while True:
            await asyncio.sleep(self.interwal_cyklu or 60)
            try:
                for k, funkcja, operacja in self.zadania_cykliczne():
                    await self.wykonaj(k, funkcja, operacja)
            except Exception:
                log.exception("%s: błąd zadania cyklicznego", self.nazwa)
