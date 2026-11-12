# Kwatermistrz

Wieloagentowy system obsługi wyjazdów służbowych w wojsku – od wniosku do rozliczenia.
Projekt S22IT1S0P2 (WAT).

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

Przebieg jednej delegacji: `GET /delegacje/{id}/przebieg`.

## Uruchomienie lokalne

Wymagania: Docker Desktop (z integracją WSL2), opcjonalnie [uv](https://docs.astral.sh/uv/).

```bash
docker compose up --build
```

- API i dokumentacja: http://localhost:8000/docs
- Panel RabbitMQ: http://localhost:15672 (guest / guest)

Przykładowy przepływ:

```bash
curl -X POST http://localhost:8000/wnioski -H "Content-Type: application/json" -H "Idempotency-Key: demo-1" \
  -d '{"id_zolnierza":"ZOL-003","id_przedsiewziecia":"PRZ-01","cel_wyjazdu":"Szkolenie z logistyki",
       "miejsce_docelowe":"Toruń","data_wyjazdu":"2026-12-07","data_powrotu":"2026-12-11","srodek_transportu":"pociąg"}'
curl -X POST http://localhost:8000/delegacje/<ID>/decyzja -H "Content-Type: application/json" \
  -d '{"id_osoby":"ZOL-001","decyzja":"Zgoda"}'
curl http://localhost:8000/delegacje/<ID>/przebieg
```

### Praca nad jednym agentem

```bash
docker compose up -d postgres rabbitmq api
uv sync --extra dev
uv run kwatermistrz-agenci zatwierdzenia
```

## Testy

```bash
uv run ruff check .
uv run pytest
```

Testy działają na SQLite i atrapie brokera – nie wymagają Dockera.

## Praca w zespole

Każdy deweloper pracuje na własnej gałęzi (`feature/S22IT1S0P2-<nr>-<opis>`) i zgłasza zmiany pull requestem
do `main`. Instrukcja krok po kroku: [CONTRIBUTING.md](CONTRIBUTING.md).

## Wdrożenie

Push na `main` → GitHub Actions: testy → obraz Docker w GitHub Container Registry → aktualizacja
Azure Container Apps. Konfiguracja Azure: [infra/azure.md](infra/azure.md).
