"""Koordynator – prowadzi delegację przez kolejne etapy (maszyna stanów) i zleca zadania agentom.

złożony ─▶ (walidacja) ─▶ w akceptacji ─▶ zatwierdzony ─▶ zorganizowany ─▶ w trakcie ─▶ do rozliczenia ─▶ rozliczony
    └▶ do uzupełnienia          └▶ odrzucony / do uzupełnienia

Po zatwierdzeniu Koordynator zleca równolegle: polecenie wyjazdu (S22IT1S0P2-38), transport (-27)
i zakwaterowanie (-39, gdy potrzebne). Delegacja jest „zorganizowana”, gdy wszystkie te elementy są gotowe.
Przejścia „w trakcie” i „do rozliczenia” wynikają z dat wyjazdu i powrotu (zadanie cykliczne).
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..db import sesja, teraz
from ..messaging import Komunikat, Typ
from ..models import (
    Delegacja,
    LogDecyzji,
    LogOperacji,
    PolecenieWyjazdu,
    Przejazd,
    Rozliczenie,
    Status,
    StatusRezerwacji,
    StatusRozliczenia,
    Zakwaterowanie,
    Zolnierz,
)
from .base import Agent, Kontekst, pobierz_delegacje

KONTROLA_STATUSU = "cykl.status_delegacji"
DECYZJA_SYNCHRONIZACJI = "Synchronizacja statusu delegacji"
DECYZJA_ROZBIEZNOSCI = "Rozbieżność statusu delegacji"

STATUSY_PRZED_DECYZJA = {Status.ZLOZONY, Status.W_AKCEPTACJI, Status.DO_UZUPELNIENIA}
STATUSY_ROZLICZENIA_PRZEKAZANEGO = {
    StatusRozliczenia.PRZEKAZANE,
    StatusRozliczenia.PRZEKAZANE_Z_ZASTRZEZENIAMI,
}


@dataclass(frozen=True)
class FaktyStatusu:
    id_delegacji: str
    status: str
    data_wyjazdu: date | None
    data_powrotu: date | None
    ma_polecenie: bool
    wybrane_kierunki: tuple[str, ...]
    potrzeba_zakwaterowania: bool
    ma_zakwaterowanie: bool
    status_rozliczenia: str | None

    @property
    def brakujace_elementy_organizacji(self) -> tuple[str, ...]:
        brakuje = []
        if not self.ma_polecenie:
            brakuje.append("polecenie wyjazdu")
        if not {"Tam", "Powrót"} <= set(self.wybrane_kierunki):
            brakuje.append("przejazd")
        if self.potrzeba_zakwaterowania and not self.ma_zakwaterowanie:
            brakuje.append("zakwaterowanie")
        return tuple(brakuje)

    @property
    def ma_fakty_po_zatwierdzeniu(self) -> bool:
        return bool(
            self.ma_polecenie or self.wybrane_kierunki or self.ma_zakwaterowanie or self.status_rozliczenia is not None
        )

    def do_logu(self) -> dict:
        dane = asdict(self)
        dane["data_wyjazdu"] = self.data_wyjazdu.isoformat() if self.data_wyjazdu else None
        dane["data_powrotu"] = self.data_powrotu.isoformat() if self.data_powrotu else None
        return dane


@dataclass(frozen=True)
class KwalifikacjaStatusu:
    nowy_status: str | None = None
    powod: str = "Brak bezpiecznej zmiany"
    rozbieznosc: bool = False


def kwalifikuj_status(fakty: FaktyStatusu, dzis: date) -> KwalifikacjaStatusu:
    """Wybiera najwyżej jedno bezpieczne przejście na podstawie bieżącego statusu i faktów modułów."""
    status = fakty.status
    brakuje = fakty.brakujace_elementy_organizacji

    if status == Status.ROBOCZY:
        return KwalifikacjaStatusu(powod="Wersja robocza nie podlega synchronizacji")
    if status in STATUSY_PRZED_DECYZJA:
        if fakty.ma_fakty_po_zatwierdzeniu:
            return KwalifikacjaStatusu(
                powod="Fakty organizacji lub rozliczenia istnieją przed decyzją przełożonego",
                rozbieznosc=True,
            )
        return KwalifikacjaStatusu(powod="Oczekiwanie na zakończenie etapu wniosku i zatwierdzenia")
    if status == Status.ODRZUCONY:
        return KwalifikacjaStatusu(
            powod=(
                "Odrzucona delegacja ma fakty organizacji lub rozliczenia"
                if fakty.ma_fakty_po_zatwierdzeniu
                else "Odrzucona delegacja jest statusem końcowym"
            ),
            rozbieznosc=fakty.ma_fakty_po_zatwierdzeniu,
        )
    if status == Status.ROZLICZONY:
        przekazane = fakty.status_rozliczenia in STATUSY_ROZLICZENIA_PRZEKAZANEGO
        return KwalifikacjaStatusu(
            powod=(
                "Rozliczenie zostało przekazane"
                if przekazane
                else "Status rozliczony nie ma potwierdzenia przekazania rozliczenia"
            ),
            rozbieznosc=not przekazane,
        )
    if status == Status.ZATWIERDZONY:
        if not brakuje:
            return KwalifikacjaStatusu(Status.ZORGANIZOWANY, "Kompletna organizacja wyjazdu")
        return KwalifikacjaStatusu(powod="Oczekiwanie na: " + ", ".join(brakuje))
    if status == Status.ZORGANIZOWANY:
        if brakuje:
            return KwalifikacjaStatusu(
                powod="Status zorganizowany jest sprzeczny z brakami: " + ", ".join(brakuje),
                rozbieznosc=True,
            )
        if fakty.data_wyjazdu is None or fakty.data_powrotu is None:
            return KwalifikacjaStatusu(powod="Brak dat wymaganych dla zorganizowanej delegacji", rozbieznosc=True)
        if fakty.data_powrotu < fakty.data_wyjazdu:
            return KwalifikacjaStatusu(powod="Data powrotu jest wcześniejsza od daty wyjazdu", rozbieznosc=True)
        if fakty.data_wyjazdu <= dzis:
            return KwalifikacjaStatusu(Status.W_TRAKCIE, "Nadeszła data wyjazdu")
        return KwalifikacjaStatusu(powod="Oczekiwanie na datę wyjazdu")
    if status == Status.W_TRAKCIE:
        if fakty.data_wyjazdu is None or fakty.data_powrotu is None:
            return KwalifikacjaStatusu(powod="Brak dat wymaganych dla trwającej delegacji", rozbieznosc=True)
        if fakty.data_powrotu < fakty.data_wyjazdu or fakty.data_wyjazdu > dzis:
            return KwalifikacjaStatusu(powod="Daty są sprzeczne ze statusem w trakcie", rozbieznosc=True)
        if fakty.data_powrotu < dzis:
            return KwalifikacjaStatusu(Status.DO_ROZLICZENIA, "Minęła data powrotu")
        return KwalifikacjaStatusu(powod="Delegacja nadal trwa")
    if status == Status.DO_ROZLICZENIA:
        if fakty.status_rozliczenia in STATUSY_ROZLICZENIA_PRZEKAZANEGO:
            return KwalifikacjaStatusu(Status.ROZLICZONY, "Rozliczenie przekazano do finansów")
        return KwalifikacjaStatusu(powod="Oczekiwanie na przygotowanie i przekazanie rozliczenia")
    return KwalifikacjaStatusu(powod=f"Nieznany status delegacji: {status}", rozbieznosc=True)


def pobierz_fakty_statusu(s: Session, d: Delegacja) -> FaktyStatusu:
    ma_polecenie = (
        s.scalar(select(PolecenieWyjazdu.numer).where(PolecenieWyjazdu.id_delegacji == d.id_delegacji)) is not None
    )
    wybrane_kierunki = tuple(
        sorted(
            set(
                s.scalars(
                    select(Przejazd.kierunek).where(
                        Przejazd.id_delegacji == d.id_delegacji,
                        Przejazd.status == StatusRezerwacji.WYBRANA,
                    )
                )
            )
        )
    )
    ma_zakwaterowanie = (
        s.scalar(
            select(Zakwaterowanie.id_zakwaterowania).where(
                Zakwaterowanie.id_delegacji == d.id_delegacji,
                Zakwaterowanie.status == StatusRezerwacji.WYBRANA,
            )
        )
        is not None
    )
    status_rozliczenia = s.scalar(select(Rozliczenie.status).where(Rozliczenie.id_delegacji == d.id_delegacji))
    return FaktyStatusu(
        id_delegacji=d.id_delegacji,
        status=d.status,
        data_wyjazdu=d.data_wyjazdu,
        data_powrotu=d.data_powrotu,
        ma_polecenie=ma_polecenie,
        wybrane_kierunki=wybrane_kierunki,
        potrzeba_zakwaterowania=d.potrzeba_zakwaterowania,
        ma_zakwaterowanie=ma_zakwaterowanie,
        status_rozliczenia=status_rozliczenia,
    )


def odcisk_rozbieznosci(fakty: FaktyStatusu, powod: str) -> str:
    tresc = json.dumps({"fakty": fakty.do_logu(), "powod": powod}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(tresc.encode()).hexdigest()


def rozbieznosc_juz_zgloszona(s: Session, id_delegacji: str, odcisk: str) -> bool:
    return (
        s.scalar(
            select(LogDecyzji.id_decyzji)
            .join(LogOperacji, LogOperacji.id_operacji == LogDecyzji.id_operacji)
            .where(
                LogOperacji.id_delegacji == id_delegacji,
                LogDecyzji.typ_decyzji == DECYZJA_ROZBIEZNOSCI,
                LogDecyzji.wybrany_wariant == odcisk,
            )
            .limit(1)
        )
        is not None
    )


class Koordynator(Agent):
    id_agenta = "AG-01"
    nazwa = "koordynator"
    etap = "Koordynacja"
    wersja_modelu = "orch-0.2"
    interwal_cyklu = 300

    def obslugi(self):
        return {
            Typ.WNIOSEK_ZLOZONY: (self.rozpocznij, "Rozpoczęcie procesu delegacji"),
            Typ.WYNIK_WALIDACJI: (self.po_walidacji, "Przejście po walidacji wniosku"),
            Typ.WYNIK_ZATWIERDZENIA: (self.po_zatwierdzeniu, "Przejście po decyzji przełożonego"),
            Typ.WYNIK_POLECENIA: (self.po_elemencie_organizacji, "Sporządzono polecenie wyjazdu"),
            Typ.WYNIK_TRANSPORTU: (self.po_elemencie_organizacji, "Wybrano przejazd"),
            Typ.WYNIK_ZAKWATEROWANIA: (self.po_elemencie_organizacji, "Wybrano zakwaterowanie"),
            Typ.WYNIK_PRZEKAZANIA: (self.po_przekazaniu_rozliczenia, "Rozliczenie przekazane do finansów"),
        }

    # ------------------------------------------------------------ wniosek i zatwierdzenie

    def rozpocznij(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        pobierz_delegacje(s, k.id_delegacji)
        ktx.wyslij(Typ.ZADANIE_WALIDACJI)

    def po_walidacji(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        if k.dane["poprawny"]:
            d.ustaw_status(Status.W_AKCEPTACJI)
            ktx.wyslij(Typ.ZADANIE_ZATWIERDZENIA)
        else:
            d.ustaw_status(Status.DO_UZUPELNIENIA)
            ktx.ostrzezenie(f"Wniosek zwrócony do uzupełnienia – błędy: {len(k.dane['bledy'])}")
            ktx.wyslij(
                Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": "Uzupełnij wniosek", "bledy": k.dane["bledy"]}
            )

    def po_zatwierdzeniu(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        decyzja = k.dane["decyzja"]
        if decyzja == "Zgoda":
            d.ustaw_status(Status.ZATWIERDZONY)
            ktx.wyslij(Typ.ZADANIE_POLECENIA)
            ktx.wyslij(Typ.ZADANIE_TRANSPORTU)
            if d.potrzeba_zakwaterowania:
                ktx.wyslij(Typ.ZADANIE_ZAKWATEROWANIA)
        elif decyzja == "Do uzupełnienia":
            d.ustaw_status(Status.DO_UZUPELNIENIA)
        else:
            d.ustaw_status(Status.ODRZUCONY)
        ktx.opis = f"{d.status} – decyzja {k.dane['id_osoby']}"
        # S22IT1S0P2-13 (8a): przy odrzuceniu i zwrocie wnioskodawca dostaje też uzasadnienie decyzji
        tresc = f"Wniosek {d.id_delegacji}: {d.status}"
        if decyzja != "Zgoda" and d.uzasadnienie_decyzji:
            tresc += f" – uzasadnienie: {d.uzasadnienie_decyzji}"
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": tresc, "decyzja_osoby": k.dane["id_osoby"]})

    # ------------------------------------------------------------ organizacja wyjazdu

    def po_elemencie_organizacji(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        self.synchronizuj_status(s, k, ktx)

    # ------------------------------------------------------------ realizacja i rozliczenie

    def zadania_cykliczne(self):
        zadania = []
        with sesja() as s:
            delegacje = s.scalars(
                select(Delegacja).where(Delegacja.status.not_in({Status.ROBOCZY, Status.ODRZUCONY, Status.ROZLICZONY}))
            ).all()
            dzis = teraz().date()
            for d in delegacje:
                fakty = pobierz_fakty_statusu(s, d)
                wynik = kwalifikuj_status(fakty, dzis)
                nowa_rozbieznosc = wynik.rozbieznosc and not rozbieznosc_juz_zgloszona(
                    s, d.id_delegacji, odcisk_rozbieznosci(fakty, wynik.powod)
                )
                if wynik.nowy_status or nowa_rozbieznosc:
                    zadania.append(
                        (
                            Komunikat(typ=KONTROLA_STATUSU, id_delegacji=d.id_delegacji),
                            self.synchronizuj_status,
                            "Synchronizacja statusu delegacji",
                        )
                    )
        return zadania

    def przesun_wedlug_dat(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        """Zgodność wsteczna dla istniejących wywołań kontroli dat."""
        self.synchronizuj_status(s, k, ktx)

    def synchronizuj_status(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        fakty = pobierz_fakty_statusu(s, d)
        wynik = kwalifikuj_status(fakty, teraz().date())
        if wynik.nowy_status:
            status_przed = d.status
            d.ustaw_status(wynik.nowy_status)
            ktx.opis = f"{d.id_delegacji}: status przed={status_przed}; status po={d.status}; powód={wynik.powod}"
            ktx.decyzja(
                Decyzja(
                    typ=DECYZJA_SYNCHRONIZACJI,
                    wybrany_wariant=f"{status_przed} → {d.status}",
                    uzasadnienie=ktx.opis,
                )
            )
            if d.status == Status.ZORGANIZOWANY:
                ktx.wyslij(
                    Typ.POWIADOMIENIE,
                    {"do": d.id_zolnierza, "tresc": f"Wyjazd {d.id_delegacji} zorganizowany"},
                )
            elif d.status == Status.DO_ROZLICZENIA:
                ktx.wyslij(
                    Typ.POWIADOMIENIE,
                    {"do": d.id_zolnierza, "tresc": f"Rozlicz delegację {d.id_delegacji}"},
                )
            return
        if wynik.rozbieznosc:
            self.eskaluj_rozbieznosc(s, d, fakty, wynik, ktx)
            return
        ktx.opis = f"{d.id_delegacji}: status bez zmian ({d.status}) – {wynik.powod}"

    def eskaluj_rozbieznosc(
        self,
        s: Session,
        d: Delegacja,
        fakty: FaktyStatusu,
        wynik: KwalifikacjaStatusu,
        ktx: Kontekst,
    ) -> None:
        odcisk = odcisk_rozbieznosci(fakty, wynik.powod)
        opis_faktow = json.dumps(fakty.do_logu(), ensure_ascii=False, sort_keys=True)
        wnioskodawca = s.get(Zolnierz, d.id_zolnierza)
        przelozony = wnioskodawca.id_przelozonego if wnioskodawca else None
        opis = (
            f"{d.id_delegacji}: rozbieżność statusu; status={d.status}; fakty={opis_faktow}; "
            f"powód={wynik.powod}; odcisk={odcisk}"
        )
        if not przelozony:
            opis += "; brak przełożonego – nie wysłano powiadomienia do przełożonego"
        if rozbieznosc_juz_zgloszona(s, d.id_delegacji, odcisk):
            ktx.ostrzezenie(opis + "; identyczna rozbieżność była już eskalowana")
            return

        ktx.ostrzezenie(opis)
        ktx.decyzja(
            Decyzja(
                typ=DECYZJA_ROZBIEZNOSCI,
                wybrany_wariant=odcisk,
                uzasadnienie=opis,
                # Flaga kieruje rozbieżność do weryfikacji człowieka; nie zmienia kwot ani danych rozliczenia.
                decyzja_finansowa=True,
            )
        )
        if przelozony:
            ktx.wyslij(
                Typ.POWIADOMIENIE,
                {
                    "do": przelozony,
                    "tresc": f"Status delegacji {d.id_delegacji} wymaga wyjaśnienia: {wynik.powod}",
                },
            )
        if d.status in {
            Status.ZATWIERDZONY,
            Status.ZORGANIZOWANY,
            Status.W_TRAKCIE,
            Status.DO_ROZLICZENIA,
            Status.ROZLICZONY,
        }:
            ktx.wyslij(
                Typ.POWIADOMIENIE,
                {
                    "do": d.id_zolnierza,
                    "tresc": (
                        f"Status delegacji {d.id_delegacji} jest wyjaśniany. "
                        "Twoje dane i rozliczenie pozostają bez zmian."
                    ),
                },
            )

    def po_przekazaniu_rozliczenia(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        self.synchronizuj_status(s, k, ktx)


def brakujace_elementy_organizacji(s: Session, d: Delegacja) -> list[str]:
    """Co jeszcze nie jest gotowe, żeby uznać delegację za zorganizowaną."""
    return list(pobierz_fakty_statusu(s, d).brakujace_elementy_organizacji)
