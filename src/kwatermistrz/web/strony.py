"""Interfejs użytkownika (`/app`) – strony generowane przez FastAPI (Jinja2) z HTMX do odświeżania fragmentów.

Logowanie jest demonstracyjne: użytkownik wybiera żołnierza z listy, a jego identyfikator trafia do ciasteczka.
Docelowe uwierzytelnianie (np. konto uczelniane / Entra ID) – osobna historyjka.

Strony do rozbudowy w historyjkach sprintu 1:
- S22IT1S0P2-37 wniosek roboczy, -33 podsumowanie przed złożeniem → `nowy_wniosek.html`,
- S22IT1S0P2-32 pełna historia i czas oczekiwania → `wniosek.html`,
- S22IT1S0P2-35 zbiorcze zatwierdzanie z budżetem → `do_zatwierdzenia.html`,
- S22IT1S0P2-38, -27, -39 polecenie wyjazdu, wybór przejazdu i noclegu → nowe sekcje w `wniosek.html`,
- S22IT1S0P2-36, -20, -43 rozliczenie → nowa strona `rozliczenie.html`.
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.koordynator import brakujace_elementy_organizacji
from ..api.organizacja import przyjmij_wariant, przyjmij_wybor
from ..api.rozliczenia import KOMUNIKAT_PRZYJETY, DokumentKosztowyIn, przyjmij_dokument
from ..api.schemas import WniosekIn
from ..api.wnioski import zarejestruj_wniosek
from ..api.zaleznosci import dostep_do_podrozy, get_broker, get_sesja, sprawdz_csrf
from ..config import settings
from ..db import teraz
from ..messaging import Broker, Komunikat, Typ
from ..models import (
    Agent,
    Delegacja,
    DokumentKosztowy,
    Jednostka,
    LogDecyzji,
    LogOperacji,
    Polaczenie,
    Przedsiewziecie,
    Przejazd,
    Rozliczenie,
    Status,
    StatusRezerwacji,
    Zolnierz,
)
from ..podroze import (
    KRYTERIA,
    Kryterium,
    WariantPodrozyIn,
    WyborPodrozy,
    decyzja_do_uzgodnienia,
    nazwa_miejscowosci,
    zestawienie,
)
from ..sesje import CIASTECZKO, CZAS_SESJI, odczytaj_sesje, token_csrf, utworz_sesje

KATALOG = Path(__file__).parent
STREFA = ZoneInfo("Europe/Warsaw")

# Sam status nie potwierdza pracy agenta. Krótkie odpytywanie służy oczekiwaniu na odpowiedź.
ETAPY_W_TOKU = {Status.ZLOZONY, Status.ZATWIERDZONY}
ETAPY_PODROZY = (Status.ZATWIERDZONY, Status.ZORGANIZOWANY, Status.W_TRAKCIE, Status.DO_ROZLICZENIA, Status.ROZLICZONY)
WYNIK_DOKUMENT_PRZYJETY = "dokument-przyjety"

szablony = Jinja2Templates(directory=KATALOG / "szablony")
router = APIRouter(prefix="/app", include_in_schema=False)


# ---------------------------------------------------------------- filtry szablonów


def czas_lokalny(wartosc: datetime | None) -> str:
    if wartosc is None:
        return "–"
    return wartosc.replace(tzinfo=ZoneInfo("UTC")).astimezone(STREFA).strftime("%d.%m.%Y %H:%M")


def ile_czasu(od: datetime | None) -> str:
    if od is None:
        return "–"
    minuty = int((teraz() - od).total_seconds() // 60)
    if minuty < 60:
        return f"{minuty} min"
    if minuty < 24 * 60:
        return f"{minuty // 60} h {minuty % 60} min"
    return f"{minuty // (24 * 60)} dni {minuty % (24 * 60) // 60} h"


def data_pl(wartosc: str | None) -> str:
    """Data z wniosku (RRRR-MM-DD lub DD.MM.RRRR) w formacie DD.MM.RRRR."""
    if not wartosc:
        return "–"
    rok, _, reszta = str(wartosc).partition("-")
    if reszta and len(rok) == 4:
        miesiac, _, dzien = reszta.partition("-")
        return f"{dzien}.{miesiac}.{rok}"
    return str(wartosc)


def klasa_statusu(status: str) -> str:
    return "status-" + status.replace(" ", "-").replace("ł", "l").replace("ż", "z").replace("ę", "e")


szablony.env.filters.update(czas=czas_lokalny, ile_czasu=ile_czasu, klasa_statusu=klasa_statusu, data_pl=data_pl)


# ---------------------------------------------------------------- użytkownik


def biezacy_uzytkownik(request: Request, s: Session = Depends(get_sesja)) -> Zolnierz:
    id_zolnierza = odczytaj_sesje(request.cookies.get(CIASTECZKO), settings.session_secret.get_secret_value())
    uzytkownik = s.get(Zolnierz, id_zolnierza) if id_zolnierza else None
    if uzytkownik is None:
        raise HTTPException(303, headers={"Location": "/app/logowanie"})
    return uzytkownik


def liczba_do_zatwierdzenia(s: Session, uzytkownik: Zolnierz) -> int:
    return s.scalar(
        select(func.count()).where(
            Delegacja.status == Status.W_AKCEPTACJI, Delegacja.id_zatwierdzajacego == uzytkownik.id_zolnierza
        )
    )


def strona(request: Request, s: Session, szablon: str, uzytkownik: Zolnierz | None = None, **kontekst):
    if uzytkownik is not None:
        kontekst["do_zatwierdzenia"] = liczba_do_zatwierdzenia(s, uzytkownik)
        kontekst["csrf"] = token_csrf(request.cookies.get(CIASTECZKO, ""), settings.session_secret.get_secret_value())
    return szablony.TemplateResponse(request, szablon, {"uzytkownik": uzytkownik, **kontekst})


# ---------------------------------------------------------------- logowanie


@router.get("", response_class=HTMLResponse)
def start():
    return RedirectResponse("/app/pulpit", status_code=303)


@router.get("/logowanie", response_class=HTMLResponse)
def logowanie(request: Request, s: Session = Depends(get_sesja)):
    zolnierze = s.scalars(select(Zolnierz).order_by(Zolnierz.id_zolnierza)).all()
    return strona(request, s, "logowanie.html", zolnierze=zolnierze)


@router.post("/logowanie")
def zaloguj(request: Request, id_zolnierza: str = Form(...), s: Session = Depends(get_sesja)):
    if s.get(Zolnierz, id_zolnierza) is None:
        raise HTTPException(404, "Nie ma takiego żołnierza")
    odpowiedz = RedirectResponse("/app/pulpit", status_code=303)
    odpowiedz.set_cookie(
        CIASTECZKO,
        utworz_sesje(id_zolnierza, settings.session_secret.get_secret_value()),
        max_age=CZAS_SESJI,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return odpowiedz


@router.post("/wyloguj")
def wyloguj(request: Request, csrf: str = Form("")):
    sprawdz_csrf(request, csrf)
    odpowiedz = RedirectResponse("/app/logowanie", status_code=303)
    odpowiedz.delete_cookie(CIASTECZKO)
    return odpowiedz


# ---------------------------------------------------------------- transport S22IT1S0P2-27, -57


@router.get("/podroze", response_class=HTMLResponse)
def lista_podrozy(
    request: Request,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    wnioski = s.scalars(
        select(Delegacja)
        .where(
            Delegacja.status.in_(ETAPY_PODROZY),
            (Delegacja.id_zolnierza == uzytkownik.id_zolnierza)
            | (Delegacja.id_zatwierdzajacego == uzytkownik.id_zolnierza),
        )
        .order_by(Delegacja.data_zmiany_statusu.desc(), Delegacja.id_delegacji)
    ).all()
    return strona(
        request,
        s,
        "podroze.html",
        uzytkownik,
        wnioski=wnioski,
        wnioskodawcy={z.id_zolnierza: z for z in s.scalars(select(Zolnierz))},
    )


def widok_podrozy(
    id_delegacji: str,
    request: Request,
    s: Session,
    uzytkownik: Zolnierz,
    kryterium: Kryterium = "standardowe",
    przekazano: bool = False,
    oferta: str | None = None,
    blad: str | None = None,
    status: int = 200,
    dane_oferty: dict | None = None,
    dane_wyboru: dict | None = None,
):
    d = dostep_do_podrozy(s, id_delegacji, uzytkownik)
    dane = zestawienie(s, d, kryterium)
    rozstrzygniete = dane["do_uzgodnienia"] or any(w["status"] != "propozycja" for w in dane["warianty"])
    odpowiedz = strona(
        request,
        s,
        "podroz.html",
        uzytkownik,
        d=d,
        **dane,
        kryteria=KRYTERIA,
        przekazano=przekazano,
        rozstrzygniete=rozstrzygniete,
        moze_wybrac=d.status == Status.ZATWIERDZONY and uzytkownik.id_zolnierza == d.id_zatwierdzajacego,
        blad=blad,
        dane_oferty=dane_oferty or {"klucz": str(uuid4())},
        dane_wyboru=dane_wyboru or {},
        oferta_zapisana=oferta and any(w["id_polaczenia"] == oferta for w in dane["warianty"]),
    )
    odpowiedz.status_code = status
    return odpowiedz


@router.get("/wnioski/{id_delegacji}/podroz", response_class=HTMLResponse)
def podroz(
    id_delegacji: str,
    request: Request,
    kryterium: Kryterium = "standardowe",
    przekazano: bool = False,
    oferta: str | None = None,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    return widok_podrozy(id_delegacji, request, s, uzytkownik, kryterium, przekazano, oferta)


@router.post("/wnioski/{id_delegacji}/podroz/warianty", response_class=HTMLResponse)
async def uzupelnij_wariant(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    formularz = await request.form()
    sprawdz_csrf(request, formularz.get("csrf"))
    dostep_do_podrozy(s, id_delegacji, uzytkownik, zapis=True)
    dane = {pole: str(formularz.get(pole, "")) for pole in WariantPodrozyIn.model_fields}
    try:
        wariant = WariantPodrozyIn.model_validate(dane)
        await przyjmij_wariant(s, broker, id_delegacji, uzytkownik, wariant)
    except ValidationError as e:
        b = e.errors()[0]
        etykiety = {
            "klucz": "identyfikator formularza (odśwież stronę)",
            "kierunek": "kierunek",
            "przewoznik": "przewoźnik",
            "srodek_transportu": "środek transportu",
            "godzina_odjazdu": "godzina odjazdu",
            "czas_min": "czas przejazdu (1–2880 minut)",
            "koszt": "koszt (nieujemna kwota, do dwóch miejsc po przecinku)",
            "liczba_przesiadek": "liczba przesiadek (0–20)",
            "punktualnosc": "punktualność (0–100%)",
            "ocena_bezpieczenstwa": "ocena bezpieczeństwa (1–5)",
            "zrodlo": "źródło i podstawa oceny",
        }
        return widok_podrozy(
            id_delegacji,
            request,
            s,
            uzytkownik,
            status=422,
            dane_oferty=dane,
            blad="Sprawdź pole: " + etykiety.get(str(b["loc"][0]), "dane oferty") + ".",
        )
    except HTTPException as e:
        if e.status_code not in (400, 409, 422):
            raise
        return widok_podrozy(
            id_delegacji, request, s, uzytkownik, status=e.status_code, dane_oferty=dane, blad=str(e.detail)
        )
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/podroz?oferta={wariant.id_polaczenia}", status_code=303)


@router.post("/wnioski/{id_delegacji}/podroz")
async def wybierz_podroz(
    id_delegacji: str,
    request: Request,
    id_tam: str = Form(""),
    id_powrot: str = Form(""),
    akcja: str = Form("wybierz"),
    kryterium: Annotated[Kryterium, Form()] = "standardowe",
    uzasadnienie: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    sprawdz_csrf(request, csrf)
    dostep_do_podrozy(s, id_delegacji, uzytkownik, zapis=True)
    if akcja not in ("wybierz", "odrzuc"):
        raise HTTPException(400, "Nieznana decyzja")
    try:
        dane = WyborPodrozy(
            id_tam=(id_tam or None) if akcja == "wybierz" else None,
            id_powrot=(id_powrot or None) if akcja == "wybierz" else None,
            odrzuc_wszystkie=akcja == "odrzuc",
            kryterium=kryterium,
            uzasadnienie=uzasadnienie,
        )
        await przyjmij_wybor(s, broker, id_delegacji, uzytkownik, dane)
    except (ValidationError, HTTPException) as e:
        if isinstance(e, HTTPException) and e.status_code not in (400, 409, 422):
            raise
        return widok_podrozy(
            id_delegacji,
            request,
            s,
            uzytkownik,
            kryterium=kryterium,
            status=e.status_code if isinstance(e, HTTPException) else 422,
            blad=str(e.detail)
            if isinstance(e, HTTPException)
            else "Wybierz oba przejazdy. Przy odrzuceniu wszystkich wariantów podaj uzasadnienie.",
            dane_wyboru={"id_tam": id_tam, "id_powrot": id_powrot, "uzasadnienie": uzasadnienie},
        )
    return RedirectResponse(f"/app/wnioski/{id_delegacji}/podroz?przekazano=true", status_code=303)


# ---------------------------------------------------------------- wnioskodawca


@router.get("/pulpit", response_class=HTMLResponse)
def pulpit(request: Request, s: Session = Depends(get_sesja), uzytkownik: Zolnierz = Depends(biezacy_uzytkownik)):
    wnioski = s.scalars(
        select(Delegacja)
        .where(Delegacja.id_zolnierza == uzytkownik.id_zolnierza)
        .order_by(Delegacja.data_zmiany_statusu.desc())
    ).all()
    return strona(request, s, "pulpit.html", uzytkownik, wnioski=wnioski)


@router.get("/wnioski/nowy", response_class=HTMLResponse)
def nowy_wniosek(request: Request, s: Session = Depends(get_sesja), uzytkownik: Zolnierz = Depends(biezacy_uzytkownik)):
    przedsiewziecia = s.scalars(select(Przedsiewziecie).order_by(Przedsiewziecie.data_rozpoczecia)).all()
    jednostka = s.get(Jednostka, uzytkownik.id_jednostki)
    miasta_ofert = sorted(
        {
            p.dokad
            for p in s.scalars(select(Polaczenie))
            if jednostka
            and not p.id_polaczenia.startswith("M-")
            and nazwa_miejscowosci(p.skad) == nazwa_miejscowosci(jednostka.miejscowosc)
        }
    )
    return strona(
        request, s, "nowy_wniosek.html", uzytkownik, przedsiewziecia=przedsiewziecia, miasta_ofert=miasta_ofert
    )


@router.post("/wnioski/nowy")
async def zloz_nowy_wniosek(
    id_przedsiewziecia: str = Form(""),
    cel_wyjazdu: str = Form(""),
    miejsce_docelowe: str = Form(""),
    data_wyjazdu: str = Form(""),
    data_powrotu: str = Form(""),
    podstawa_numer_rozkazu: str = Form(""),
    podstawa_data_rozkazu: str = Form(""),
    srodek_transportu: str = Form("kolej"),
    potrzeba_zakwaterowania: bool = Form(False),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    przedsiewziecie = s.get(Przedsiewziecie, id_przedsiewziecia) if id_przedsiewziecia else None
    if przedsiewziecie:
        # puste pola uzupełniamy danymi wybranego przedsięwzięcia
        cel_wyjazdu = cel_wyjazdu or przedsiewziecie.nazwa
        miejsce_docelowe = miejsce_docelowe or przedsiewziecie.miejscowosc
        data_wyjazdu = data_wyjazdu or przedsiewziecie.data_rozpoczecia.isoformat()
        data_powrotu = data_powrotu or przedsiewziecie.data_zakonczenia.isoformat()
    wniosek = WniosekIn(
        id_zolnierza=uzytkownik.id_zolnierza,
        id_przedsiewziecia=przedsiewziecie.id_przedsiewziecia if przedsiewziecie else None,
        cel_wyjazdu=cel_wyjazdu or None,
        miejsce_docelowe=miejsce_docelowe or None,
        data_wyjazdu=data_wyjazdu or None,
        data_powrotu=data_powrotu or None,
        srodek_transportu=srodek_transportu or None,
        potrzeba_zakwaterowania=potrzeba_zakwaterowania,
        podstawa_numer_rozkazu=podstawa_numer_rozkazu or None,
        podstawa_data_rozkazu=podstawa_data_rozkazu or None,
    )
    d = await zarejestruj_wniosek(s, broker, wniosek)
    return RedirectResponse(f"/app/wnioski/{d.id_delegacji}", status_code=303)


def dane_wniosku(s: Session, id_delegacji: str, uzytkownik: Zolnierz) -> dict:
    d = s.get(Delegacja, id_delegacji)
    if d is None or uzytkownik.id_zolnierza not in (d.id_zolnierza, d.id_zatwierdzajacego):
        raise HTTPException(404, "Nie znaleziono wniosku")
    operacje = s.scalars(
        select(LogOperacji).where(LogOperacji.id_delegacji == id_delegacji).order_by(LogOperacji.id_operacji)
    ).all()
    decyzje: dict[int, list[LogDecyzji]] = {}
    for dec in s.scalars(select(LogDecyzji).where(LogDecyzji.id_operacji.in_([o.id_operacji for o in operacje]))):
        decyzje.setdefault(dec.id_operacji, []).append(dec)
    bledy = []
    if d.status == Status.DO_UZUPELNIENIA:
        walidacje = [
            x for o in operacje for x in decyzje.get(o.id_operacji, []) if x.typ_decyzji == "Walidacja wniosku"
        ]
        if walidacje and walidacje[-1].wybrany_wariant == "Do poprawy":
            bledy = walidacje[-1].uzasadnienie.split("; ")
    oczekiwanie = None
    odswiezaj = d.status in ETAPY_W_TOKU and (teraz() - d.data_zmiany_statusu).total_seconds() < 60
    if d.status == Status.ZLOZONY:
        oczekiwanie = "Oczekiwanie na obsługę wniosku."
    elif d.status == Status.W_AKCEPTACJI:
        oczekiwanie = "Wniosek oczekuje na decyzję przełożonego."
    elif d.status == Status.ZATWIERDZONY:
        warianty = list(s.scalars(select(Przejazd).where(Przejazd.id_delegacji == id_delegacji)))
        wynik_transportu = any(o.id_agenta == "AG-04" for o in operacje)
        if decyzja_do_uzgodnienia(s, d) or any(r.status == StatusRezerwacji.DO_UZGODNIENIA for r in warianty):
            oczekiwanie = "Podróż została przekazana do dodatkowego uzgodnienia."
            odswiezaj = False
        elif {"Tam", "Powrót"} <= {r.kierunek for r in warianty if r.status == StatusRezerwacji.WYBRANA}:
            brakujace = brakujace_elementy_organizacji(s, d)
            oczekiwanie = "Transport został wybrany."
            if brakujace:
                oczekiwanie += " Organizacja oczekuje na: " + ", ".join(brakujace) + "."
            odswiezaj = False
        elif warianty or wynik_transportu:
            porownanie = zestawienie(s, d)
            if porownanie["brak_kierunkow"] or not any(
                w["kierunek"] == "Tam" and not w["spozniony"] for w in porownanie["warianty"]
            ):
                oczekiwanie = "Brak kompletu odpowiednich przejazdów. Podróż wymaga uzgodnienia."
            else:
                oczekiwanie = "Warianty są przygotowane. Oczekiwanie na wybór podróży przez osobę akceptującą."
            odswiezaj = False
        else:
            oczekiwanie = "Oczekiwanie na przygotowanie wariantów podróży."
    return dict(
        d=d,
        wnioskodawca=s.get(Zolnierz, d.id_zolnierza),
        zatwierdzajacy=s.get(Zolnierz, d.id_zatwierdzajacego) if d.id_zatwierdzajacego else None,
        przedsiewziecie=s.get(Przedsiewziecie, d.id_przedsiewziecia) if d.id_przedsiewziecia else None,
        operacje=operacje,
        decyzje=decyzje,
        agenci={a.id_agenta: a.nazwa for a in s.scalars(select(Agent))},
        bledy=bledy,
        w_toku=odswiezaj,
        oczekiwanie=oczekiwanie,
    )


@router.get("/wnioski/{id_delegacji}", response_class=HTMLResponse)
def wniosek(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    return strona(request, s, "wniosek.html", uzytkownik, **dane_wniosku(s, id_delegacji, uzytkownik))


@router.get("/wnioski/{id_delegacji}/stan", response_class=HTMLResponse)
def stan_wniosku(
    id_delegacji: str,
    request: Request,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    """Fragment strony odświeżany przez HTMX, dopóki agenci pracują nad wnioskiem."""
    return szablony.TemplateResponse(
        request, "_stan_wniosku.html", {**dane_wniosku(s, id_delegacji, uzytkownik), "uzytkownik": uzytkownik}
    )


# ---------------------------------------------------------------- rozliczenie


def dane_rozliczenia(s: Session, id_delegacji: str, uzytkownik: Zolnierz) -> dict:
    d = s.get(Delegacja, id_delegacji)
    if d is None or d.id_zolnierza != uzytkownik.id_zolnierza:
        raise HTTPException(404, "Nie znaleziono delegacji")
    if d.status != Status.DO_ROZLICZENIA:
        raise HTTPException(409, "Delegacja nie kwalifikuje się do rozliczenia")
    rozliczenie = s.scalar(select(Rozliczenie).where(Rozliczenie.id_delegacji == id_delegacji))
    dokumenty = []
    if rozliczenie is not None:
        dokumenty = s.scalars(
            select(DokumentKosztowy)
            .where(DokumentKosztowy.id_rozliczenia == rozliczenie.id_rozliczenia)
            .order_by(DokumentKosztowy.id_dokumentu)
        ).all()
    return {"d": d, "rozliczenie": rozliczenie, "dokumenty": dokumenty}


@router.get("/wnioski/{id_delegacji}/rozliczenie", response_class=HTMLResponse)
def rozliczenie(
    id_delegacji: str,
    request: Request,
    wynik: str | None = None,
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    komunikat = KOMUNIKAT_PRZYJETY if wynik == WYNIK_DOKUMENT_PRZYJETY else None
    return strona(
        request,
        s,
        "rozliczenie.html",
        uzytkownik,
        **dane_rozliczenia(s, id_delegacji, uzytkownik),
        komunikat=komunikat,
    )


@router.post("/wnioski/{id_delegacji}/rozliczenie/dokumenty", response_class=HTMLResponse)
def dodaj_dokument_w_interfejsie(
    id_delegacji: str,
    request: Request,
    rodzaj: str = Form(...),
    numer: str = Form(...),
    data: str = Form(...),
    kwota: str = Form(...),
    s: Session = Depends(get_sesja),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    kontekst = dane_rozliczenia(s, id_delegacji, uzytkownik)
    try:
        dane = DokumentKosztowyIn(rodzaj=rodzaj, numer=numer, data=data, kwota=kwota)
    except ValidationError:
        return strona(
            request,
            s,
            "rozliczenie.html",
            uzytkownik,
            **kontekst,
            komunikat="Sprawdź rodzaj, numer, datę i dodatnią kwotę dokumentu.",
            blad=True,
        )
    wynik = przyjmij_dokument(s, kontekst["d"], dane)
    if wynik.przyjeto:
        return RedirectResponse(
            f"/app/wnioski/{id_delegacji}/rozliczenie?wynik={WYNIK_DOKUMENT_PRZYJETY}", status_code=303
        )
    return strona(
        request,
        s,
        "rozliczenie.html",
        uzytkownik,
        **dane_rozliczenia(s, id_delegacji, uzytkownik),
        komunikat=wynik.komunikat,
        blad=not wynik.przyjeto,
    )


# ---------------------------------------------------------------- przełożony


@router.get("/do-zatwierdzenia", response_class=HTMLResponse)
def do_zatwierdzenia(
    request: Request, s: Session = Depends(get_sesja), uzytkownik: Zolnierz = Depends(biezacy_uzytkownik)
):
    wnioski = s.scalars(
        select(Delegacja)
        .where(Delegacja.status == Status.W_AKCEPTACJI, Delegacja.id_zatwierdzajacego == uzytkownik.id_zolnierza)
        .order_by(Delegacja.data_zmiany_statusu)
    ).all()
    wnioskodawcy = {z.id_zolnierza: z for z in s.scalars(select(Zolnierz))}
    return strona(request, s, "do_zatwierdzenia.html", uzytkownik, wnioski=wnioski, wnioskodawcy=wnioskodawcy)


@router.post("/do-zatwierdzenia/{id_delegacji}", response_class=HTMLResponse)
async def decyduj(
    id_delegacji: str,
    request: Request,
    decyzja: str = Form(...),
    uzasadnienie: str = Form(""),
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    uzytkownik: Zolnierz = Depends(biezacy_uzytkownik),
):
    if decyzja not in ("Zgoda", "Odmowa", "Do uzupełnienia"):
        raise HTTPException(400, "Nieznana decyzja")
    d = s.get(Delegacja, id_delegacji)
    if d is None or d.id_zatwierdzajacego != uzytkownik.id_zolnierza:
        raise HTTPException(404, "Nie znaleziono wniosku")
    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    if decyzja != "Zgoda" and not uzasadnienie.strip():
        return szablony.TemplateResponse(
            request,
            "_wiersz_decyzji.html",
            {"d": d, "wnioskodawca": wnioskodawca, "blad": "Podaj uzasadnienie odmowy lub zwrotu do uzupełnienia"},
        )
    dane = {"id_osoby": uzytkownik.id_zolnierza, "decyzja": decyzja, "uzasadnienie": uzasadnienie.strip() or None}
    await broker.publikuj(Komunikat(typ=Typ.DECYZJA_PRZELOZONEGO, id_delegacji=id_delegacji, dane=dane))
    return szablony.TemplateResponse(
        request, "_wiersz_decyzji.html", {"d": d, "wnioskodawca": wnioskodawca, "przekazano": decyzja}
    )
