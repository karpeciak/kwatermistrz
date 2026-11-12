"""Agent Monitorujący – zmiany w trakcie wyjazdu i replanowanie (sprint 3: S22IT1S0P2-17, -18, -29, -41)."""

from sqlalchemy.orm import Session

from ..messaging import Komunikat, Typ
from .base import Agent, Kontekst, do_zrobienia


class AgentMonitorujacy(Agent):
    id_agenta = "AG-08"
    nazwa = "monitorujacy"
    etap = "Monitorowanie"
    wersja_modelu = "w-budowie"

    def obslugi(self):
        return {Typ.ZADANIE_MONITOROWANIA: (self.monitoruj, "Monitorowanie przebiegu wyjazdu")}

    def monitoruj(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        do_zrobienia(ktx, "sprintu 3")
