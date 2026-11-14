"""Wspólne reguły porównania i walidacji podróży (S22IT1S0P2-27, -57).

Terminowość jest warunkiem nadrzędnym. Wśród połączeń spełniających termin
standardowo porównujemy koszt, bezpieczeństwo, przesiadki, punktualność i czas.
Brak godziny zakończenia zajęć w modelu wymaga ręcznej kontroli czasu powrotu.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from unicodedata import normalize
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Delegacja,
    Jednostka,
    LogDecyzji,
    LogOperacji,
    Polaczenie,
    Przedsiewziecie,
    Przejazd,
    Status,
    StatusRezerwacji,
    Zolnierz,
)

Kryterium = Literal["standardowe", "koszt", "czas", "bezpieczenstwo", "punktualnosc"]
KRYTERIA = {
    "standardowe": "Standardowe",
    "koszt": "Najniższy koszt",
    "czas": "Najkrótszy czas",
    "bezpieczenstwo": "Bezpieczeństwo",
    "punktualnosc": "Punktualność",
}
KIERUNKI = ("Tam", "Powrót")
PROG_ROZNICY_CZASU_MIN = 60  # próg prezentacji MVP; do uzgodnienia z PO


class BladPodrozy(ValueError):
    pass


def nazwa_miejscowosci(wartosc: str) -> str:
    return " ".join(normalize("NFC", wartosc).split()).casefold()


class WariantPodrozyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    klucz: UUID
    kierunek: Literal["Tam", "Powrót"]
    przewoznik: str = Field(min_length=1, max_length=100)
    srodek_transportu: Literal["kolej", "autobus", "transport wojskowy", "pojazd służbowy"]
    godzina_odjazdu: time
    czas_min: int = Field(gt=0, le=2880)
    koszt: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    liczba_przesiadek: int = Field(ge=0, le=20)
    punktualnosc: Decimal = Field(ge=0, le=100)
    ocena_bezpieczenstwa: int = Field(ge=1, le=5)
    zrodlo: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def bez_strefy(self):
        if self.godzina_odjazdu.tzinfo is not None:
            raise ValueError("Podaj lokalną godzinę odjazdu bez strefy")
        return self

    @property
    def id_polaczenia(self) -> str:
        return "M-" + self.klucz.hex[:18]


def trasa_delegacji(s: Session, d: Delegacja) -> tuple[str, str]:
    z = s.get(Zolnierz, d.id_zolnierza)
    jednostka = s.get(Jednostka, z.id_jednostki) if z else None
    return (jednostka.miejscowosc if jednostka else "", d.miejsce_docelowe or "")


def decyzja_do_uzgodnienia(s: Session, d: Delegacja) -> bool:
    ostatnia = s.scalar(
        select(LogDecyzji)
        .join(LogOperacji)
        .where(
            LogOperacji.id_delegacji == d.id_delegacji,
            LogOperacji.id_agenta == "AG-04",
            LogDecyzji.typ_decyzji == "Zapis wyboru podróży",
        )
        .order_by(LogDecyzji.id_decyzji.desc())
    )
    return ostatnia is not None and ostatnia.wybrany_wariant == "Do uzgodnienia"


def sprawdz_dodanie_wariantu(s: Session, d: Delegacja) -> None:
    if d.status != Status.ZATWIERDZONY:
        raise BladPodrozy("Oferty można dodawać po zatwierdzeniu wniosku")
    if not d.data_wyjazdu or not d.data_powrotu or not d.miejsce_docelowe:
        raise BladPodrozy("Wniosek musi zawierać miejsce i daty podróży")
    if any(r.status != StatusRezerwacji.PROPOZYCJA for r in przejazdy(s, d)) or decyzja_do_uzgodnienia(s, d):
        raise BladPodrozy("Decyzja dotycząca podróży została już zapisana")


class WyborPodrozy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id_tam: int | None = Field(default=None, gt=0)
    id_powrot: int | None = Field(default=None, gt=0)
    odrzuc_wszystkie: bool = False
    uzasadnienie: str = Field(default="", max_length=2000)
    kryterium: Kryterium = "standardowe"

    @model_validator(mode="after")
    def sprawdz_wybor(self):
        if self.odrzuc_wszystkie:
            if self.id_tam is not None or self.id_powrot is not None:
                raise ValueError("Odrzucenie wszystkich nie może zawierać wybranego przejazdu")
            if not self.uzasadnienie:
                raise ValueError("Podaj przyczynę odrzucenia wszystkich wariantów")
        elif self.id_tam is None or self.id_powrot is None:
            raise ValueError("Wybierz przejazd tam i z powrotem")
        return self


def przejazdy(s: Session, d: Delegacja) -> list[Przejazd]:
    return list(
        s.scalars(select(Przejazd).where(Przejazd.id_delegacji == d.id_delegacji).order_by(Przejazd.id_przejazdu))
    )


def termin(s: Session, d: Delegacja) -> datetime | None:
    p = s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None
    if (
        p
        and p.godzina_rozpoczecia
        and nazwa_miejscowosci(p.miejscowosc) == nazwa_miejscowosci(d.miejsce_docelowe or "")
    ):
        return datetime.combine(p.data_rozpoczecia, p.godzina_rozpoczecia)
    return None


def przyjazd(p: Polaczenie, data) -> datetime:
    return datetime.combine(data, p.godzina_odjazdu) + timedelta(minutes=p.czas_min)


def ocena(p: Polaczenie, data, deadline: datetime | None, kryterium: str = "standardowe") -> tuple:
    spozniony = deadline is not None and przyjazd(p, data) > deadline
    wspolne = (p.koszt, -p.ocena_bezpieczenstwa, p.liczba_przesiadek, -p.punktualnosc, p.czas_min)
    priorytet = {
        "standardowe": (),
        "koszt": (),
        "czas": (p.czas_min,),
        "bezpieczenstwo": (-p.ocena_bezpieczenstwa,),
        "punktualnosc": (-p.punktualnosc,),
    }[kryterium]
    return (spozniony, *priorytet, *wspolne, p.id_polaczenia)


def zestawienie(s: Session, d: Delegacja, kryterium: str = "standardowe") -> dict:
    deadline = termin(s, d)
    warianty = []
    for r in przejazdy(s, d):
        p = s.get(Polaczenie, r.id_polaczenia) if r.id_polaczenia else None
        if p is None:
            continue
        limit = deadline if r.kierunek == "Tam" else None
        spozniony = limit is not None and przyjazd(p, r.data_przejazdu) > limit
        warianty.append(
            {
                "id_przejazdu": r.id_przejazdu,
                "id_polaczenia": r.id_polaczenia,
                "kierunek": r.kierunek,
                "data_przejazdu": r.data_przejazdu,
                "skad": p.skad,
                "dokad": p.dokad,
                "srodek_transportu": r.srodek_transportu,
                "przewoznik": p.przewoznik,
                "godzina_odjazdu": p.godzina_odjazdu,
                "przyjazd": przyjazd(p, r.data_przejazdu),
                "czas_min": p.czas_min,
                "koszt": r.koszt,
                "liczba_przesiadek": p.liczba_przesiadek,
                "punktualnosc": p.punktualnosc,
                "bezpieczenstwo": p.ocena_bezpieczenstwa,
                "status": r.status,
                "uzasadnienie": r.uzasadnienie,
                "zrodlo": r.powod_zmiany if p.id_polaczenia.startswith("M-") else None,
                "spozniony": spozniony,
                "rekomendowany": False,
            }
        )
    for kierunek in KIERUNKI:
        grupa = [w for w in warianty if w["kierunek"] == kierunek and not w["spozniony"]]
        if not grupa:
            continue

        def klucz(w, kierunek=kierunek):
            r = s.get(Przejazd, w["id_przejazdu"])
            p = s.get(Polaczenie, r.id_polaczenia)
            return ocena(p, r.data_przejazdu, deadline if kierunek == "Tam" else None, kryterium)

        najlepszy = min(grupa, key=klucz)
        najlepszy["rekomendowany"] = True
        najlepszy["podstawa_rekomendacji"] = (
            f"Priorytet: {KRYTERIA[kryterium]}. "
            "Najpierw terminowość; przy remisie koszt, bezpieczeństwo, przesiadki, punktualność i czas."
        )
    return {
        "kryterium": kryterium,
        "warianty": warianty,
        "istotna_roznica_czasu": any(
            len(czasy := [w["czas_min"] for w in warianty if w["kierunek"] == kierunek]) > 1
            and max(czasy) - min(czasy) > PROG_ROZNICY_CZASU_MIN
            for kierunek in KIERUNKI
        ),
        "brak_kierunkow": [k for k in KIERUNKI if not any(w["kierunek"] == k for w in warianty)],
        "mozna_zatwierdzic": all(any(w["kierunek"] == k and not w["spozniony"] for w in warianty) for k in KIERUNKI),
        "termin_rozpoczecia": deadline,
        "trasa": trasa_delegacji(s, d),
        "do_uzgodnienia": decyzja_do_uzgodnienia(s, d),
        "uwaga": "Przed zatwierdzeniem sprawdź, czy odjazd powrotny następuje po zakończeniu zadania.",
    }


def sprawdz_decyzje(s: Session, d: Delegacja, wybor: WyborPodrozy) -> list[Przejazd]:
    if d.status != Status.ZATWIERDZONY:
        raise BladPodrozy("Wybór podróży jest dostępny dla zatwierdzonej delegacji")
    rows = przejazdy(s, d)
    if decyzja_do_uzgodnienia(s, d):
        raise BladPodrozy("Podróż została już przekazana do uzgodnienia")
    if any(r.status != StatusRezerwacji.PROPOZYCJA for r in rows):
        raise BladPodrozy("Decyzja dotycząca podróży została już zapisana")
    if wybor.odrzuc_wszystkie:
        return []
    wybrane = [next((r for r in rows if r.id_przejazdu == i), None) for i in (wybor.id_tam, wybor.id_powrot)]
    if any(r is None for r in wybrane) or [r.kierunek for r in wybrane] != list(KIERUNKI):
        raise BladPodrozy("Wybierz istniejące warianty tej delegacji we właściwych kierunkach")
    podsumowanie = zestawienie(s, d, wybor.kryterium)
    ids_rekomendowane = {w["id_przejazdu"] for w in podsumowanie["warianty"] if w["rekomendowany"]}
    if any(r.id_przejazdu not in ids_rekomendowane for r in wybrane) and not wybor.uzasadnienie:
        raise BladPodrozy("Podaj uzasadnienie wyboru innego niż rekomendowany")
    p_tam, p_powrot = [s.get(Polaczenie, r.id_polaczenia) for r in wybrane]
    if p_tam is None or p_powrot is None:
        raise BladPodrozy("Brak danych wybranego połączenia")
    koniec_tam = przyjazd(p_tam, wybrane[0].data_przejazdu)
    deadline = termin(s, d)
    if deadline is not None and koniec_tam > deadline:
        raise BladPodrozy("Przejazd nie zapewnia dotarcia na rozpoczęcie szkolenia")
    if datetime.combine(wybrane[1].data_przejazdu, p_powrot.godzina_odjazdu) < max(koniec_tam, deadline or koniec_tam):
        raise BladPodrozy("Powrót nie może rozpocząć się przed przyjazdem i rozpoczęciem szkolenia")
    return wybrane
