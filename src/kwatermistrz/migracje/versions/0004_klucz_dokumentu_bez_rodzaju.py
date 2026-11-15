"""S22IT1S0P2-20: klucz dokumentu kosztowego bez rodzaju dokumentu.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15
"""

from collections import defaultdict
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from unicodedata import normalize

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

dokument_kosztowy = sa.table(
    "dokument_kosztowy",
    sa.column("id_dokumentu", sa.Integer),
    sa.column("rodzaj", sa.String),
    sa.column("numer", sa.String),
    sa.column("data", sa.Date),
    sa.column("kwota", sa.Numeric(10, 2)),
    sa.column("klucz_unikalnosci", sa.String),
)


def _tekst(wartosc: str) -> str:
    return " ".join(normalize("NFKC", wartosc).split()).casefold()


def _klucz(wiersz, uwzglednij_rodzaj: bool) -> str:
    data = wiersz.data.isoformat() if hasattr(wiersz.data, "isoformat") else str(wiersz.data)
    kwota = Decimal(str(wiersz.kwota)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    czesci = [_tekst(wiersz.numer), data, f"{kwota:.2f}"]
    if uwzglednij_rodzaj:
        czesci.insert(0, _tekst(wiersz.rodzaj))
    return "|".join(czesci)


def _przelicz_klucze(polaczenie, uwzglednij_rodzaj: bool) -> None:
    wiersze = polaczenie.execute(
        sa.select(
            dokument_kosztowy.c.id_dokumentu,
            dokument_kosztowy.c.rodzaj,
            dokument_kosztowy.c.numer,
            dokument_kosztowy.c.data,
            dokument_kosztowy.c.kwota,
            dokument_kosztowy.c.klucz_unikalnosci,
        ).order_by(dokument_kosztowy.c.id_dokumentu)
    ).all()
    nowe_klucze = {wiersz.id_dokumentu: _klucz(wiersz, uwzglednij_rodzaj) for wiersz in wiersze}
    dokumenty_wedlug_klucza: dict[str, list[int]] = defaultdict(list)
    for id_dokumentu, klucz in nowe_klucze.items():
        dokumenty_wedlug_klucza[klucz].append(id_dokumentu)
    kolizje = {klucz: ids for klucz, ids in dokumenty_wedlug_klucza.items() if len(ids) > 1}
    if kolizje:
        opis = "; ".join(f"{klucz}: dokumenty {ids}" for klucz, ids in sorted(kolizje.items()))
        raise RuntimeError(f"Wykryto kolizje kluczy dokumentów kosztowych; uzgodnij duplikaty przed migracją: {opis}")

    zajete = {wiersz.klucz_unikalnosci for wiersz in wiersze} | set(nowe_klucze.values())
    tymczasowe: dict[int, str] = {}
    for wiersz in wiersze:
        klucz = f"__migracja_0004_{wiersz.id_dokumentu}__"
        while klucz in zajete:
            klucz += "_"
        zajete.add(klucz)
        tymczasowe[wiersz.id_dokumentu] = klucz

    for id_dokumentu, klucz in tymczasowe.items():
        polaczenie.execute(
            dokument_kosztowy.update()
            .where(dokument_kosztowy.c.id_dokumentu == id_dokumentu)
            .values(klucz_unikalnosci=klucz)
        )
    for id_dokumentu, klucz in nowe_klucze.items():
        polaczenie.execute(
            dokument_kosztowy.update()
            .where(dokument_kosztowy.c.id_dokumentu == id_dokumentu)
            .values(klucz_unikalnosci=klucz)
        )


def upgrade() -> None:
    _przelicz_klucze(op.get_bind(), uwzglednij_rodzaj=False)


def downgrade() -> None:
    _przelicz_klucze(op.get_bind(), uwzglednij_rodzaj=True)
