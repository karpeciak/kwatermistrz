"""Pierwszy pełny przepływ (sprint 1): wniosek → walidacja → zatwierdzenie, z logami agentów."""

import asyncio
from datetime import timedelta

from sqlalchemy import select

from kwatermistrz.agents import AGENCI
from kwatermistrz.db import sesja, teraz
from kwatermistrz.messaging import Komunikat, Typ
from kwatermistrz.models import Delegacja, LogDecyzji, LogOperacji, Status

WNIOSEK = {
    "cel_wyjazdu": "Szkolenie z logistyki wojskowej",
    "miejsce_docelowe": "Toruń",
    "data_wyjazdu": "2026-12-07",
    "data_powrotu": "2026-12-11",
    "srodek_transportu": "pociąg",
}


def utworz_delegacje(dane=WNIOSEK, **pola) -> str:
    with sesja() as s:
        d = Delegacja(id_delegacji="DEL-TEST-1", id_zolnierza="ZOL-003", dane_wniosku=dane, **pola)
        s.add(d)
    return "DEL-TEST-1"


def delegacja(id_delegacji: str) -> Delegacja:
    with sesja() as s:
        return s.get(Delegacja, id_delegacji)


def agenci(broker):
    return [klasa(broker) for klasa in AGENCI.values()]


def test_poprawny_wniosek_trafia_do_przelozonego_i_jest_zatwierdzany(broker):
    id_d = utworz_delegacje()
    zespol = agenci(broker)
    broker.wyslane.append(Komunikat(typ=Typ.WNIOSEK_ZLOZONY, id_delegacji=id_d))
    asyncio.run(broker.dostarczaj(zespol))

    d = delegacja(id_d)
    assert d.status == Status.W_AKCEPTACJI
    assert d.id_zatwierdzajacego == "ZOL-001"
    assert d.data_powrotu.isoformat() == "2026-12-11"

    broker.wyslane.append(
        Komunikat(typ=Typ.DECYZJA_PRZELOZONEGO, id_delegacji=id_d, dane={"id_osoby": "ZOL-001", "decyzja": "Zgoda"})
    )
    asyncio.run(broker.dostarczaj(zespol))
    assert delegacja(id_d).status == Status.ZATWIERDZONY

    with sesja() as s:
        operacje = s.scalars(select(LogOperacji).where(LogOperacji.id_delegacji == id_d)).all()
        agenci_w_logu = {o.id_agenta for o in operacje}
        decyzje = s.scalars(select(LogDecyzji.typ_decyzji)).all()
    # Koordynator, Wnioski (walidacja i polecenie), Zatwierdzenia, Transport i Zakwaterowanie
    assert agenci_w_logu == {"AG-01", "AG-02", "AG-03", "AG-04", "AG-05"}
    assert all(o.id_korelacji == f"COR-{id_d}" for o in operacje)
    assert "Walidacja wniosku" in decyzje


def test_niepoprawny_wniosek_wraca_do_uzupelnienia(broker):
    id_d = utworz_delegacje({**WNIOSEK, "cel_wyjazdu": ""})
    broker.wyslane.append(Komunikat(typ=Typ.WNIOSEK_ZLOZONY, id_delegacji=id_d))
    asyncio.run(broker.dostarczaj(agenci(broker)))
    assert delegacja(id_d).status == Status.DO_UZUPELNIENIA


def test_ponowna_decyzja_jest_pomijana_s14(broker):
    id_d = utworz_delegacje(status=Status.W_AKCEPTACJI, id_zatwierdzajacego="ZOL-001")
    zespol = agenci(broker)
    decyzja = {"id_osoby": "ZOL-001", "decyzja": "Zgoda"}
    for _ in range(2):
        broker.wyslane.append(Komunikat(typ=Typ.DECYZJA_PRZELOZONEGO, id_delegacji=id_d, dane=decyzja))
    asyncio.run(broker.dostarczaj(zespol))

    with sesja() as s:
        wyniki = s.scalars(
            select(LogOperacji.wynik).where(LogOperacji.operacja == "Rejestracja decyzji przełożonego")
        ).all()
    assert wyniki == ["Sukces", "Ostrzeżenie"]
    assert delegacja(id_d).status == Status.ZATWIERDZONY


def test_brak_decyzji_przypomnienie_potem_eskalacja_do_zastepcy_s13(broker):
    przeszlosc = teraz() - timedelta(minutes=1)
    id_d = utworz_delegacje(status=Status.W_AKCEPTACJI, id_zatwierdzajacego="ZOL-001", termin_decyzji=przeszlosc)
    agent = AGENCI["zatwierdzenia"](broker)

    def uplyw_terminu():
        for k, funkcja, operacja in agent.zadania_cykliczne():
            asyncio.run(agent.wykonaj(k, funkcja, operacja))

    uplyw_terminu()
    d = delegacja(id_d)
    assert d.liczba_przypomnien == 1 and d.id_zatwierdzajacego == "ZOL-001"

    with sesja() as s:
        s.get(Delegacja, id_d).termin_decyzji = przeszlosc
    uplyw_terminu()
    d = delegacja(id_d)
    assert d.przekazano_zastepcy and d.id_zatwierdzajacego == "ZOL-002"

    with sesja() as s:
        eskalacja = s.scalar(select(LogDecyzji).where(LogDecyzji.typ_decyzji == "Eskalacja do zastępcy"))
    assert eskalacja.wybrany_wariant == "ZOL-002" and eskalacja.rodzaj_decyzji == "Regułowa"
