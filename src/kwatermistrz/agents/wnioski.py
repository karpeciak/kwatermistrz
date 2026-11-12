"""Agent Wniosków – walidacja kompletności i spójności wniosku (S22IT1S0P2-7…-12)."""

from dataclasses import asdict

from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..messaging import Komunikat, Typ
from ..models import Zolnierz
from ..walidacja import parsuj_date, waliduj_wniosek
from .base import Agent, Kontekst, pobierz_delegacje


class AgentWnioskow(Agent):
    id_agenta = "AG-02"
    nazwa = "wnioski"
    etap = "Wniosek"
    wersja_modelu = "walidacja-1.0"

    def obslugi(self):
        return {Typ.ZADANIE_WALIDACJI: (self.waliduj, "Walidacja wniosku")}

    def waliduj(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        zolnierz = s.get(Zolnierz, d.id_zolnierza)
        w = d.dane_wniosku
        bledy = waliduj_wniosek(w, jednostka=zolnierz.id_jednostki if zolnierz else None)

        if not bledy:
            d.cel_wyjazdu = w["cel_wyjazdu"].strip()
            d.miejsce_docelowe = w["miejsce_docelowe"].strip()
            d.data_wyjazdu = parsuj_date(w["data_wyjazdu"])
            d.data_powrotu = d.pierwotna_data_powrotu = parsuj_date(w["data_powrotu"])
            d.srodek_transportu = w.get("srodek_transportu")
        else:
            ktx.ostrzezenie("; ".join(b.komunikat for b in bledy))

        ktx.decyzja(
            Decyzja(
                typ="Walidacja wniosku",
                wybrany_wariant="Poprawny" if not bledy else "Do poprawy",
                uzasadnienie="Wszystkie wymagane pola poprawne"
                if not bledy
                else "; ".join(f"{b.historyjka}: {b.komunikat}" for b in bledy),
            )
        )
        ktx.wyslij(Typ.WYNIK_WALIDACJI, {"poprawny": not bledy, "bledy": [asdict(b) for b in bledy]})
