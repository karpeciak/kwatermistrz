"""Agent Zgodności – S22IT1S0P2-43 Weryfikacja wyliczenia należności i przekazanie rozliczenia do komórki finansowej.

Przebieg:
1. Delegowany zleca przekazanie zatwierdzonego wyliczenia (`POST /delegacje/{id}/rozliczenie/przekazanie`)
   → `zadanie.weryfikacja` → `weryfikuj`:
   - kompletność: faktyczny czas podróży i wszystkie składniki wyliczenia,
   - zgodność ze stawkami, limitami i dokumentami: niezależne przeliczenie regułami z `agents/rozliczenia.py`
     (`wylicz_naleznosci`, stawki z `stawki.py`) i porównanie każdej pozycji,
   - każda niezgodność poprawiona i zapisana jako `KorektaRozliczenia` (pozycja, przed, po, powód); zmiana
     kwoty należności koryguje wydatek w budżecie jednostki (S22IT1S0P2-66),
   - status „zweryfikowane”, decyzja w logu (korekta = decyzja finansowa do sprawdzenia), `wynik.weryfikacja`
     i powiadomienie delegowanego.
2. Delegowany akceptuje albo zgłasza zastrzeżenia (`POST /delegacje/{id}/rozliczenie/akceptacja`)
   → `akceptacja.rozliczenia` → `przekaz`:
   - akceptacja: status „przekazane do komórki finansowej”, `data_przekazania`,
   - zastrzeżenia (3a–4a): `zastrzezenia` + status „przekazane z zastrzeżeniami”,
   - w obu przypadkach `wynik.przekazanie` (Koordynator ustawia delegacji status „rozliczony”)
     i powiadomienie delegowanego.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..db import teraz
from ..messaging import Komunikat, Typ
from ..models import (
    Delegacja,
    DokumentKosztowy,
    KorektaRozliczenia,
    Rozliczenie,
    StatusRozliczenia,
)
from ..przynaleznosc import jednostka_delegacji
from .base import Agent, Kontekst, pobierz_delegacje
from .rozliczenia import wylicz_naleznosci

TYP_DECYZJI = "Weryfikacja wyliczenia"
POZYCJE = {
    "dieta": "dieta",
    "zwrot_przejazdu": "zwrot przejazdu",
    "zwrot_noclegu": "zwrot noclegu",
    "ryczalty": "ryczałty",
    "kwota_naleznosci": "kwota należności",
}


def rozliczenie_delegacji(s: Session, d: Delegacja) -> Rozliczenie | None:
    return s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == d.id_delegacji))


def braki_wyliczenia(r: Rozliczenie) -> list[str]:
    braki = []
    if r.faktyczny_wyjazd is None or r.faktyczny_powrot is None:
        braki.append("faktyczny czas podróży")
    braki += [nazwa for pole, nazwa in POZYCJE.items() if getattr(r, pole) is None]
    return braki


def skoryguj_budzet(s: Session, d: Delegacja, roznica: Decimal) -> None:
    """Zatwierdzona należność jest już wydatkiem jednostki – korekta zmienia go o różnicę."""
    jednostka = jednostka_delegacji(s, d)
    if jednostka is not None:
        jednostka.budzet_wydatkowany = (jednostka.budzet_wydatkowany or Decimal("0")) + roznica


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
        d = pobierz_delegacje(s, k.id_delegacji)
        r = rozliczenie_delegacji(s, d)
        if r is None or r.status != StatusRozliczenia.GOTOWE_DO_WERYFIKACJI:
            stan = r.status if r else "brak rozliczenia"
            ktx.ostrzezenie(f"Rozliczenie nie czeka na weryfikację ({stan}) – weryfikacja pominięta")
            return
        braki = braki_wyliczenia(r)
        if braki:
            opis = "Wyliczenie niekompletne – brak: " + ", ".join(braki)
            ktx.ostrzezenie(opis)
            ktx.decyzja(Decyzja(typ=TYP_DECYZJI, wybrany_wariant="Wstrzymano", uzasadnienie=opis))
            ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": f"Rozliczenie {d.id_delegacji}: {opis}"})
            return

        dokumenty = s.scalars(
            select(DokumentKosztowy)
            .where(DokumentKosztowy.id_rozliczenia == r.id_rozliczenia)
            .order_by(DokumentKosztowy.data, DokumentKosztowy.id_dokumentu)
        ).all()
        poprawne = wylicz_naleznosci(
            r.faktyczny_wyjazd,
            r.faktyczny_powrot,
            r.zapewnione_posilki or {},
            r.bezplatne_zakwaterowanie,
            [(dok.rodzaj, dok.kwota) for dok in dokumenty],
        )

        kwota_przed = r.kwota_naleznosci
        korekty = []
        for pole, nazwa in POZYCJE.items():
            przed, po = getattr(r, pole), getattr(poprawne, pole)
            if przed != po:
                powod = f"{nazwa}: {przed:.2f} zł niezgodne ze stawkami i dokumentami – powinno być {po:.2f} zł"
                s.add(
                    KorektaRozliczenia(
                        id_rozliczenia=r.id_rozliczenia, pozycja=nazwa, wartosc_przed=przed, wartosc_po=po, powod=powod
                    )
                )
                korekty.append(powod)
                setattr(r, pole, po)
        for dok, uznana in zip(dokumenty, poprawne.kwoty_uznane, strict=True):
            dok.kwota_uznana = uznana
            dok.przekracza_limit = uznana < dok.kwota

        if r.kwota_naleznosci != kwota_przed:
            skoryguj_budzet(s, d, r.kwota_naleznosci - kwota_przed)

        r.status = StatusRozliczenia.ZWERYFIKOWANE
        kwota = f"{r.kwota_naleznosci:.2f} zł"
        if korekty:
            ktx.ostrzezenie(f"Zweryfikowano z korektami ({len(korekty)}), należność {kwota}")
        else:
            ktx.opis = f"Wyliczenie zgodne ze stawkami i dokumentami, należność {kwota}"
        ktx.decyzja(
            Decyzja(
                typ=TYP_DECYZJI,
                wybrany_wariant=f"{'Skorygowano' if korekty else 'Zgodne'}: {kwota}",
                uzasadnienie="; ".join(korekty) or "Wszystkie pozycje zgodne z przeliczeniem według stawek",
                decyzja_finansowa=bool(korekty),
            )
        )
        ktx.wyslij(Typ.WYNIK_WERYFIKACJI, {"kwota_naleznosci": str(r.kwota_naleznosci), "korekty": len(korekty)})
        tresc = f"Rozliczenie {d.id_delegacji} zweryfikowane ({kwota}" + (", z korektami" if korekty else "") + ")"
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": tresc + " – zaakceptuj albo zgłoś zastrzeżenia"})

    def przekaz(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        r = rozliczenie_delegacji(s, d)
        if r is None or r.status != StatusRozliczenia.ZWERYFIKOWANE:
            stan = r.status if r else "brak rozliczenia"
            ktx.ostrzezenie(f"Rozliczenie nie jest zweryfikowane ({stan}) – przekazanie pominięte")
            return

        zastrzezenia = str(k.dane.get("zastrzezenia") or "").strip()
        r.data_przekazania = teraz()
        if zastrzezenia:
            r.zastrzezenia = zastrzezenia
            r.status = StatusRozliczenia.PRZEKAZANE_Z_ZASTRZEZENIAMI
            ktx.ostrzezenie(f"Przekazano z zastrzeżeniami delegowanego: {zastrzezenia}")
        else:
            r.status = StatusRozliczenia.PRZEKAZANE
            ktx.opis = f"Przekazano do komórki finansowej, należność {r.kwota_naleznosci:.2f} zł"
        ktx.wyslij(Typ.WYNIK_PRZEKAZANIA, {"status": r.status})
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": f"Rozliczenie {d.id_delegacji}: {r.status}"})
