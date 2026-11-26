"""Historia przynaleznosci organizacyjnej i snapshot jednostki delegacji."""

from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import teraz
from .models import Delegacja, Jednostka, PrzynaleznoscJednostki, Zolnierz


def historia_przynaleznosci(s: Session, id_zolnierza: str) -> list[PrzynaleznoscJednostki]:
    return list(
        s.scalars(
            select(PrzynaleznoscJednostki)
            .where(PrzynaleznoscJednostki.id_zolnierza == id_zolnierza)
            .order_by(PrzynaleznoscJednostki.data_od.desc(), PrzynaleznoscJednostki.id_przynaleznosci.desc())
        )
    )


def biezaca_przynaleznosc(s: Session, id_zolnierza: str, *, blokuj: bool = False) -> PrzynaleznoscJednostki | None:
    zapytanie = select(PrzynaleznoscJednostki).where(
        PrzynaleznoscJednostki.id_zolnierza == id_zolnierza,
        PrzynaleznoscJednostki.data_do.is_(None),
    )
    if blokuj:
        zapytanie = zapytanie.with_for_update()
    wpisy = list(s.scalars(zapytanie))
    if len(wpisy) > 1:
        raise HTTPException(409, "Żołnierz ma więcej niż jedno bieżące przypisanie")
    return wpisy[0] if wpisy else None


def jednostka_na_dzien(s: Session, id_zolnierza: str, dzien: date) -> Jednostka | None:
    wpis = s.scalar(
        select(PrzynaleznoscJednostki)
        .where(
            PrzynaleznoscJednostki.id_zolnierza == id_zolnierza,
            PrzynaleznoscJednostki.data_od <= dzien,
            (PrzynaleznoscJednostki.data_do.is_(None) | (PrzynaleznoscJednostki.data_do >= dzien)),
        )
        .order_by(PrzynaleznoscJednostki.data_od.desc())
        .limit(1)
    )
    if wpis is not None:
        return s.get(Jednostka, wpis.id_jednostki)

    najwczesniejszy_wpis = s.scalar(
        select(PrzynaleznoscJednostki)
        .where(PrzynaleznoscJednostki.id_zolnierza == id_zolnierza)
        .order_by(PrzynaleznoscJednostki.data_od)
        .limit(1)
    )
    if najwczesniejszy_wpis is not None and dzien < najwczesniejszy_wpis.data_od:
        return s.get(Jednostka, najwczesniejszy_wpis.id_jednostki)

    zolnierz = s.get(Zolnierz, id_zolnierza)
    if zolnierz is not None and najwczesniejszy_wpis is None:
        return s.get(Jednostka, zolnierz.id_jednostki)
    return None


def dostep_do_przynaleznosci(s: Session, id_zolnierza: str, operator: Zolnierz, *, zapis: bool = False) -> Zolnierz:
    zolnierz = s.get(Zolnierz, id_zolnierza)
    if zolnierz is None:
        raise HTTPException(404, f"Nie znaleziono żołnierza {id_zolnierza}")
    wlasny_profil = zolnierz.id_zolnierza == operator.id_zolnierza
    bezposredni_podwladny = zolnierz.id_przelozonego == operator.id_zolnierza
    if (zapis and not bezposredni_podwladny) or (not zapis and not (wlasny_profil or bezposredni_podwladny)):
        raise HTTPException(403, "Brak uprawnień do profilu przynależności")
    return zolnierz


def jednostka_delegacji(s: Session, delegacja: Delegacja) -> Jednostka | None:
    if delegacja.id_jednostki:
        return s.get(Jednostka, delegacja.id_jednostki)
    zolnierz = s.get(Zolnierz, delegacja.id_zolnierza)
    return s.get(Jednostka, zolnierz.id_jednostki) if zolnierz else None


def zmien_jednostke(
    s: Session,
    id_zolnierza: str,
    id_jednostki: str,
    data_od: date,
    *,
    operator: Zolnierz,
    powod: str | None = None,
    zrodlo: str | None = None,
) -> PrzynaleznoscJednostki:
    zolnierz = dostep_do_przynaleznosci(s, id_zolnierza, operator, zapis=True)
    if s.get(Jednostka, id_jednostki) is None:
        raise HTTPException(404, f"Nie znaleziono jednostki {id_jednostki}")
    if data_od > teraz().date():
        raise HTTPException(422, "Data zmiany jednostki nie może przypadać w przyszłości")

    with s.begin_nested():
        biezacy = biezaca_przynaleznosc(s, id_zolnierza, blokuj=True)
        if biezacy is None:
            raise HTTPException(409, "Brak bieżącego wpisu historii przynależności")
        if biezacy.id_jednostki == id_jednostki:
            raise HTTPException(409, "Żołnierz jest już przypisany do tej jednostki")
        if data_od <= biezacy.data_od:
            raise HTTPException(409, "Data zmiany musi być późniejsza od początku bieżącego przypisania")

        kolizja = s.scalar(
            select(PrzynaleznoscJednostki.id_przynaleznosci)
            .where(
                PrzynaleznoscJednostki.id_zolnierza == id_zolnierza,
                PrzynaleznoscJednostki.id_przynaleznosci != biezacy.id_przynaleznosci,
                (PrzynaleznoscJednostki.data_do.is_(None) | (PrzynaleznoscJednostki.data_do >= data_od)),
            )
            .limit(1)
        )
        if kolizja is not None:
            raise HTTPException(409, "Nowe przypisanie nakłada się na istniejący przedział")

        biezacy.data_do = data_od - timedelta(days=1)
        nowy = PrzynaleznoscJednostki(
            id_zolnierza=id_zolnierza,
            id_jednostki=id_jednostki,
            data_od=data_od,
            powod=(powod or "").strip() or None,
            id_operatora=operator.id_zolnierza,
            zrodlo=zrodlo,
        )
        s.add(nowy)
        zolnierz.id_jednostki = id_jednostki
        s.flush()
    return nowy
