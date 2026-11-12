"""Koordynator – prowadzi delegację przez kolejne etapy (maszyna stanów) i zleca zadania agentom."""

from sqlalchemy.orm import Session

from ..messaging import Komunikat, Typ
from ..models import Status
from .base import Agent, Kontekst, pobierz_delegacje


class Koordynator(Agent):
    id_agenta = "AG-01"
    nazwa = "koordynator"
    etap = "Koordynacja"
    wersja_modelu = "orch-0.1"

    def obslugi(self):
        return {
            Typ.WNIOSEK_ZLOZONY: (self.rozpocznij, "Rozpoczęcie procesu delegacji"),
            Typ.WYNIK_WALIDACJI: (self.po_walidacji, "Przejście po walidacji wniosku"),
            Typ.WYNIK_ZATWIERDZENIA: (self.po_zatwierdzeniu, "Przejście po decyzji przełożonego"),
        }

    def rozpocznij(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        d.status = Status.W_WALIDACJI
        ktx.wyslij(Typ.ZADANIE_WALIDACJI)

    def po_walidacji(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        if k.dane["poprawny"]:
            d.status = Status.OCZEKUJE
            ktx.wyslij(Typ.ZADANIE_ZATWIERDZENIA)
        else:
            d.status = Status.DO_POPRAWY
            ktx.ostrzezenie(f"Wniosek zwrócony do poprawy – błędy: {len(k.dane['bledy'])}")
            ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": "Popraw wniosek", "bledy": k.dane["bledy"]})

    def po_zatwierdzeniu(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        if k.dane["decyzja"] == "Zgoda":
            d.status = Status.ZATWIERDZONA
            ktx.wyslij(Typ.ZADANIE_TRANSPORTU)
            ktx.wyslij(Typ.ZADANIE_ZAKWATEROWANIA)
        else:
            d.status = Status.ODRZUCONA
        ktx.opis = f"{d.status} – decyzja {k.dane['id_osoby']}"
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": f"Wniosek {d.id_delegacji}: {d.status}"})
