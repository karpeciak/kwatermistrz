"""Wspólne reguły porównania i walidacji podróży (S22IT1S0P2-27, -57).

Terminowość jest warunkiem nadrzędnym. Wśród połączeń spełniających termin
standardowo porównujemy koszt, bezpieczeństwo, przesiadki, punktualność i czas.
Brak godziny zakończenia zajęć w modelu wymaga ręcznej kontroli czasu powrotu.
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Delegacja, Polaczenie, Przedsiewziecie, Przejazd, Status, StatusRezerwacji

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
    if p and p.godzina_rozpoczecia:
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
        "termin_rozpoczecia": deadline,
        "uwaga": "Godzinę powrotu porównaj z zakończeniem zajęć — model nie zawiera godziny ich zakończenia.",
    }


def sprawdz_decyzje(s: Session, d: Delegacja, wybor: WyborPodrozy) -> list[Przejazd]:
    if d.status != Status.ZATWIERDZONY:
        raise BladPodrozy("Wybór podróży jest dostępny dla zatwierdzonej delegacji")
    rows = przejazdy(s, d)
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
