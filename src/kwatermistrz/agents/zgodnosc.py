"""Agent Zgodności – S22IT1S0P2-43 Weryfikacja wyliczenia należności i przekazanie rozliczenia do komórki finansowej.

Przebieg:
1. Delegowany zleca przekazanie zatwierdzonego wyliczenia (API `api/rozliczenia.py`) → `zadanie.weryfikacja`
   → `weryfikuj`:
   - sprawdzić kompletność wyliczenia i zgodność ze stawkami i limitami (`stawki.py`) oraz dokumentami,
   - każdą niezgodność skorygować i zapisać jako `KorektaRozliczenia` (pozycja, przed, po, powód),
   - status `StatusRozliczenia.ZWERYFIKOWANE`, decyzja w logu (`ktx.decyzja(Decyzja(typ="Weryfikacja
     wyliczenia", ...))`), `ktx.wyslij(Typ.WYNIK_WERYFIKACJI)`.
2. Delegowany akceptuje albo zgłasza zastrzeżenia (API) → `akceptacja.rozliczenia` → `przekaz`:
   - akceptacja: status `PRZEKAZANE`, `data_przekazania`,
   - zastrzeżenia (3a–4a): `zastrzezenia` + status `PRZEKAZANE_Z_ZASTRZEZENIAMI`,
   - w obu przypadkach `ktx.wyslij(Typ.WYNIK_PRZEKAZANIA)` (Koordynator ustawi delegacji status „rozliczony”)
     i powiadomienie delegowanego.
"""

from sqlalchemy.orm import Session

from ..messaging import Komunikat, Typ
from .base import Agent, Kontekst, do_zrobienia, pobierz_delegacje


class AgentZgodnosci(Agent):
    id_agenta = "AG-07"
    nazwa = "zgodnosc"
    etap = "Zgodność"
    wersja_modelu = "weryfikacja-0.1"

    def obslugi(self):
        return {
            Typ.ZADANIE_WERYFIKACJI: (self.weryfikuj, "Weryfikacja wyliczenia należności"),
            Typ.AKCEPTACJA_ROZLICZENIA: (self.przekaz, "Przekazanie rozliczenia do komórki finansowej"),
        }

    def weryfikuj(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        pobierz_delegacje(s, k.id_delegacji)
        do_zrobienia(ktx, "S22IT1S0P2-43")

    def przekaz(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        pobierz_delegacje(s, k.id_delegacji)
        do_zrobienia(ktx, "S22IT1S0P2-43")
