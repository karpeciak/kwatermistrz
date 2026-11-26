"""Wnioski – S22IT1S0P2-33 (rejestracja), -37 (wniosek roboczy), -32 (status i miejsce oczekiwania).

Do zrobienia w sprincie 1:
- S22IT1S0P2-33: dane wnioskodawcy z ewidencji (`Zolnierz`, `Jednostka`) w odpowiedzi, podstawa wyjazdu
  (`podstawa_numer_rozkazu`, `podstawa_data_rozkazu`), preferencje (`srodek_transportu`, `potrzeba_zakwaterowania`),
  podsumowanie przed złożeniem (walidacja bez rejestracji – `walidacja.waliduj_wniosek`).
- S22IT1S0P2-37: zapis wniosku roboczego (`Status.ROBOCZY`, `etap_przygotowania`, `data_ostatniej_zmiany`,
  `data_zlozenia` pusta), wznowienie, rezygnacja, złożenie wersji roboczej; widoczny tylko dla wnioskodawcy.
- S22IT1S0P2-32: lista wniosków wnioskodawcy, etap, osoba odpowiedzialna (`id_zatwierdzajacego`), czas oczekiwania
  (`data_zmiany_statusu`), historia (log operacji), informacja o przypomnieniu / przekazaniu zastępcy.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import teraz
from ..messaging import Broker, Komunikat, Typ
from ..models import Delegacja, LogDecyzji, LogOperacji, Przedsiewziecie, Status, Zolnierz
from ..przynaleznosc import jednostka_na_dzien
from ..walidacja import parsuj_date
from .schemas import DecyzjaAgentaOut, DelegacjaOut, OperacjaOut, WniosekIn
from .zaleznosci import get_broker, get_sesja, pobierz_delegacje

router = APIRouter(tags=["Wnioski"])

OGRANICZENIE_KLUCZA_IDEMPOTENCJI = "delegacja_klucz_idempotencji_key"


def _zlozony_wniosek_po_kluczu(s: Session, klucz: str) -> Delegacja | None:
    return s.scalar(
        select(Delegacja).where(
            Delegacja.klucz_idempotencji == klucz,
            Delegacja.status != Status.ROBOCZY,
        )
    )


def _ten_sam_wniosek(d: Delegacja, wniosek: WniosekIn) -> bool:
    return (
        d.id_zolnierza == wniosek.id_zolnierza
        and d.id_przedsiewziecia == wniosek.id_przedsiewziecia
        and d.dane_wniosku == wniosek.model_dump(exclude={"id_zolnierza", "id_przedsiewziecia"})
    )


def _zweryfikuj_ponowienie(d: Delegacja, wniosek: WniosekIn) -> Delegacja:
    if not _ten_sam_wniosek(d, wniosek):
        raise HTTPException(409, "Klucz idempotencji został już użyty z innymi danymi wniosku")
    return d


def _konflikt_klucza_idempotencji(exc: IntegrityError) -> bool:
    """Rozpoznaje wyłącznie konflikt unikalnego klucza w SQLite i PostgreSQL."""
    diagnostyka = getattr(exc.orig, "diag", None)
    nazwa_ograniczenia = getattr(diagnostyka, "constraint_name", None)
    if nazwa_ograniczenia == OGRANICZENIE_KLUCZA_IDEMPOTENCJI:
        return True
    return "UNIQUE constraint failed: delegacja.klucz_idempotencji" in str(exc.orig)


async def zarejestruj_wniosek(s: Session, broker: Broker, wniosek: WniosekIn, klucz: str | None = None) -> Delegacja:
    """Rejestruje złożony wniosek i przekazuje go agentom – wspólne dla API i interfejsu (`web/`)."""
    # S22IT1S0P2-22: ponowienie po utracie połączenia nie tworzy drugiego wniosku
    if klucz:
        istniejaca = _zlozony_wniosek_po_kluczu(s, klucz)
        if istniejaca:
            return _zweryfikuj_ponowienie(istniejaca, wniosek)
    if s.get(Zolnierz, wniosek.id_zolnierza) is None:
        raise HTTPException(404, f"Nie znaleziono żołnierza {wniosek.id_zolnierza}")
    if wniosek.id_przedsiewziecia and s.get(Przedsiewziecie, wniosek.id_przedsiewziecia) is None:
        raise HTTPException(404, f"Nie znaleziono przedsięwzięcia {wniosek.id_przedsiewziecia}")

    usun_wersje_robocza(s, wniosek.id_zolnierza)  # S22IT1S0P2-37: wersja niezłożona znika
    dzien_wyjazdu = parsuj_date(wniosek.data_wyjazdu) if wniosek.data_wyjazdu else teraz().date()
    jednostka = jednostka_na_dzien(s, wniosek.id_zolnierza, dzien_wyjazdu or teraz().date())
    d = Delegacja(
        id_delegacji=f"DEL-{teraz():%Y}-{uuid.uuid4().hex[:6].upper()}",
        id_zolnierza=wniosek.id_zolnierza,
        id_jednostki=jednostka.id_jednostki if jednostka else None,
        id_przedsiewziecia=wniosek.id_przedsiewziecia,
        dane_wniosku=wniosek.model_dump(exclude={"id_zolnierza", "id_przedsiewziecia"}),
        potrzeba_zakwaterowania=wniosek.potrzeba_zakwaterowania,
        status=Status.ZLOZONY,
        data_zlozenia=teraz(),
        klucz_idempotencji=klucz,
    )
    s.add(d)
    try:
        s.commit()
    except IntegrityError as exc:
        if not klucz or not _konflikt_klucza_idempotencji(exc):
            raise
        s.rollback()
        istniejaca = _zlozony_wniosek_po_kluczu(s, klucz)
        if istniejaca is None:
            raise
        return _zweryfikuj_ponowienie(istniejaca, wniosek)
    await broker.publikuj(Komunikat(typ=Typ.WNIOSEK_ZLOZONY, id_delegacji=d.id_delegacji))
    return d


@router.post("/wnioski", status_code=202, response_model=DelegacjaOut)
async def zloz_wniosek(
    wniosek: WniosekIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
    idempotency_key: str | None = Header(None, max_length=64),
):
    return await zarejestruj_wniosek(s, broker, wniosek, idempotency_key)


@router.get("/delegacje/{id_delegacji}", response_model=DelegacjaOut)
def pokaz_delegacje(id_delegacji: str, s: Session = Depends(get_sesja)):
    return pobierz_delegacje(s, id_delegacji)


@router.get("/delegacje/{id_delegacji}/przebieg", response_model=list[OperacjaOut])
def przebieg_delegacji(id_delegacji: str, s: Session = Depends(get_sesja)):
    """Log operacji i decyzji agentów dla jednej delegacji – ślad audytowy procesu."""
    pobierz_delegacje(s, id_delegacji)
    operacje = s.scalars(
        select(LogOperacji).where(LogOperacji.id_delegacji == id_delegacji).order_by(LogOperacji.id_operacji)
    ).all()
    decyzje: dict[int, list[LogDecyzji]] = {}
    for d in s.scalars(select(LogDecyzji).where(LogDecyzji.id_operacji.in_([o.id_operacji for o in operacje]))):
        decyzje.setdefault(d.id_operacji, []).append(d)
    return [
        OperacjaOut.model_validate(o).model_copy(
            update={"decyzje": [DecyzjaAgentaOut.model_validate(d) for d in decyzje.get(o.id_operacji, [])]}
        )
        for o in operacje
    ]


# ---------------------------------------------------------------- S22IT1S0P2-37: wersja robocza wniosku

ETAPY_PRZYGOTOWANIA = ("dane wnioskodawcy", "cel i termin", "preferencje", "podsumowanie")


def etap_przygotowania(dane: dict) -> str:
    """Etap, na którym wnioskodawca przerwał pracę – z tego, które dane już podał."""
    cel_i_termin = [dane.get(pole) for pole in ("cel_wyjazdu", "miejsce_docelowe", "data_wyjazdu", "data_powrotu")]
    if all(cel_i_termin):
        return "preferencje"
    if any(cel_i_termin) or dane.get("id_przedsiewziecia"):
        return "cel i termin"
    return "dane wnioskodawcy"


def wersja_robocza(s: Session, id_zolnierza: str) -> Delegacja | None:
    """Niezłożony wniosek – dostępny wyłącznie dla wnioskodawcy."""
    return s.scalar(
        select(Delegacja)
        .where(Delegacja.id_zolnierza == id_zolnierza, Delegacja.status == Status.ROBOCZY)
        .order_by(Delegacja.data_ostatniej_zmiany.desc())
        .limit(1)
    )


def zapisz_wersje_robocza(s: Session, id_zolnierza: str, dane: dict, etap: str) -> Delegacja:
    """Zachowuje podane dane jako wniosek niezłożony (jedna wersja robocza na wnioskodawcę)."""
    d = wersja_robocza(s, id_zolnierza)
    if d is None:
        jednostka = jednostka_na_dzien(s, id_zolnierza, teraz().date())
        d = Delegacja(
            id_delegacji=f"ROB-{uuid.uuid4().hex[:8].upper()}",
            id_zolnierza=id_zolnierza,
            id_jednostki=jednostka.id_jednostki if jednostka else None,
            status=Status.ROBOCZY,
            data_zlozenia=None,
        )
        s.add(d)
    id_przedsiewziecia = dane.get("id_przedsiewziecia") or None
    if id_przedsiewziecia and s.get(Przedsiewziecie, id_przedsiewziecia) is None:
        id_przedsiewziecia = None
    d.id_przedsiewziecia = id_przedsiewziecia
    d.dane_wniosku = dict(dane)
    d.potrzeba_zakwaterowania = bool(dane.get("potrzeba_zakwaterowania", True))
    d.etap_przygotowania = etap
    d.data_ostatniej_zmiany = teraz()
    s.commit()
    return d


def usun_wersje_robocza(s: Session, id_zolnierza: str) -> bool:
    """Usuwa niezłożony wniosek – po złożeniu wniosku albo po rezygnacji wnioskodawcy."""
    robocze = s.scalars(
        select(Delegacja).where(Delegacja.id_zolnierza == id_zolnierza, Delegacja.status == Status.ROBOCZY)
    ).all()
    for d in robocze:
        s.delete(d)
    s.flush()
    return bool(robocze)


class WersjaRoboczaIn(BaseModel):
    dane: dict = Field(default_factory=dict, examples=[{"cel_wyjazdu": "szkolenie", "miejsce_docelowe": "Toruń"}])
    etap_przygotowania: Literal["dane wnioskodawcy", "cel i termin", "preferencje", "podsumowanie"] | None = None


def wersja_robocza_out(d: Delegacja) -> dict:
    return {
        "id_delegacji": d.id_delegacji,
        "id_zolnierza": d.id_zolnierza,
        "data_ostatniej_zmiany": d.data_ostatniej_zmiany,
        "etap_przygotowania": d.etap_przygotowania,
        "dane": d.dane_wniosku,
    }


@router.get("/wnioskodawcy/{id_zolnierza}/wersja-robocza")
def pokaz_wersje_robocza(id_zolnierza: str, s: Session = Depends(get_sesja)):
    """Niezłożony wniosek z zachowanymi danymi – do wznowienia przygotowania (S22IT1S0P2-37)."""
    d = wersja_robocza(s, id_zolnierza)
    if d is None:
        raise HTTPException(404, "Brak niezłożonego wniosku")
    return wersja_robocza_out(d)


@router.put("/wnioskodawcy/{id_zolnierza}/wersja-robocza")
def zapisz_wersje_robocza_api(id_zolnierza: str, dane: WersjaRoboczaIn, s: Session = Depends(get_sesja)):
    """Zapis (autozapis) danych rozpoczętego wniosku jako wersji roboczej."""
    if s.get(Zolnierz, id_zolnierza) is None:
        raise HTTPException(404, f"Nie znaleziono żołnierza {id_zolnierza}")
    etap = dane.etap_przygotowania or etap_przygotowania(dane.dane)
    return wersja_robocza_out(zapisz_wersje_robocza(s, id_zolnierza, dane.dane, etap))


@router.delete("/wnioskodawcy/{id_zolnierza}/wersja-robocza")
def usun_wersje_robocza_api(id_zolnierza: str, s: Session = Depends(get_sesja)):
    """Rezygnacja z niezłożonego wniosku – usuwa zachowane dane."""
    if not usun_wersje_robocza(s, id_zolnierza):
        raise HTTPException(404, "Brak niezłożonego wniosku")
    s.commit()
    return {"usunieto": True}
