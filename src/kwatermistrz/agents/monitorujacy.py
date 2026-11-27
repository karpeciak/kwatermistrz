"""Agent Monitorujący – zmiany w trakcie wyjazdu i replanowanie (sprint 3: S22IT1S0P2-17, -18, -29, -41).

Zrobione:
- S22IT1S0P2-17: odwołanie przejazdu przez przewoźnika i wybór przejazdu alternatywnego
  (`agents/zmiana_przejazdu.py`).
- S22IT1S0P2-41: zmiana terminu zadania – nowy plan transportu i noclegu, potwierdzenie albo wstrzymanie delegacji
  (`agents/zmiana_terminu.py`).
- S22IT1S0P2-42: kontrola czasu realizacji zlecenia transportu, ponowienie, oczekiwanie albo wycofanie
  (`agents/opoznienia.py`).
- S22IT1S0P2-34: zmiana usługi w trakcie podróży zgłoszona przez dostawcę – rozwiązanie zastępcze, akceptacja
  albo przekazanie osobie dyżurnej (`agents/zmiana_w_podrozy.py`).
"""

from sqlalchemy.orm import Session

from ..db import sesja, teraz
from ..messaging import Komunikat, Typ
from .base import Agent, Kontekst, do_zrobienia
from .opoznienia import KONTROLA_ZLECEN, kontroluj_zlecenie, zapisz_decyzje_oczekiwania, zlecenia_do_kontroli
from .zmiana_przejazdu import zapisz_wybor_alternatywy, zarejestruj_odwolanie
from .zmiana_terminu import przygotuj_nowy_plan, zapisz_decyzje_planu
from .zmiana_w_podrozy import (
    KONTROLA_ODPOWIEDZI,
    przekaz_po_terminie,
    przygotuj_rozwiazanie,
    zapisz_odpowiedz,
    zmiany_po_terminie,
)


class AgentMonitorujacy(Agent):
    id_agenta = "AG-08"
    nazwa = "monitorujacy"
    etap = "Monitorowanie"
    wersja_modelu = "w-budowie"
    interwal_cyklu = 60  # S22IT1S0P2-42: kontrola czasu realizacji zleceń co minutę

    def obslugi(self):
        return {
            Typ.ZADANIE_MONITOROWANIA: (self.monitoruj, "Monitorowanie przebiegu wyjazdu"),
            # S22IT1S0P2-17
            Typ.ODWOLANIE_PRZEJAZDU: (zarejestruj_odwolanie, "Odwołanie przejazdu przez przewoźnika"),
            Typ.WYBOR_PRZEJAZDU_ALTERNATYWNEGO: (zapisz_wybor_alternatywy, "Zmiana przejazdu po odwołaniu"),
            # S22IT1S0P2-41
            Typ.ZMIANA_TERMINU: (przygotuj_nowy_plan, "Nowy plan po zmianie terminu zadania"),
            Typ.DECYZJA_NOWEGO_PLANU: (zapisz_decyzje_planu, "Decyzja delegowanego o nowym planie"),
            # S22IT1S0P2-42
            Typ.DECYZJA_OCZEKIWANIA: (zapisz_decyzje_oczekiwania, "Decyzja o oczekiwaniu na realizację zlecenia"),
            # S22IT1S0P2-34
            Typ.ZMIANA_USLUGI: (przygotuj_rozwiazanie, "Zmiana usługi w trakcie podróży"),
            Typ.DECYZJA_ZMIANY_USLUGI: (zapisz_odpowiedz, "Odpowiedź na rozwiązanie zastępcze"),
        }

    def zadania_cykliczne(self):
        """S22IT1S0P2-42: zlecenia transportu, które przekroczyły czas realizacji albo właśnie się zakończyły.

        S22IT1S0P2-34: rozwiązania zastępcze bez odpowiedzi delegowanego w wyznaczonym terminie.
        """
        with sesja() as s:
            chwila = teraz()
            ids = zlecenia_do_kontroli(s, chwila)
            po_terminie = zmiany_po_terminie(s, chwila)
        return [
            (Komunikat(typ=KONTROLA_ZLECEN, id_delegacji=i), kontroluj_zlecenie, "Kontrola czasu realizacji zlecenia")
            for i in ids
        ] + [
            (
                Komunikat(typ=KONTROLA_ODPOWIEDZI, id_delegacji=i),
                przekaz_po_terminie,
                "Brak odpowiedzi na rozwiązanie zastępcze",
            )
            for i in po_terminie
        ]

    def monitoruj(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        do_zrobienia(ktx, "sprintu 3")
