"""Organizacja wyjazdu w interfejsie – wybór przejazdu (S22IT1S0P2-27, podzadanie -45)
i zakwaterowania (S22IT1S0P2-39, podzadanie -99).

Ekrany pokazują propozycje przygotowane przez agentów – `Przejazd` i `Zakwaterowanie` ze statusem
`StatusRezerwacji.PROPOZYCJA` – i przekazują wybór delegowanego komunikatem:
- `wybor.transport`: `{"wybrane": [id_przejazdu, ...], "uzasadnienie": ...}` albo, gdy delegowany odrzuca
  wszystkie warianty (scenariusz 6a), `{"odrzucone": True, "przyczyna": ...}`;
  zapis przejazdu – Agent Transportu (S22IT1S0P2-57),
- `wybor.zakwaterowanie`: `{"id_zakwaterowania": ..., "uzasadnienie": ...}`;
  zapis rezerwacji – `agents/rezerwacja.py` (S22IT1S0P2-99).
Wyszukiwanie wariantów i obiektów należy do agentów (S22IT1S0P2-27, -97, -98) – ekrany tylko je prezentują.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.zaleznosci import get_broker, get_sesja
from ..messaging import Broker, Komunikat, Typ
from ..models import (
    Delegacja,
    ObiektZakwaterowania,
    Polaczenie,
    Przejazd,
    Status,
    StatusRezerwacji,
    Zakwaterowanie,
    Zolnierz,
)
from .strony import biezacy_uzytkownik, strona

router = APIRouter(prefix="/app", include_in_schema=False)

# Wybór przejazdu i noclegu jest możliwy po zatwierdzeniu; po zorganizowaniu ekrany są tylko do wglądu
ETAPY_ORGANIZACJI = (Status.ZATWIERDZONY, Status.ZORGANIZOWANY)
KIERUNKI = ("Tam", "Powrót")
WYNIK_WYBOR = "wybor-przekazany"
WYNIK_ODRZUCENIE = "warianty-odrzucone"
KOMUNIKATY = {
    WYNIK_WYBOR: "Wybór przekazany – system zapisze go i uzupełni dokumentację delegacji.",
    WYNIK_ODRZUCENIE: "Odrzucono zaproponowane warianty – przejazd zostanie uzgodniony indywidualnie.",
}


def delegacja_do_organizacji(s: Session, id_delegacji: str, uzytkownik: Zolnierz) -> Delegacja:
    """Delegacja delegowanego na etapie organizacji wyjazdu – inni użytkownicy jej nie widzą."""
    d = s.get(Delegacja, id_delegacji)
    if d is None or d.id_zolnierza != uzytkownik.id_zolnierza:
        raise HTTPException(404, "Nie znaleziono delegacji")
    if d.status not in ETAPY_ORGANIZACJI:
        raise HTTPException(409, "Wyjazd nie jest na etapie organizacji")
    return d


def _na_liczbe(tekst: str) -> int | None:
    try:
        return int(tekst)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- przejazd (S22IT1S0P2-45)


def dane_przejazdu(s: Session, d: Delegacja) -> dict:
    przejazdy = s.scalars(
        select(Przejazd)
        .where(Przejazd.id_delegacji == d.id_delegacji)
        .order_by(Przejazd.data_przejazdu, Przejazd.rekomendowany.desc(), Przejazd.id_przejazdu)
    ).all()
    id_polaczen = {p.id_polaczenia for p in przejazdy if p.id_polaczenia}
    polaczenia = {
        x.id_polaczenia: x for x in s.scalars(select(Polaczenie).where(Polaczenie.id_polaczenia.in_(id_polaczen)))
    }
    propozycje = [p for p in przejazdy if p.status == StatusRezerwacji.PROPOZYCJA]
    kierunki = sorted(
        {p.kierunek for p in propozycje}, key=lambda k: KIERUNKI.index(k) if k in KIERUNKI else len(KIERUNKI)
    )
    wybrane = [p for p in przejazdy if p.status == StatusRezerwacji.WYBRANA]
    do_uzgodnienia = any(p.status == StatusRezerwacji.DO_UZGODNIENIA for p in przejazdy)
    return dict(
        d=d,
        warianty={k: [p for p in propozycje if p.kierunek == k] for k in kierunki},
        polaczenia=polaczenia,
        wybrane=wybrane,
        do_uzgodnienia=do_uzgodnienia,
        mozna_wybrac=d.status == Status.ZATWIERDZONY and bool(propozycje) and not wybrane and not do_uzgodnienia,
    )


@router.get("/wnioski/{id_delegacji}/przejazd", response_class=HTMLResponse)
def przejazd(
    id_delegacji: str,
    request: Request,
    wynik: str | None = None,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-45: porównanie wariantów podróży z rekomendacją systemu."""
    d = delegacja_do_organizacji(s, id_delegacji, uzytkownik)
    return strona(request, s, "przejazd.html", uzytkownik, **dane_przejazdu(s, d), komunikat=KOMUNIKATY.get(wynik))


@router.post("/wnioski/{id_delegacji}/przejazd", response_class=HTMLResponse)
async def wybierz_przejazd(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """Wybór wariantu dla każdego kierunku; inny niż rekomendowany wymaga podania przyczyny."""
    d = delegacja_do_organizacji(s, id_delegacji, uzytkownik)
    kontekst = dane_przejazdu(s, d)
    formularz = await request.form()
    uzasadnienie = str(formularz.get("uzasadnienie") or "").strip()
    zaznaczone: dict[str, int | None] = {
        kierunek: _na_liczbe(str(formularz.get(f"wybor_{nr}") or ""))
        for nr, kierunek in enumerate(kontekst["warianty"], start=1)
    }

    komunikat = None
    wybrane: list[Przejazd] = []
    if not kontekst["mozna_wybrac"]:
        komunikat = "Przejazd został już wybrany albo nie ma propozycji do wyboru."
    else:
        for kierunek, warianty in kontekst["warianty"].items():
            wariant = next((p for p in warianty if p.id_przejazdu == zaznaczone[kierunek]), None)
            if wariant is None:
                komunikat = f"Wybierz wariant przejazdu: {kierunek.lower()}."
                break
            wybrane.append(wariant)
        else:
            ma_rekomendacje = {k for k, lista in kontekst["warianty"].items() if any(x.rekomendowany for x in lista)}
            inny_niz_rekomendowany = any(not p.rekomendowany and p.kierunek in ma_rekomendacje for p in wybrane)
            if inny_niz_rekomendowany and not uzasadnienie:
                komunikat = "Wybrano wariant inny niż rekomendowany – podaj przyczynę wyboru."
    if komunikat:
        return strona(
            request,
            s,
            "przejazd.html",
            uzytkownik,
            **kontekst,
            komunikat=komunikat,
            blad=True,
            zaznaczone=set(zaznaczone.values()),
            uzasadnienie=uzasadnienie,
        )

    dane = {"wybrane": [p.id_przejazdu for p in wybrane], "uzasadnienie": uzasadnienie or None}
    await broker.publikuj(Komunikat(typ=Typ.WYBOR_TRANSPORTU, id_delegacji=d.id_delegacji, dane=dane))
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/przejazd?wynik={WYNIK_WYBOR}", status_code=303)


@router.post("/wnioski/{id_delegacji}/przejazd/odrzucenie", response_class=HTMLResponse)
async def odrzuc_warianty_przejazdu(
    id_delegacji: str,
    request: Request,
    przyczyna: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """Scenariusz 6a: żaden wariant nie odpowiada delegowanemu – sprawa do indywidualnego uzgodnienia."""
    d = delegacja_do_organizacji(s, id_delegacji, uzytkownik)
    kontekst = dane_przejazdu(s, d)
    komunikat = None
    if not kontekst["mozna_wybrac"]:
        komunikat = "Przejazd został już wybrany albo nie ma propozycji do wyboru."
    elif not przyczyna.strip():
        komunikat = "Podaj przyczynę odrzucenia wszystkich wariantów."
    if komunikat:
        return strona(request, s, "przejazd.html", uzytkownik, **kontekst, komunikat=komunikat, blad=True)

    dane = {"odrzucone": True, "przyczyna": przyczyna.strip()}
    await broker.publikuj(Komunikat(typ=Typ.WYBOR_TRANSPORTU, id_delegacji=d.id_delegacji, dane=dane))
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/przejazd?wynik={WYNIK_ODRZUCENIE}", status_code=303)


# ---------------------------------------------------------------- zakwaterowanie (S22IT1S0P2-99)


def dane_zakwaterowania(s: Session, d: Delegacja) -> dict:
    noclegi = s.scalars(
        select(Zakwaterowanie)
        .where(Zakwaterowanie.id_delegacji == d.id_delegacji)
        .order_by(Zakwaterowanie.wymaga_odstepstwa, Zakwaterowanie.koszt_calkowity, Zakwaterowanie.id_zakwaterowania)
    ).all()
    id_obiektow = {z.id_obiektu for z in noclegi}
    obiekty = {
        o.id_obiektu: o
        for o in s.scalars(select(ObiektZakwaterowania).where(ObiektZakwaterowania.id_obiektu.in_(id_obiektow)))
    }
    propozycje = [z for z in noclegi if z.status == StatusRezerwacji.PROPOZYCJA]
    wybrane = next((z for z in noclegi if z.status == StatusRezerwacji.WYBRANA), None)
    oczekuje = next((z for z in noclegi if z.status == StatusRezerwacji.DO_DECYZJI_PRZELOZONEGO), None)
    return dict(
        d=d,
        w_limicie=[z for z in propozycje if not z.wymaga_odstepstwa],
        z_odstepstwem=[z for z in propozycje if z.wymaga_odstepstwa],
        obiekty=obiekty,
        wybrane=wybrane,
        oczekuje=oczekuje,
        mozna_wybrac=d.status == Status.ZATWIERDZONY and bool(propozycje) and not wybrane and not oczekuje,
    )


@router.get("/wnioski/{id_delegacji}/zakwaterowanie", response_class=HTMLResponse)
def zakwaterowanie(
    id_delegacji: str,
    request: Request,
    wynik: str | None = None,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """S22IT1S0P2-99: obiekty w limicie noclegowym z kosztem i czasem dojazdu na miejsce zadania."""
    d = delegacja_do_organizacji(s, id_delegacji, uzytkownik)
    kontekst = dane_zakwaterowania(s, d)
    return strona(request, s, "zakwaterowanie.html", uzytkownik, **kontekst, komunikat=KOMUNIKATY.get(wynik))


@router.post("/wnioski/{id_delegacji}/zakwaterowanie", response_class=HTMLResponse)
async def wybierz_zakwaterowanie(
    id_delegacji: str,
    request: Request,
    id_zakwaterowania: str = Form(""),
    uzasadnienie: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """Wybór obiektu; wariant wymagający odstępstwa (3a) wymaga uzasadnienia i trafia do przełożonego."""
    d = delegacja_do_organizacji(s, id_delegacji, uzytkownik)
    kontekst = dane_zakwaterowania(s, d)
    numer = _na_liczbe(id_zakwaterowania)
    wybor = next((z for z in kontekst["w_limicie"] + kontekst["z_odstepstwem"] if z.id_zakwaterowania == numer), None)
    uzasadnienie = uzasadnienie.strip()
    komunikat = None
    if not kontekst["mozna_wybrac"]:
        komunikat = "Zakwaterowanie zostało już wybrane albo nie ma propozycji do wyboru."
    elif wybor is None:
        komunikat = "Wybierz obiekt zakwaterowania."
    elif wybor.wymaga_odstepstwa and not uzasadnienie:
        komunikat = "Ten obiekt wymaga odstępstwa od limitu – podaj uzasadnienie wyboru."
    if komunikat:
        return strona(
            request,
            s,
            "zakwaterowanie.html",
            uzytkownik,
            **kontekst,
            komunikat=komunikat,
            blad=True,
            zaznaczony=numer,
            uzasadnienie=uzasadnienie,
        )

    dane = {"id_zakwaterowania": wybor.id_zakwaterowania, "uzasadnienie": uzasadnienie or None}
    await broker.publikuj(Komunikat(typ=Typ.WYBOR_ZAKWATEROWANIA, id_delegacji=d.id_delegacji, dane=dane))
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/zakwaterowanie?wynik={WYNIK_WYBOR}", status_code=303)
