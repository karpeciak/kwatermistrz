"""Agent Zakwaterowania – S22IT1S0P2-39 Dobór zakwaterowania zapewniającego terminowy dojazd na miejsce zadania.

Przebieg:
1. `zadanie.zakwaterowanie` (od Koordynatora, gdy `Delegacja.potrzeba_zakwaterowania`) → `przygotuj_propozycje`:
   - obiekty z `ObiektZakwaterowania` w miejscowości przedsięwzięcia z wolnymi miejscami
     (`liczba_wolnych_miejsc`), cena ≤ `stawki.LIMIT_NOCLEGOWY`, czas dojazdu (`czas_dojazdu_min`)
     pozwalający zdążyć na `Przedsiewziecie.godzina_rozpoczecia`; obiekty wojskowe mają pierwszeństwo,
   - zapisać propozycje jako `Zakwaterowanie` ze statusem `StatusRezerwacji.PROPOZYCJA` (koszt, czas dojazdu),
   - brak obiektu w limicie z terminowym dojazdem (2a): przedstawić warianty z `wymaga_odstepstwa=True`,
   - decyzja do logu: `ktx.decyzja(Decyzja(typ="Dobór zakwaterowania", ...))`.
2. Delegowany wybiera obiekt przez API (`api/organizacja.py`) → `wybor.zakwaterowanie` → `zapisz_wybor`:
   - wybór w limicie: status `WYBRANA`, rezerwacja i uzasadnienie wyboru, `ktx.wyslij(Typ.WYNIK_ZAKWATEROWANIA)`,
   - wybór wymagający odstępstwa (3a–4a): status `DO_DECYZJI_PRZELOZONEGO`, uzasadnienie delegowanego,
     powiadomienie przełożonego i delegowanego.
"""

from sqlalchemy.orm import Session

from ..messaging import Komunikat, Typ
from .base import Agent, Kontekst, do_zrobienia, pobierz_delegacje


class AgentZakwaterowania(Agent):
    id_agenta = "AG-05"
    nazwa = "zakwaterowanie"
    etap = "Zakwaterowanie"
    wersja_modelu = "nocleg-0.1"

    def obslugi(self):
        return {
            Typ.ZADANIE_ZAKWATEROWANIA: (self.przygotuj_propozycje, "Dobór obiektów zakwaterowania"),
            Typ.WYBOR_ZAKWATEROWANIA: (self.zapisz_wybor, "Zapis wybranego zakwaterowania"),
        }

    def przygotuj_propozycje(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        pobierz_delegacje(s, k.id_delegacji)
        do_zrobienia(ktx, "S22IT1S0P2-39")

    def zapisz_wybor(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        pobierz_delegacje(s, k.id_delegacji)
        do_zrobienia(ktx, "S22IT1S0P2-39")
