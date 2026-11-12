# Kwatermistrz

Wieloagentowy system obsługi wyjazdów służbowych w wojsku – od wniosku do rozliczenia.
Projekt S22IT1S0P2 (WAT).

## Aplikacja online

**https://kw-api.livelyflower-540bc67c.francecentral.azurecontainerapps.io/docs**

Strona `/docs` to interaktywna dokumentacja API: przy każdej operacji jest przycisk **Try it out** → **Execute**.
Wersja online zawsze odpowiada gałęzi `main` – aktualizuje się automatycznie po scaleniu pull requesta.
Po dłuższej przerwie pierwsze wywołanie trwa kilka–kilkanaście sekund (API usypia się, gdy nikt go nie używa).

Dane przykładowe: jednostka `JW-1001`, żołnierze `ZOL-001` (dowódca), `ZOL-002` (zastępca dowódcy), `ZOL-003`,
`ZOL-004` (przełożony: `ZOL-001`), przedsięwzięcie `PRZ-01` (szkolenie w Toruniu).

Przepływ do wypróbowania:

1. `POST /wnioski` z treścią:
   ```json
   {"id_zolnierza": "ZOL-003", "id_przedsiewziecia": "PRZ-01", "cel_wyjazdu": "Szkolenie z logistyki",
    "miejsce_docelowe": "Toruń", "data_wyjazdu": "2026-12-07", "data_powrotu": "2026-12-11",
    "srodek_transportu": "pociąg"}
   ```
   Zapamiętaj `id_delegacji` z odpowiedzi.
2. `GET /delegacje/{id_delegacji}` – po chwili status `Oczekuje na akceptację` (agenci zwalidowali wniosek
   i przekazali go przełożonemu).
3. `POST /delegacje/{id_delegacji}/decyzja` z treścią `{"id_osoby": "ZOL-001", "decyzja": "Zgoda"}` → status
   `Zatwierdzona`.
4. `GET /delegacje/{id_delegacji}/przebieg` – log operacji i decyzji każdego agenta.

Wniosek z błędem (np. pusty `cel_wyjazdu` albo data powrotu przed datą wyjazdu) dostaje status `Do poprawy`,
a uzasadnienie widać w przebiegu.

## Architektura

```
klient ──HTTP──▶ API (FastAPI) ──▶ RabbitMQ (exchange „kwatermistrz”, topic) ──▶ agenci
                     │                                                          │
                     └──────────────────▶ PostgreSQL ◀──────────────────────────┘
                                         (model z Excela + LogOperacji + LogDecyzji)
```

| Agent | Kolejka | Stan |
|---|---|---|
| AG-01 Koordynator | `kwatermistrz.koordynator` | sprint 1 – maszyna stanów delegacji |
| AG-02 Agent Wniosków | `kwatermistrz.wnioski` | sprint 1 – walidacja (S22IT1S0P2-7…12) |
| AG-03 Agent Zatwierdzeń | `kwatermistrz.zatwierdzenia` | sprint 1 – przełożony, przypomnienie, eskalacja (-13), podwójna decyzja (-14) |
| AG-04 Agent Transportu | `kwatermistrz.transport` | sprint 2 |
| AG-05 Agent Zakwaterowania | `kwatermistrz.zakwaterowanie` | sprint 2 |
| AG-08 Agent Monitorujący | `kwatermistrz.monitorujacy` | sprint 3 |
| AG-06 Agent Rozliczeń | `kwatermistrz.rozliczenia` | sprint 4 |
| AG-07 Agent Zgodności | `kwatermistrz.zgodnosc` | sprint 4 |

Agenci w budowie przyjmują zadanie i odnotowują je w logu, żeby przepływ był widoczny od początku.

### Logowanie (wymóg prowadzącego)

Każda obsługa komunikatu przez agenta zapisuje wpis w **`log_operacji`** (czas, agent, operacja, wynik,
czas trwania, numer próby, komunikat). Każda decyzja agenta trafia do **`log_decyzji`**: rodzaj (regułowa /
probabilistyczna), liczba wariantów, wybór, uzasadnienie, pewność i wersja modelu. Decyzja probabilistyczna
o pewności < 0,75 lub decyzja finansowa ponad limit ma `wymaga_zatwierdzenia = true`.
`log_decyzji` wskazuje tylko na `log_operacji` – w modelu nie ma cyklu z encją log.

## Praca w zespole

Nowa osoba w zespole: [CONTRIBUTING.md](CONTRIBUTING.md) – instalacja, pierwsze uruchomienie testów,
gałęzie i pull requesty.

## Uruchomienie lokalne

**Testy** – bez Dockera (SQLite i atrapa brokera), wystarczy Python:

```bash
pytest
```

**Cały system** (API, agenci, PostgreSQL, RabbitMQ) – wymaga Docker Desktop:

```bash
docker compose up --build
```

- API i dokumentacja: http://localhost:8000/docs
- Panel RabbitMQ: http://localhost:15672 (guest / guest)

Praca nad jednym agentem przy działających usługach:

```bash
docker compose up -d postgres rabbitmq api
kwatermistrz-agenci zatwierdzenia
```

## Wdrożenie

Scalenie do `main` → GitHub Actions: testy → obraz Docker w GitHub Container Registry → automatyczna
aktualizacja Azure Container Apps. Administracja środowiskiem Azure: [infra/azure.md](infra/azure.md).
