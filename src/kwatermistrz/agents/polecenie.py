"""S22IT1S0P2-38 – Wygenerowanie polecenia wyjazdu służbowego po zatwierdzeniu wniosku.

Wywoływane przez Agenta Wniosków po komunikacie `zadanie.polecenie` (Koordynator wysyła go po zatwierdzeniu).

1. Dane według wzoru: delegowany (stopień, imię i nazwisko, jednostka), cel, miejsce, termin,
   podstawa (numer i data rozkazu), przełożony zatwierdzający i data decyzji.
2. Brak danych wymaganych we wzorze (scenariusz 2a) → ostrzeżenie w logu i powiadomienie osoby organizującej
   delegację (przełożonego zatwierdzającego); polecenia nie zapisujemy.
3. `PolecenieWyjazdu` z kolejnym numerem w roku (PW/2026/0001) i treścią (`tresc`) wypełnioną według wzoru.
4. `Typ.WYNIK_POLECENIA` do Koordynatora i powiadomienie delegowanego.
Dokument udostępnia `GET /delegacje/{id}/polecenie` w `api/organizacja.py`.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..db import teraz
from ..messaging import Komunikat, Typ
from ..models import Delegacja, Jednostka, PolecenieWyjazdu, Zolnierz
from ..walidacja import parsuj_date
from .base import Kontekst, pobierz_delegacje

WERSJA_WZORU = "2026-1"
TYP_DECYZJI = "Sporządzenie polecenia wyjazdu"

# Pola wzoru, bez których polecenia nie można wydać – klucz w treści → nazwa dla człowieka
POLA_WYMAGANE = {
    "delegowany.stopien": "stopień delegowanego",
    "delegowany.imie_nazwisko": "imię i nazwisko delegowanego",
    "delegowany.jednostka": "jednostka delegowanego",
    "cel": "cel wyjazdu",
    "miejsce": "miejsce docelowe",
    "termin.od": "data wyjazdu",
    "termin.do": "data powrotu",
    "podstawa.numer_rozkazu": "numer rozkazu",
    "podstawa.data_rozkazu": "data rozkazu",
    "zatwierdzajacy.stopien": "stopień przełożonego zatwierdzającego",
    "zatwierdzajacy.imie_nazwisko": "imię i nazwisko przełożonego zatwierdzającego",
    "zatwierdzajacy.data_decyzji": "data decyzji przełożonego",
}


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _podstawa(d: Delegacja) -> tuple[str | None, date | None]:
    """Podstawa wyjazdu z pól delegacji, a gdy walidacja ich nie przepisała – z danych wniosku."""
    wniosek = d.dane_wniosku or {}
    numer = d.podstawa_numer_rozkazu or wniosek.get("podstawa_numer_rozkazu")
    data_rozkazu = d.podstawa_data_rozkazu
    if data_rozkazu is None and isinstance(wniosek.get("podstawa_data_rozkazu"), str):
        data_rozkazu = parsuj_date(wniosek["podstawa_data_rozkazu"])
    return (numer.strip() or None) if isinstance(numer, str) else None, data_rozkazu


def tresc_polecenia(s: Session, d: Delegacja) -> dict:
    """Wypełnia wzór polecenia danymi delegacji; brakujące pola zostają puste (None)."""
    delegowany = s.get(Zolnierz, d.id_zolnierza)
    jednostka = s.get(Jednostka, delegowany.id_jednostki) if delegowany else None
    zatwierdzajacy = s.get(Zolnierz, d.id_zatwierdzajacego) if d.id_zatwierdzajacego else None
    numer_rozkazu, data_rozkazu = _podstawa(d)
    return {
        "delegowany": {
            "id_zolnierza": d.id_zolnierza,
            "stopien": delegowany.stopien if delegowany else None,
            "imie_nazwisko": delegowany.imie_nazwisko if delegowany else None,
            "jednostka": jednostka.nazwa if jednostka else None,
        },
        "cel": d.cel_wyjazdu,
        "miejsce": d.miejsce_docelowe,
        "termin": {"od": _iso(d.data_wyjazdu), "do": _iso(d.data_powrotu)},
        "podstawa": {"numer_rozkazu": numer_rozkazu, "data_rozkazu": _iso(data_rozkazu)},
        "zatwierdzajacy": {
            "id_zolnierza": d.id_zatwierdzajacego,
            "stopien": zatwierdzajacy.stopien if zatwierdzajacy else None,
            "imie_nazwisko": zatwierdzajacy.imie_nazwisko if zatwierdzajacy else None,
            "data_decyzji": _iso(d.data_decyzji.date()) if d.data_decyzji else None,
        },
    }


def brakujace_pola(tresc: dict) -> list[str]:
    brakuje = []
    for sciezka, nazwa in POLA_WYMAGANE.items():
        wartosc = tresc
        for klucz in sciezka.split("."):
            wartosc = wartosc.get(klucz) if isinstance(wartosc, dict) else None
        if wartosc in (None, ""):
            brakuje.append(nazwa)
    return brakuje


def kolejny_numer(s: Session, rok: int) -> str:
    """Kolejny numer polecenia w roku: PW/2026/0001, PW/2026/0002…"""
    prefiks = f"PW/{rok}/"
    ostatni = s.scalar(
        select(PolecenieWyjazdu.numer)
        .where(PolecenieWyjazdu.numer.like(f"{prefiks}%"))
        .order_by(PolecenieWyjazdu.numer.desc())
        .limit(1)
    )
    nr = int(ostatni.removeprefix(prefiks)) + 1 if ostatni else 1
    return f"{prefiks}{nr:04d}"


def sporzadz_polecenie(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)

    # Ponowione zlecenie nie tworzy drugiego polecenia – odsyłamy numer już wydanego
    istniejace = s.scalar(select(PolecenieWyjazdu).where(PolecenieWyjazdu.id_delegacji == d.id_delegacji))
    if istniejace is not None:
        ktx.opis = f"Polecenie {istniejace.numer} zostało już sporządzone"
        ktx.wyslij(Typ.WYNIK_POLECENIA, {"numer": istniejace.numer})
        return

    tresc = tresc_polecenia(s, d)
    brakuje = brakujace_pola(tresc)
    if brakuje:
        opis = "Nie sporządzono polecenia wyjazdu – brak danych: " + ", ".join(brakuje)
        ktx.ostrzezenie(opis)
        ktx.decyzja(Decyzja(typ=TYP_DECYZJI, wybrany_wariant="Wstrzymano", uzasadnienie=opis))
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {"do": d.id_zatwierdzajacego or d.id_zolnierza, "tresc": f"Delegacja {d.id_delegacji}: {opis}"},
        )
        return

    numer = kolejny_numer(s, teraz().year)
    s.add(PolecenieWyjazdu(numer=numer, id_delegacji=d.id_delegacji, wersja_wzoru=WERSJA_WZORU, tresc=tresc))
    ktx.opis = f"Sporządzono polecenie wyjazdu {numer}"
    ktx.decyzja(
        Decyzja(
            typ=TYP_DECYZJI,
            wybrany_wariant=numer,
            uzasadnienie=f"Wszystkie dane wymagane we wzorze {WERSJA_WZORU} są dostępne",
        )
    )
    ktx.wyslij(Typ.WYNIK_POLECENIA, {"numer": numer})
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {"do": d.id_zolnierza, "tresc": f"Sporządzono polecenie wyjazdu {numer} dla delegacji {d.id_delegacji}"},
    )
