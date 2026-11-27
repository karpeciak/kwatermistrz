"""Ponowienie zlecenia i powiadomienie delegowanego o opóźnieniu realizacji – S22IT1S0P2-42 (podzadania 162–165).

Obsługę wywołuje Agent Monitorujący (AG-08):
1. Po zatwierdzeniu wniosku Koordynator zleca przygotowanie transportu (`zadanie.transport`). Cykliczna kontrola
   (`zlecenia_do_kontroli`, co minutę) wykrywa zlecenia bez odpowiedzi Agenta Transportu dłuższe niż
   `DOPUSZCZALNY_CZAS` → `kontroluj_zlecenie` (podzadanie 162):
   - przekroczony czas: zlecenie ponowione (`zadanie.transport`), `ZlecenieAgenta` ze statusem `OPOZNIONE`,
     liczbą ponowień i przewidywanym terminem odpowiedzi, powiadomienie delegowanego,
   - po `MAKS_PONOWIEN` bez odpowiedzi: zlecenie wycofane, incydent, sprawa u osoby odpowiedzialnej,
   - odpowiedź agenta po przywróceniu działania (podzadanie 164): zlecenie `ZREALIZOWANE`, informacja, że warianty
     transportu są gotowe – dalej wybór i rezerwacja jak w S22IT1S0P2-27.
2. Delegowany potwierdza oczekiwanie albo rezygnuje (`/app/wnioski/{id}`) → `decyzja.oczekiwanie_na_zlecenie`
   → `zapisz_decyzje_oczekiwania`:
   - oczekiwanie: `OCZEKIWANIE` – system ponawia dalej i informuje o kolejnych terminach,
   - rezygnacja (3a–4a): zlecenie wycofane (Agent Transportu pomija spóźnione zlecenie – `zlecenie_wycofane`),
     incydent w rejestrze zleceń i w logu operacji, sprawa u osoby odpowiedzialnej za organizację delegacji.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..db import teraz
from ..messaging import Komunikat, Typ
from ..models import Delegacja, LogOperacji, Przejazd, Status, StatusZlecenia, ZlecenieAgenta, Zolnierz
from .base import BladProcesu, Kontekst, pobierz_delegacje

DOPUSZCZALNY_CZAS = timedelta(minutes=10)  # czas na przygotowanie wariantów transportu
MAKS_PONOWIEN = 3
RODZAJ_TRANSPORT = "przygotowanie transportu"
OPERACJA_TRANSPORTU = "Przygotowanie wariantów podróży"  # odpowiedź Agenta Transportu (AG-04)
KONTROLA_ZLECEN = "kontrola.zlecenia"  # wewnętrzny typ zadania cyklicznego Agenta Monitorującego
TYP_DECYZJI = "Opóźnienie realizacji zlecenia"
W_TOKU = (StatusZlecenia.OPOZNIONE, StatusZlecenia.OCZEKIWANIE)
STREFA = ZoneInfo("Europe/Warsaw")


# ---------------------------------------------------------------- zapytania wspólne dla agenta, API i ekranu


def zlecenie_transportu(s: Session, d: Delegacja) -> ZlecenieAgenta | None:
    return s.scalar(
        select(ZlecenieAgenta)
        .where(ZlecenieAgenta.id_delegacji == d.id_delegacji, ZlecenieAgenta.rodzaj == RODZAJ_TRANSPORT)
        .order_by(ZlecenieAgenta.id_zlecenia.desc())
        .limit(1)
    )


def zlecenie_wycofane(s: Session, d: Delegacja) -> bool:
    """Agent Transportu pomija zlecenie wycofane przez delegowanego (4a)."""
    z = zlecenie_transportu(s, d)
    return z is not None and z.status == StatusZlecenia.WYCOFANE


def transport_odpowiedzial(s: Session, d: Delegacja) -> bool:
    """Agent Transportu przygotował warianty albo zakończył obsługę zlecenia (także bez wariantów)."""
    if s.scalar(select(Przejazd.id_przejazdu).where(Przejazd.id_delegacji == d.id_delegacji).limit(1)):
        return True
    return (
        s.scalar(
            select(LogOperacji.id_operacji)
            .where(
                LogOperacji.id_delegacji == d.id_delegacji,
                LogOperacji.id_agenta == "AG-04",
                LogOperacji.operacja == OPERACJA_TRANSPORTU,
                LogOperacji.wynik != "Błąd",
            )
            .limit(1)
        )
        is not None
    )


def wymaga_kontroli(s: Session, d: Delegacja, chwila: datetime) -> bool:
    z = zlecenie_transportu(s, d)
    if z is not None and z.status in W_TOKU:
        return transport_odpowiedzial(s, d) or z.przewidywany_termin <= chwila
    if z is not None or d.status != Status.ZATWIERDZONY:
        return False
    return d.data_zmiany_statusu + DOPUSZCZALNY_CZAS <= chwila and not transport_odpowiedzial(s, d)


def zlecenia_do_kontroli(s: Session, chwila: datetime) -> list[str]:
    """Delegacje, w których zlecenie transportu przekroczyło czas albo właśnie zostało zrealizowane."""
    kandydaci = set(
        s.scalars(
            select(Delegacja.id_delegacji).where(
                Delegacja.status == Status.ZATWIERDZONY, Delegacja.data_zmiany_statusu <= chwila - DOPUSZCZALNY_CZAS
            )
        )
    ) | set(s.scalars(select(ZlecenieAgenta.id_delegacji).where(ZlecenieAgenta.status.in_(W_TOKU))))
    return sorted(i for i in kandydaci if wymaga_kontroli(s, s.get(Delegacja, i), chwila))


def osoba_odpowiedzialna(s: Session, d: Delegacja) -> str | None:
    wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
    return d.id_zatwierdzajacego or (wnioskodawca.id_przelozonego if wnioskodawca else None)


def _godzina(chwila: datetime) -> str:
    """Czas w strefie użytkowników (jak na ekranach) – w bazie przechowujemy UTC."""
    return chwila.replace(tzinfo=UTC).astimezone(STREFA).strftime("%d.%m.%Y %H:%M")


def _wycofaj(s: Session, d: Delegacja, z: ZlecenieAgenta, ktx: Kontekst, incydent: str) -> None:
    """4a: wycofanie zlecenia, incydent i przekazanie sprawy osobie odpowiedzialnej."""
    z.status = StatusZlecenia.WYCOFANE
    z.data_zakonczenia = teraz()
    z.incydent = incydent
    ktx.ostrzezenie(f"Incydent: {incydent} – zlecenie wycofane, sprawę przekazano osobie odpowiedzialnej")
    ktx.decyzja(Decyzja(typ=TYP_DECYZJI, wybrany_wariant="Wycofano zlecenie", uzasadnienie=incydent))
    odpowiedzialny = osoba_odpowiedzialna(s, d)
    if odpowiedzialny:
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {
                "do": odpowiedzialny,
                "tresc": f"Delegacja {d.id_delegacji}: {incydent}. Zorganizuj transport indywidualnie.",
            },
        )
    ktx.wyslij(
        Typ.POWIADOMIENIE,
        {
            "do": d.id_zolnierza,
            "tresc": f"Zlecenie przygotowania transportu delegacji {d.id_delegacji} wycofano – sprawę prowadzi "
            f"osoba odpowiedzialna za organizację{f' ({odpowiedzialny})' if odpowiedzialny else ''}",
        },
    )


# ---------------------------------------------------------------- 1. kontrola czasu realizacji (162, 164)


def kontroluj_zlecenie(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)
    chwila = teraz()
    z = zlecenie_transportu(s, d)

    if transport_odpowiedzial(s, d):
        if z is None or z.status not in W_TOKU:
            ktx.opis = "Agent Transportu odpowiedział – kontrola nie jest potrzebna"
            return
        # podzadanie 164: realizacja po przywróceniu działania
        z.status = StatusZlecenia.ZREALIZOWANE
        z.data_zakonczenia = chwila
        ktx.opis = f"Zlecenie zrealizowane po {z.liczba_ponowien} ponowieniu(-ach) – warianty transportu gotowe"
        ktx.wyslij(
            Typ.POWIADOMIENIE,
            {
                "do": d.id_zolnierza,
                "tresc": f"Delegacja {d.id_delegacji}: warianty transportu są gotowe – wybierz przejazd.",
            },
        )
        return
    if not wymaga_kontroli(s, d, chwila):
        ktx.opis = "Zlecenie w terminie – kontrola nie jest potrzebna"
        return
    if z is not None and z.liczba_ponowien >= MAKS_PONOWIEN:
        _wycofaj(s, d, z, ktx, f"brak odpowiedzi Agenta Transportu po {MAKS_PONOWIEN} ponowieniach")
        return

    # podzadanie 162: przekroczony czas – ponowienie zlecenia i przewidywany termin odpowiedzi
    if z is None:
        z = ZlecenieAgenta(
            id_delegacji=d.id_delegacji,
            rodzaj=RODZAJ_TRANSPORT,
            data_zlecenia=d.data_zmiany_statusu,
            liczba_ponowien=0,
            przewidywany_termin=chwila,
            status=StatusZlecenia.OPOZNIONE,
        )
        s.add(z)
    z.liczba_ponowien += 1
    z.przewidywany_termin = chwila + DOPUSZCZALNY_CZAS
    opoznienie = int((chwila - z.data_zlecenia) / timedelta(minutes=1))
    limit = int(DOPUSZCZALNY_CZAS / timedelta(minutes=1))
    ktx.ostrzezenie(
        f"Przekroczono czas realizacji zlecenia transportu ({opoznienie} min > {limit} min) – ponowienie "
        f"{z.liczba_ponowien}/{MAKS_PONOWIEN}, przewidywana odpowiedź {_godzina(z.przewidywany_termin)}"
    )
    ktx.wyslij(Typ.ZADANIE_TRANSPORTU)
    tresc = (
        f"Przygotowanie transportu dla delegacji {d.id_delegacji} trwa dłużej niż zwykle. Zlecenie ponowiono, "
        f"przewidywany termin odpowiedzi: {_godzina(z.przewidywany_termin)}."
    )
    if z.status == StatusZlecenia.OPOZNIONE:
        tresc += " Potwierdź oczekiwanie albo zrezygnuj – sprawę przejmie wtedy osoba odpowiedzialna."
    ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": tresc})


# ---------------------------------------------------------------- 2. decyzja delegowanego (podzadanie 163)


def zapisz_decyzje_oczekiwania(s: Session, k: Komunikat, ktx: Kontekst) -> None:
    d = pobierz_delegacje(s, k.id_delegacji)
    if k.dane.get("id_osoby") != d.id_zolnierza:
        raise BladProcesu("O oczekiwaniu na realizację zlecenia decyduje delegowany")
    z = zlecenie_transportu(s, d)
    if z is None or z.status not in W_TOKU:
        ktx.opis = "Brak opóźnionego zlecenia – decyzja nie jest potrzebna"
        return
    if k.dane.get("czekam"):
        z.status = StatusZlecenia.OCZEKIWANIE
        ktx.opis = f"Delegowany czeka na realizację zlecenia (przewidywany termin {_godzina(z.przewidywany_termin)})"
        return
    powod = str(k.dane.get("powod") or "").strip()
    incydent = "delegowany zrezygnował z oczekiwania na przygotowanie transportu" + (f": {powod}" if powod else "")
    _wycofaj(s, d, z, ktx, incydent)
