"""Agent Rozliczeń – S22IT1S0P2-36 Wyliczenie należności z delegacji na podstawie dokumentów kosztowych.

Przebieg:
1. Delegowany przekazuje przez API (`api/rozliczenia.py`) faktyczny czas podróży, zapewnione świadczenia
   i dokumenty kosztowe → API zapisuje `Rozliczenie` i `DokumentKosztowy` → `zadanie.rozliczenie` → `wylicz`.
2. `wylicz`:
   - dieta: liczba dób z `faktyczny_wyjazd`/`faktyczny_powrot` × `stawki.DIETA_KRAJOWA`,
     pomniejszona o zapewnione posiłki (`stawki.POTRACENIE_ZA_POSILEK`),
   - zwrot przejazdu: suma dokumentów „bilet”,
   - zwrot noclegu: rachunki do wysokości `stawki.LIMIT_NOCLEGOWY` za dobę; nadwyżka → `przekracza_limit=True`
     i `kwota_uznana` = limit (scenariusz 1a–2a); bez rachunku i bez bezpłatnego zakwaterowania → ryczałt,
   - ryczałty (`stawki.RYCZALT_DOJAZDY`, `stawki.RYCZALT_NOCLEG`),
   - zapisać pola wyliczenia w `Rozliczenie`, decyzję w logu (`ktx.decyzja(Decyzja(typ="Wyliczenie należności"...))`).
3. Delegowany zatwierdza wyliczenie przez API → status `StatusRozliczenia.GOTOWE_DO_WERYFIKACJI`,
   `ktx.wyslij(Typ.WYNIK_ROZLICZENIA)`.

Duplikaty dokumentów (S22IT1S0P2-20) – osobny moduł `dokumenty_kosztowe.py`.
"""

from sqlalchemy.orm import Session

from ..messaging import Komunikat, Typ
from .base import Agent, Kontekst, do_zrobienia, pobierz_delegacje


class AgentRozliczen(Agent):
    id_agenta = "AG-06"
    nazwa = "rozliczenia"
    etap = "Rozliczenie"
    wersja_modelu = "naleznosci-0.1"

    def obslugi(self):
        return {Typ.ZADANIE_ROZLICZENIA: (self.wylicz, "Wyliczenie należności")}

    def wylicz(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        pobierz_delegacje(s, k.id_delegacji)
        do_zrobienia(ktx, "S22IT1S0P2-36")
