"""Agent Wniosków – walidacja wniosku (S22IT1S0P2-7…-12, -33) i polecenie wyjazdu (S22IT1S0P2-38, w polecenie.py)."""

from dataclasses import asdict

from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..messaging import Komunikat, Typ
from ..przynaleznosc import jednostka_delegacji
from ..stawki import szacuj_koszt
from ..walidacja import parsuj_date, waliduj_wniosek
from .base import Agent, Kontekst, pobierz_delegacje
from .polecenie import sporzadz_polecenie


class AgentWnioskow(Agent):
    id_agenta = "AG-02"
    nazwa = "wnioski"
    etap = "Wniosek"
    wersja_modelu = "walidacja-1.0"

    def obslugi(self):
        return {
            Typ.ZADANIE_WALIDACJI: (self.waliduj, "Walidacja wniosku"),
            Typ.ZADANIE_POLECENIA: (sporzadz_polecenie, "Sporządzenie polecenia wyjazdu"),
        }

    def waliduj(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        w = d.dane_wniosku
        jednostka = jednostka_delegacji(s, d)
        bledy = waliduj_wniosek(w, jednostka=jednostka.id_jednostki if jednostka else None)

        if not bledy:
            d.cel_wyjazdu = w["cel_wyjazdu"].strip()
            d.miejsce_docelowe = w["miejsce_docelowe"].strip()
            d.data_wyjazdu = parsuj_date(w["data_wyjazdu"])
            d.data_powrotu = d.pierwotna_data_powrotu = parsuj_date(w["data_powrotu"])
            d.srodek_transportu = w.get("srodek_transportu")
            # S22IT1S0P2-33: podstawa wyjazdu w polach delegacji (polecenie wyjazdu, wyszukiwanie)
            d.podstawa_numer_rozkazu = str(w.get("podstawa_numer_rozkazu") or "").strip() or None
            data_rozkazu = w.get("podstawa_data_rozkazu")
            d.podstawa_data_rozkazu = parsuj_date(str(data_rozkazu)) if data_rozkazu else None
            # S22IT1S0P2-35: przewidywany koszt – przełożony porównuje go z budżetem jednostki
            d.szacowany_koszt = szacuj_koszt(d.data_wyjazdu, d.data_powrotu, d.potrzeba_zakwaterowania)
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
