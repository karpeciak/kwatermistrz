"""Agent Zatwierdzeń – przekazanie przełożonemu, przypomnienie i eskalacja do zastępcy (S22IT1S0P2-13),
ochrona przed podwójnym zatwierdzeniem (S22IT1S0P2-14)."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import Decyzja
from ..config import settings
from ..db import sesja, teraz
from ..messaging import Komunikat, Typ
from ..models import Delegacja, Status, Zolnierz
from .base import Agent, BladProcesu, Kontekst, pobierz_delegacje

KONTROLA_TERMINU = "cykl.termin_decyzji"


class AgentZatwierdzen(Agent):
    id_agenta = "AG-03"
    nazwa = "zatwierdzenia"
    etap = "Zatwierdzenie"
    wersja_modelu = "rules-appr-1.0"
    interwal_cyklu = settings.interwal_kontroli_terminow_s

    def obslugi(self):
        return {
            Typ.ZADANIE_ZATWIERDZENIA: (self.przekaz_przelozonemu, "Przekazanie wniosku przełożonemu"),
            Typ.DECYZJA_PRZELOZONEGO: (self.zarejestruj_decyzje, "Rejestracja decyzji przełożonego"),
        }

    def przekaz_przelozonemu(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        z = s.get(Zolnierz, d.id_zolnierza)
        if z is None or not z.id_przelozonego:
            raise BladProcesu(f"Brak przełożonego dla {d.id_zolnierza} w strukturze podległości")
        d.id_zatwierdzajacego = z.id_przelozonego
        d.termin_decyzji = teraz() + timedelta(hours=settings.termin_decyzji_godz)
        ktx.opis = f"Do: {z.id_przelozonego}, termin {d.termin_decyzji:%Y-%m-%d %H:%M}"
        ktx.wyslij(Typ.POWIADOMIENIE, {"do": z.id_przelozonego, "tresc": f"Wniosek {d.id_delegacji} czeka na decyzję"})

    def zarejestruj_decyzje(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        id_osoby = k.dane["id_osoby"]
        if d.decyzja is not None:
            ktx.ostrzezenie(f"Decyzja już zarejestrowana ({d.decyzja}) – ponowne żądanie pominięte")
            return
        if id_osoby != d.id_zatwierdzajacego:
            ktx.ostrzezenie(f"{id_osoby} nie jest osobą zatwierdzającą tego wniosku")
            return
        d.decyzja = k.dane["decyzja"]
        d.data_decyzji = teraz()
        d.uzasadnienie_decyzji = k.dane.get("uzasadnienie")
        ktx.opis = f"{d.decyzja} – {id_osoby}"
        ktx.wyslij(Typ.WYNIK_ZATWIERDZENIA, {"decyzja": d.decyzja, "id_osoby": id_osoby})

    # ------------------------------------------------------------ kontrola terminów

    def zadania_cykliczne(self):
        with sesja() as s:
            ids = s.scalars(
                select(Delegacja.id_delegacji).where(
                    Delegacja.status == Status.W_AKCEPTACJI,
                    Delegacja.decyzja.is_(None),
                    Delegacja.termin_decyzji < teraz(),
                )
            ).all()
        return [
            (Komunikat(typ=KONTROLA_TERMINU, id_delegacji=i), self.pilnuj_terminu, "Kontrola terminu decyzji")
            for i in ids
        ]

    def pilnuj_terminu(self, s: Session, k: Komunikat, ktx: Kontekst) -> None:
        d = pobierz_delegacje(s, k.id_delegacji)
        if d.liczba_przypomnien == 0:
            d.liczba_przypomnien = 1
            d.termin_decyzji = teraz() + timedelta(hours=settings.termin_po_przypomnieniu_godz)
            ktx.operacja = "Przypomnienie o oczekującym wniosku"
            ktx.ostrzezenie(f"Brak decyzji {d.id_zatwierdzajacego} w terminie – wysłano przypomnienie")
            ktx.wyslij(
                Typ.POWIADOMIENIE,
                {"do": d.id_zatwierdzajacego, "tresc": f"Przypomnienie: wniosek {d.id_delegacji} czeka na decyzję"},
            )
        elif not d.przekazano_zastepcy:
            przelozony = s.get(Zolnierz, d.id_zatwierdzajacego)
            if przelozony is None or not przelozony.id_zastepcy:
                raise BladProcesu(f"Brak zastępcy dla {d.id_zatwierdzajacego} w strukturze podległości")
            d.id_zatwierdzajacego = przelozony.id_zastepcy
            d.przekazano_zastepcy = True
            d.termin_decyzji = teraz() + timedelta(hours=settings.termin_decyzji_godz)
            ktx.operacja = "Przekazanie wniosku zastępcy"
            ktx.ostrzezenie(f"Eskalacja do zastępcy {przelozony.id_zastepcy}")
            ktx.decyzja(
                Decyzja(
                    typ="Eskalacja do zastępcy",
                    wybrany_wariant=przelozony.id_zastepcy,
                    uzasadnienie="Upłynął termin decyzji i termin po przypomnieniu – reguła zastępstwa "
                    "ze struktury podległości",
                )
            )
            for odbiorca in (przelozony.id_zastepcy, d.id_zolnierza):
                ktx.wyslij(
                    Typ.POWIADOMIENIE, {"do": odbiorca, "tresc": f"Wniosek {d.id_delegacji} przekazano zastępcy"}
                )
        else:
            d.termin_decyzji = None
            ktx.ostrzezenie("Brak decyzji także u zastępcy – wymagana interwencja kwatermistrza")
