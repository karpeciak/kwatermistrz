"""Agent Transportu – S22IT1S0P2-27 Wybór wariantu podróży na podstawie porównania tras.

Przebieg:
1. `zadanie.transport` (od Koordynatora po zatwierdzeniu) → `przygotuj_warianty`:
   - wybrać z tabeli `Polaczenie` połączenia z miejscowości jednostki do miejsca docelowego,
   - porównać czas, koszt, liczbę przesiadek, punktualność, bezpieczeństwo i zgodność z godziną
     rozpoczęcia przedsięwzięcia (`Przedsiewziecie.godzina_rozpoczecia`),
   - zapisać warianty jako `Przejazd` ze statusem `StatusRezerwacji.PROPOZYCJA`, jeden z `rekomendowany=True`
     i `uzasadnienie`,
   - zapisać decyzję: `ktx.decyzja(Decyzja(typ="Rekomendacja wariantu podróży", rodzaj="Probabilistyczna"
     albo "Regułowa", liczba_wariantow=..., pewnosc=...))`.
2. Aktor wybiera wariant przez API (`api/organizacja.py`) → `wybor.transport` → `zapisz_wybor`:
   - wybrany wariant: status `WYBRANA`, przyczyna wyboru innego niż rekomendowany w `uzasadnienie`,
   - odrzucenie wszystkich (6a): status `DO_UZGODNIENIA`,
   - `ktx.wyslij(Typ.WYNIK_TRANSPORTU)` – Koordynator sprawdzi, czy wyjazd jest zorganizowany.
"""

from sqlalchemy.orm import Session

from ..messaging import Komunikat, Typ
from .base import Agent, Kontekst, do_zrobienia, pobierz_delegacje


class AgentTransportu(Agent):
    id_agenta = "AG-04"
    nazwa = "transport"
    etap = "Transport"
    wersja_modelu = "trasy-0.1"

    def obslugi(self):
        return {
            Typ.ZADANIE_TRANSPORTU: (self.przygotuj_warianty, "Przygotowanie wariantów podróży"),
            Typ.WYBOR_TRANSPORTU: (self.zapisz_wybor, "Zapis wybranego wariantu podróży"),
        }

    def przygotuj_warianty(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        pobierz_delegacje(s, k.id_delegacji)
        do_zrobienia(ktx, "S22IT1S0P2-27")

    def zapisz_wybor(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        pobierz_delegacje(s, k.id_delegacji)
        do_zrobienia(ktx, "S22IT1S0P2-27")
