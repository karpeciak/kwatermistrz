"""Sprint 1 – fundament: statusy z S22IT1S0P2-32, wniosek roboczy, polecenie wyjazdu, połączenia,
zakwaterowanie z czasem dojazdu, nowe rozliczenie z dokumentami kosztowymi i korektami.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dotychczasowe statusy delegacji → etapy obsługi z S22IT1S0P2-32
STATUSY = {
    "Złożony": "złożony",
    "W walidacji": "złożony",
    "Do poprawy": "do uzupełnienia",
    "Oczekuje na akceptację": "w akceptacji",
    "Zatwierdzona": "zatwierdzony",
    "Odrzucona": "odrzucony",
    "W realizacji": "w trakcie",
    "Rozliczona": "rozliczony",
}


def upgrade() -> None:
    # ---------------------------------------------------------------- słowniki
    with op.batch_alter_table("jednostka") as b:
        b.add_column(sa.Column("budzet_wykorzystany", sa.Numeric(12, 2), server_default="0", nullable=False))
    with op.batch_alter_table("przedsiewziecie") as b:
        b.add_column(sa.Column("godzina_rozpoczecia", sa.Time(), nullable=True))
    with op.batch_alter_table("obiekt_zakwaterowania") as b:
        b.add_column(sa.Column("liczba_wolnych_miejsc", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("czas_dojazdu_min", sa.Integer(), nullable=True))

    # ---------------------------------------------------------------- delegacja
    with op.batch_alter_table("delegacja") as b:
        b.add_column(sa.Column("potrzeba_zakwaterowania", sa.Boolean(), server_default="1", nullable=False))
        b.add_column(sa.Column("podstawa_numer_rozkazu", sa.String(50), nullable=True))
        b.add_column(sa.Column("podstawa_data_rozkazu", sa.Date(), nullable=True))
        b.add_column(sa.Column("szacowany_koszt", sa.Numeric(10, 2), nullable=True))
        b.add_column(sa.Column("data_zmiany_statusu", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("etap_przygotowania", sa.String(30), nullable=True))
        b.add_column(sa.Column("data_ostatniej_zmiany", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("uzasadnienie_decyzji", sa.Text(), nullable=True))
        b.alter_column("data_zlozenia", existing_type=sa.DateTime(), nullable=True)

    delegacja = sa.table(
        "delegacja",
        sa.column("status", sa.String),
        sa.column("data_zmiany_statusu", sa.DateTime),
        sa.column("data_zlozenia", sa.DateTime),
        sa.column("data_decyzji", sa.DateTime),
    )
    for stary, nowy in STATUSY.items():
        op.execute(delegacja.update().where(delegacja.c.status == stary).values(status=nowy))
    op.execute(
        delegacja.update().values(
            data_zmiany_statusu=sa.func.coalesce(delegacja.c.data_decyzji, delegacja.c.data_zlozenia, sa.func.now())
        )
    )
    with op.batch_alter_table("delegacja") as b:
        b.alter_column("data_zmiany_statusu", existing_type=sa.DateTime(), nullable=False)

    # ---------------------------------------------------------------- organizacja wyjazdu
    op.create_table(
        "polecenie_wyjazdu",
        sa.Column("numer", sa.String(30), primary_key=True),
        sa.Column("id_delegacji", sa.String(30), sa.ForeignKey("delegacja.id_delegacji"), nullable=False, unique=True),
        sa.Column("data_sporzadzenia", sa.DateTime(), nullable=False),
        sa.Column("wersja_wzoru", sa.String(20), nullable=False),
        sa.Column("tresc", sa.JSON(), nullable=False),
    )
    op.create_table(
        "polaczenie",
        sa.Column("id_polaczenia", sa.String(20), primary_key=True),
        sa.Column("skad", sa.String(100), nullable=False),
        sa.Column("dokad", sa.String(100), nullable=False),
        sa.Column("srodek_transportu", sa.String(40), nullable=False),
        sa.Column("przewoznik", sa.String(100), nullable=False),
        sa.Column("godzina_odjazdu", sa.Time(), nullable=False),
        sa.Column("godzina_przyjazdu", sa.Time(), nullable=False),
        sa.Column("czas_min", sa.Integer(), nullable=False),
        sa.Column("liczba_przesiadek", sa.Integer(), nullable=False),
        sa.Column("koszt", sa.Numeric(10, 2), nullable=False),
        sa.Column("punktualnosc", sa.Float(), nullable=False),
        sa.Column("ocena_bezpieczenstwa", sa.Integer(), nullable=False),
    )
    with op.batch_alter_table("przejazd") as b:
        b.add_column(sa.Column("id_polaczenia", sa.String(20), nullable=True))
        b.add_column(sa.Column("rekomendowany", sa.Boolean(), server_default="0", nullable=False))
        b.add_column(sa.Column("uzasadnienie", sa.Text(), nullable=True))
        b.create_foreign_key("fk_przejazd_polaczenie", "polaczenie", ["id_polaczenia"], ["id_polaczenia"])
    with op.batch_alter_table("zakwaterowanie") as b:
        b.add_column(sa.Column("czas_dojazdu_min", sa.Integer(), nullable=True))
        b.add_column(sa.Column("wymaga_odstepstwa", sa.Boolean(), server_default="0", nullable=False))

    # ---------------------------------------------------------------- rozliczenie (tabela dotąd nieużywana – nowa)
    op.drop_table("rozliczenie")
    op.create_table(
        "rozliczenie",
        sa.Column("id_rozliczenia", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_delegacji", sa.String(30), sa.ForeignKey("delegacja.id_delegacji"), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("data_zlozenia", sa.DateTime(), nullable=False),
        sa.Column("faktyczny_wyjazd", sa.DateTime(), nullable=True),
        sa.Column("faktyczny_powrot", sa.DateTime(), nullable=True),
        sa.Column("zapewnione_posilki", sa.JSON(), nullable=False),
        sa.Column("bezplatne_zakwaterowanie", sa.Boolean(), nullable=False),
        sa.Column("dieta", sa.Numeric(10, 2), nullable=True),
        sa.Column("zwrot_przejazdu", sa.Numeric(10, 2), nullable=True),
        sa.Column("zwrot_noclegu", sa.Numeric(10, 2), nullable=True),
        sa.Column("ryczalty", sa.Numeric(10, 2), nullable=True),
        sa.Column("kwota_naleznosci", sa.Numeric(10, 2), nullable=True),
        sa.Column("zastrzezenia", sa.Text(), nullable=True),
        sa.Column("data_przekazania", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "dokument_kosztowy",
        sa.Column("id_dokumentu", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_rozliczenia", sa.Integer(), sa.ForeignKey("rozliczenie.id_rozliczenia"), nullable=False),
        sa.Column("rodzaj", sa.String(30), nullable=False),
        sa.Column("numer", sa.String(60), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("kwota", sa.Numeric(10, 2), nullable=False),
        sa.Column("kwota_uznana", sa.Numeric(10, 2), nullable=True),
        sa.Column("przekracza_limit", sa.Boolean(), nullable=False),
        sa.Column("klucz_unikalnosci", sa.String(200), nullable=False, unique=True),
        sa.Column("data_przekazania", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_dokument_kosztowy_id_rozliczenia", "dokument_kosztowy", ["id_rozliczenia"])
    op.create_table(
        "korekta_rozliczenia",
        sa.Column("id_korekty", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_rozliczenia", sa.Integer(), sa.ForeignKey("rozliczenie.id_rozliczenia"), nullable=False),
        sa.Column("pozycja", sa.String(60), nullable=False),
        sa.Column("wartosc_przed", sa.Numeric(10, 2), nullable=True),
        sa.Column("wartosc_po", sa.Numeric(10, 2), nullable=True),
        sa.Column("powod", sa.Text(), nullable=False),
        sa.Column("znacznik_czasu", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_korekta_rozliczenia_id_rozliczenia", "korekta_rozliczenia", ["id_rozliczenia"])


def downgrade() -> None:
    op.drop_index("ix_korekta_rozliczenia_id_rozliczenia", "korekta_rozliczenia")
    op.drop_table("korekta_rozliczenia")
    op.drop_index("ix_dokument_kosztowy_id_rozliczenia", "dokument_kosztowy")
    op.drop_table("dokument_kosztowy")
    op.drop_table("rozliczenie")
    op.create_table(
        "rozliczenie",
        sa.Column("id_rozliczenia", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_delegacji", sa.String(30), sa.ForeignKey("delegacja.id_delegacji"), nullable=False, unique=True),
        sa.Column("data_zlozenia", sa.Date(), nullable=False),
        sa.Column("liczba_dob", sa.Integer(), nullable=False),
        sa.Column("kwota_diet", sa.Numeric(10, 2), nullable=False),
        sa.Column("koszt_przejazdow", sa.Numeric(10, 2), nullable=False),
        sa.Column("koszt_noclegow", sa.Numeric(10, 2), nullable=False),
        sa.Column("limit_noclegow", sa.Numeric(10, 2), nullable=False),
        sa.Column("przekroczenie_limitu", sa.Boolean(), nullable=False),
        sa.Column("uzasadnienie", sa.Text(), nullable=True),
        sa.Column("decyzja_przelozonego", sa.String(20), nullable=True),
        sa.Column("noclegi_uznane", sa.Numeric(10, 2), nullable=False),
        sa.Column("kwota_naleznosci", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
    )
    with op.batch_alter_table("zakwaterowanie") as b:
        b.drop_column("wymaga_odstepstwa")
        b.drop_column("czas_dojazdu_min")
    with op.batch_alter_table("przejazd") as b:
        b.drop_constraint("fk_przejazd_polaczenie", type_="foreignkey")
        b.drop_column("uzasadnienie")
        b.drop_column("rekomendowany")
        b.drop_column("id_polaczenia")
    op.drop_table("polaczenie")
    op.drop_table("polecenie_wyjazdu")

    delegacja = sa.table("delegacja", sa.column("status", sa.String))
    for nowy, stary in {
        "złożony": "Złożony",
        "do uzupełnienia": "Do poprawy",
        "w akceptacji": "Oczekuje na akceptację",
        "zatwierdzony": "Zatwierdzona",
        "odrzucony": "Odrzucona",
        "w trakcie": "W realizacji",
        "rozliczony": "Rozliczona",
    }.items():
        op.execute(delegacja.update().where(delegacja.c.status == nowy).values(status=stary))
    with op.batch_alter_table("delegacja") as b:
        for kolumna in (
            "uzasadnienie_decyzji",
            "data_ostatniej_zmiany",
            "etap_przygotowania",
            "data_zmiany_statusu",
            "szacowany_koszt",
            "podstawa_data_rozkazu",
            "podstawa_numer_rozkazu",
            "potrzeba_zakwaterowania",
        ):
            b.drop_column(kolumna)

    with op.batch_alter_table("obiekt_zakwaterowania") as b:
        b.drop_column("czas_dojazdu_min")
        b.drop_column("liczba_wolnych_miejsc")
    with op.batch_alter_table("przedsiewziecie") as b:
        b.drop_column("godzina_rozpoczecia")
    with op.batch_alter_table("jednostka") as b:
        b.drop_column("budzet_wykorzystany")
