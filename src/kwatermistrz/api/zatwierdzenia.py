"""Zatwierdzanie – S22IT1S0P2-14 (jednokrotna decyzja), -35 (zbiorcze zatwierdzenie).

S22IT1S0P2-35: zestawienie wniosków oczekujących na decyzję przełożonego z danymi wnioskodawcy, szacowanym
kosztem i dostępnym budżetem jednostki (`budzet_delegacji` − `budzet_wykorzystany`). Decyzja zbiorcza wysyła
`decyzja.przelozonego` dla każdego wniosku (Agent Zatwierdzeń dba o jednokrotność – S22IT1S0P2-14).
Gdy łączny koszt zatwierdzanych wniosków przekracza dostępny budżet (3b–4b), zatwierdzenie jest wstrzymane
do decyzji komórki finansowej.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.zatwierdzenia import TYP_KONTROLI_BUDZETU
from ..messaging import Broker, Komunikat, Typ
from ..models import Delegacja, Jednostka, LogDecyzji, LogOperacji, Status, Zolnierz
from ..stawki import szacuj_koszt
from .schemas import DecyzjaIn
from .zaleznosci import get_broker, get_sesja, pobierz_delegacje

router = APIRouter(tags=["Zatwierdzanie"])

RODZAJE_DECYZJI = ("Zgoda", "Odmowa", "Do uzupełnienia")


@router.post("/delegacje/{id_delegacji}/decyzja", status_code=202)
async def decyzja_przelozonego(
    id_delegacji: str,
    decyzja: DecyzjaIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    pobierz_delegacje(s, id_delegacji)
    await broker.publikuj(Komunikat(typ=Typ.DECYZJA_PRZELOZONEGO, id_delegacji=id_delegacji, dane=decyzja.model_dump()))
    return {"przyjeto": True}


# ---------------------------------------------------------------- S22IT1S0P2-35: zbiorcze zatwierdzenie


@dataclass
class Budzet:
    przyznany: Decimal
    wykorzystany: Decimal

    @property
    def pozostaly(self) -> Decimal:
        return self.przyznany - self.wykorzystany


@dataclass
class WynikDecyzjiZbiorczej:
    decyzja: str = ""
    przekazane: list[str] = field(default_factory=list)
    wstrzymane: list[str] = field(default_factory=list)
    pominiete: list[str] = field(default_factory=list)
    koszt_laczny: Decimal = Decimal("0")
    komunikat: str = ""
    blad: bool = False


def zl(kwota: Decimal) -> str:
    return f"{kwota:.2f}".replace(".", ",") + " zł"


def koszt_delegacji(d: Delegacja) -> Decimal | None:
    """Szacowany koszt wyjazdu – zapisany przez Agenta Wniosków albo wyliczony z dat wniosku."""
    if d.szacowany_koszt is not None:
        return d.szacowany_koszt
    return szacuj_koszt(d.data_wyjazdu, d.data_powrotu, d.potrzeba_zakwaterowania)


def budzet_jednostki(s: Session, przelozony: Zolnierz) -> Budzet | None:
    """Budżet delegacji jednostki przełożonego: przyznany, wykorzystany, pozostały."""
    j = s.get(Jednostka, przelozony.id_jednostki)
    if j is None:
        return None
    # wykorzystany = zaangażowany w zatwierdzone delegacje + wydatkowany z rozliczeń (S22IT1S0P2-66)
    wykorzystany = (j.budzet_wykorzystany or Decimal("0")) + (j.budzet_wydatkowany or Decimal("0"))
    return Budzet(przyznany=j.budzet_delegacji or Decimal("0"), wykorzystany=wykorzystany)


def oczekujace_na_decyzje(s: Session, id_przelozonego: str) -> list[Delegacja]:
    return list(
        s.scalars(
            select(Delegacja)
            .where(
                Delegacja.status == Status.W_AKCEPTACJI,
                Delegacja.decyzja.is_(None),
                Delegacja.id_zatwierdzajacego == id_przelozonego,
            )
            .order_by(Delegacja.data_zmiany_statusu)
        )
    )


def wstrzymane_z_powodu_budzetu(s: Session, ids: list[str]) -> set[str]:
    """Wnioski, których zatwierdzenie czeka na decyzję komórki finansowej (S22IT1S0P2-35, 4b)."""
    if not ids:
        return set()
    return set(
        s.scalars(
            select(LogOperacji.id_delegacji)
            .join(LogDecyzji, LogDecyzji.id_operacji == LogOperacji.id_operacji)
            .where(
                LogOperacji.id_delegacji.in_(ids),
                LogDecyzji.typ_decyzji == TYP_KONTROLI_BUDZETU,
                LogDecyzji.decyzja_czlowieka.is_(None),
            )
        )
    )


async def decyzja_zbiorcza(
    s: Session,
    broker: Broker,
    przelozony: Zolnierz,
    ids: list[str],
    decyzja: str,
    uzasadnienie: str | None = None,
) -> WynikDecyzjiZbiorczej:
    """Jedna decyzja dla wielu wniosków – osobny komunikat `decyzja.przelozonego` dla każdego z nich."""
    if decyzja not in RODZAJE_DECYZJI:
        raise HTTPException(400, "Nieznana decyzja")
    wynik = WynikDecyzjiZbiorczej(decyzja=decyzja)
    uzasadnienie = (uzasadnienie or "").strip() or None
    if decyzja != "Zgoda" and not uzasadnienie:
        wynik.blad = True
        wynik.komunikat = "Podaj uzasadnienie odmowy lub zwrotu do uzupełnienia."
        return wynik

    oczekujace = {d.id_delegacji: d for d in oczekujace_na_decyzje(s, przelozony.id_zolnierza)}
    unikalne = list(dict.fromkeys(ids))
    wybrane = [oczekujace[i] for i in unikalne if i in oczekujace]
    wynik.pominiete = [i for i in unikalne if i not in oczekujace]
    if not wybrane:
        wynik.blad = True
        wynik.komunikat = (
            "Ten wniosek nie oczekuje już na Twoją decyzję."
            if len(unikalne) == 1
            else "Zaznacz co najmniej jeden wniosek oczekujący na Twoją decyzję."
        )
        return wynik

    koszty = {d.id_delegacji: koszt_delegacji(d) or Decimal("0") for d in wybrane}
    wynik.koszt_laczny = sum(koszty.values(), Decimal("0"))
    przekroczenie = None
    if decyzja == "Zgoda":
        budzet = budzet_jednostki(s, przelozony)
        if budzet is not None and wynik.koszt_laczny > budzet.pozostaly:
            przekroczenie = (
                f"Łączny koszt zatwierdzanych wniosków ({zl(wynik.koszt_laczny)}) przekracza "
                f"dostępny budżet jednostki ({zl(budzet.pozostaly)})"
            )

    for d in wybrane:
        dane = {
            "id_osoby": przelozony.id_zolnierza,
            "decyzja": decyzja,
            "uzasadnienie": uzasadnienie,
            "koszt": str(koszty[d.id_delegacji]),
        }
        if przekroczenie:
            dane["przekroczenie_budzetu"] = przekroczenie
        await broker.publikuj(Komunikat(typ=Typ.DECYZJA_PRZELOZONEGO, id_delegacji=d.id_delegacji, dane=dane))
        (wynik.wstrzymane if przekroczenie else wynik.przekazane).append(d.id_delegacji)

    if przekroczenie:
        wynik.blad = True
        wynik.komunikat = f"{przekroczenie} – zatwierdzenie wstrzymano do decyzji komórki finansowej."
    else:
        wynik.komunikat = f"Przekazano decyzję „{decyzja}” dla wniosków: {len(wynik.przekazane)}."
    return wynik


class DecyzjaZbiorczaIn(BaseModel):
    id_osoby: str
    wnioski: list[str] = Field(min_length=1)
    decyzja: Literal["Zgoda", "Odmowa", "Do uzupełnienia"]
    uzasadnienie: str | None = None


def pobierz_zolnierza(s: Session, id_osoby: str) -> Zolnierz:
    z = s.get(Zolnierz, id_osoby)
    if z is None:
        raise HTTPException(404, f"Nie znaleziono żołnierza {id_osoby}")
    return z


@router.get("/przelozeni/{id_osoby}/do-zatwierdzenia")
def zestawienie_do_zatwierdzenia(id_osoby: str, s: Session = Depends(get_sesja)):
    """Wnioski oczekujące na decyzję przełożonego z szacowanym kosztem i budżetem jednostki (S22IT1S0P2-35)."""
    przelozony = pobierz_zolnierza(s, id_osoby)
    wnioski = oczekujace_na_decyzje(s, id_osoby)
    wstrzymane = wstrzymane_z_powodu_budzetu(s, [d.id_delegacji for d in wnioski])
    budzet = budzet_jednostki(s, przelozony)
    pozycje = []
    for d in wnioski:
        z = s.get(Zolnierz, d.id_zolnierza)
        pozycje.append(
            {
                "id_delegacji": d.id_delegacji,
                "wnioskodawca": {"stopien": z.stopien, "imie_nazwisko": z.imie_nazwisko, "pododdzial": z.pododdzial},
                "cel_wyjazdu": d.cel_wyjazdu or d.dane_wniosku.get("cel_wyjazdu"),
                "miejsce_docelowe": d.miejsce_docelowe or d.dane_wniosku.get("miejsce_docelowe"),
                "data_wyjazdu": d.data_wyjazdu,
                "data_powrotu": d.data_powrotu,
                "szacowany_koszt": koszt_delegacji(d),
                "wstrzymany_budzet": d.id_delegacji in wstrzymane,
            }
        )
    return {
        "wnioski": pozycje,
        "budzet": None
        if budzet is None
        else {"przyznany": budzet.przyznany, "wykorzystany": budzet.wykorzystany, "pozostaly": budzet.pozostaly},
    }


@router.post("/decyzje-zbiorcze", status_code=202)
async def decyzja_zbiorcza_api(
    dane: DecyzjaZbiorczaIn,
    s: Session = Depends(get_sesja),
    broker: Broker = Depends(get_broker),
):
    """Jedna decyzja przełożonego dla wielu wniosków (S22IT1S0P2-35)."""
    przelozony = pobierz_zolnierza(s, dane.id_osoby)
    wynik = await decyzja_zbiorcza(s, broker, przelozony, dane.wnioski, dane.decyzja, dane.uzasadnienie)
    if wynik.blad and not wynik.wstrzymane:
        raise HTTPException(400, wynik.komunikat)
    return {
        "przekazane": wynik.przekazane,
        "wstrzymane": wynik.wstrzymane,
        "pominiete": wynik.pominiete,
        "koszt_laczny": wynik.koszt_laczny,
        "komunikat": wynik.komunikat,
    }
