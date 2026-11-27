"""Agent Monitorujący – zmiany w trakcie wyjazdu i replanowanie (sprint 3: S22IT1S0P2-17, -18, -29, -41).

Zrobione:
- S22IT1S0P2-17: odwołanie przejazdu przez przewoźnika i wybór przejazdu alternatywnego
  (`agents/zmiana_przejazdu.py`).
- S22IT1S0P2-41: zmiana terminu zadania – nowy plan transportu i noclegu, potwierdzenie albo wstrzymanie delegacji
  (`agents/zmiana_terminu.py`).
"""

from sqlalchemy.orm import Session

from ..messaging import Komunikat, Typ
from .base import Agent, Kontekst, do_zrobienia
from .zmiana_przejazdu import zapisz_wybor_alternatywy, zarejestruj_odwolanie
from .zmiana_terminu import przygotuj_nowy_plan, zapisz_decyzje_planu


class AgentMonitorujacy(Agent):
    id_agenta = "AG-08"
    nazwa = "monitorujacy"
    etap = "Monitorowanie"
    wersja_modelu = "w-budowie"

    def obslugi(self):
        return {
            Typ.ZADANIE_MONITOROWANIA: (self.monitoruj, "Monitorowanie przebiegu wyjazdu"),
            # S22IT1S0P2-17
            Typ.ODWOLANIE_PRZEJAZDU: (zarejestruj_odwolanie, "Odwołanie przejazdu przez przewoźnika"),
            Typ.WYBOR_PRZEJAZDU_ALTERNATYWNEGO: (zapisz_wybor_alternatywy, "Zmiana przejazdu po odwołaniu"),
            # S22IT1S0P2-41
            Typ.ZMIANA_TERMINU: (przygotuj_nowy_plan, "Nowy plan po zmianie terminu zadania"),
            Typ.DECYZJA_NOWEGO_PLANU: (zapisz_decyzje_planu, "Decyzja delegowanego o nowym planie"),
        }

    def monitoruj(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        do_zrobienia(ktx, "sprintu 3")
