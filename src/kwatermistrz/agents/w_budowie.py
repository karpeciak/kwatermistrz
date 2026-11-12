"""Agenci planowani w kolejnych sprintach – na razie przyjmują zadanie i odnotowują je w logu."""

from sqlalchemy.orm import Session

from ..messaging import Komunikat, Typ
from .base import Agent, Kontekst


class AgentWBudowie(Agent):
    sprint: str
    zadania: tuple[str, ...]
    wersja_modelu = "w-budowie"

    def obslugi(self):
        return {typ: (self.przyjmij, f"Przyjęcie zadania {typ}") for typ in self.zadania}

    def przyjmij(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        ktx.ostrzezenie(f"Funkcja planowana w sprincie {self.sprint} – zadanie odnotowane")


class AgentTransportu(AgentWBudowie):
    id_agenta, nazwa, etap, sprint = "AG-04", "transport", "Transport", "2"
    zadania = (Typ.ZADANIE_TRANSPORTU,)


class AgentZakwaterowania(AgentWBudowie):
    id_agenta, nazwa, etap, sprint = "AG-05", "zakwaterowanie", "Zakwaterowanie", "2"
    zadania = (Typ.ZADANIE_ZAKWATEROWANIA,)


class AgentRozliczen(AgentWBudowie):
    id_agenta, nazwa, etap, sprint = "AG-06", "rozliczenia", "Rozliczenie", "4"
    zadania = (Typ.ZADANIE_ROZLICZENIA,)


class AgentZgodnosci(AgentWBudowie):
    id_agenta, nazwa, etap, sprint = "AG-07", "zgodnosc", "Zgodność", "4"
    zadania = (Typ.ZADANIE_KONTROLI,)


class AgentMonitorujacy(AgentWBudowie):
    id_agenta, nazwa, etap, sprint = "AG-08", "monitorujacy", "Monitorowanie", "3"
    zadania = (Typ.ZADANIE_MONITOROWANIA,)
