"""Koordynator – prowadzi delegację przez kolejne etapy (maszyna stanów) i zleca zadania agentom.

złożony ─▶ (walidacja) ─▶ w akceptacji ─▶ zatwierdzony ─▶ zorganizowany ─▶ w trakcie ─▶ do rozliczenia ─▶ rozliczony
    └▶ do uzupełnienia          └▶ odrzucony / do uzupełnienia

Po zatwierdzeniu Koordynator zleca równolegle: polecenie wyjazdu (S22IT1S0P2-38), transport (-27)
i zakwaterowanie (-39, gdy potrzebne). Delegacja jest „zorganizowana”, gdy wszystkie te elementy są gotowe.
Przejścia „w trakcie” i „do rozliczenia” wynikają z dat wyjazdu i powrotu (zadanie cykliczne).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import sesja, teraz
from ..messaging import Komunikat, Typ
from ..models import Delegacja, PolecenieWyjazdu, Przejazd, Status, StatusRezerwacji, Zakwaterowanie
from .base import Agent, Kontekst, pobierz_delegacje

KONTROLA_DAT = "cykl.daty_delegacji"


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
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": f"Wniosek {d.id_delegacji}: {d.status}"})

    # ------------------------------------------------------------ organizacja wyjazdu

    def po_elemencie_organizacji(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        brakuje = brakujace_elementy_organizacji(s, d)
        if not brakuje and d.status == Status.ZATWIERDZONY:
            d.ustaw_status(Status.ZORGANIZOWANY)
            ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": f"Wyjazd {d.id_delegacji} zorganizowany"})
        ktx.opis = "Organizacja zakończona" if not brakuje else "Brakuje: " + ", ".join(brakuje)

    # ------------------------------------------------------------ realizacja i rozliczenie

    def zadania_cykliczne(self):
        dzis = teraz().date()
        with sesja() as s:
            ids = s.scalars(
                select(Delegacja.id_delegacji).where(
                    ((Delegacja.status == Status.ZORGANIZOWANY) & (Delegacja.data_wyjazdu <= dzis))
                    | ((Delegacja.status == Status.W_TRAKCIE) & (Delegacja.data_powrotu < dzis))
                )
            ).all()
        return [
            (Komunikat(typ=KONTROLA_DAT, id_delegacji=i), self.przesun_wedlug_dat, "Kontrola dat delegacji")
            for i in ids
        ]

    def przesun_wedlug_dat(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        dzis = teraz().date()
        if d.status == Status.ZORGANIZOWANY and d.data_wyjazdu and d.data_wyjazdu <= dzis:
            d.ustaw_status(Status.W_TRAKCIE)
        if d.status == Status.W_TRAKCIE and d.data_powrotu and d.data_powrotu < dzis:
            d.ustaw_status(Status.DO_ROZLICZENIA)
            ktx.wyslij(Typ.POWIADOMIENIE, {"do": d.id_zolnierza, "tresc": f"Rozlicz delegację {d.id_delegacji}"})
        ktx.opis = d.status

    def po_przekazaniu_rozliczenia(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        d.ustaw_status(Status.ROZLICZONY)


def brakujace_elementy_organizacji(s: Session, d: Delegacja) -> list[str]:
    """Co jeszcze nie jest gotowe, żeby uznać delegację za zorganizowaną."""
    brakuje = []
    if s.scalar(select(PolecenieWyjazdu.numer).where(PolecenieWyjazdu.id_delegacji == d.id_delegacji)) is None:
        brakuje.append("polecenie wyjazdu")
    wybrany_przejazd = select(Przejazd.id_przejazdu).where(
        Przejazd.id_delegacji == d.id_delegacji, Przejazd.status == StatusRezerwacji.WYBRANA
    )
    if s.scalar(wybrany_przejazd) is None:
        brakuje.append("przejazd")
    wybrany_nocleg = select(Zakwaterowanie.id_zakwaterowania).where(
        Zakwaterowanie.id_delegacji == d.id_delegacji, Zakwaterowanie.status == StatusRezerwacji.WYBRANA
    )
    if d.potrzeba_zakwaterowania and s.scalar(wybrany_nocleg) is None:
        brakuje.append("zakwaterowanie")
    return brakuje
