# Kwatermistrz

Wieloagentowy system obsługi wyjazdów służbowych w wojsku – od wniosku, przez zatwierdzenie i organizację wyjazdu,
do rozliczenia należności. Projekt zespołowy S22IT1S0P2 (WAT), Product Owner: Kacper (`karpeciak`).

## Gdzie co znajdziesz

| Potrzebujesz… | Zajrzyj do |
|---|---|
| Zobaczyć działającą aplikację | https://kw-api.livelyflower-540bc67c.francecentral.azurecontainerapps.io/app |
| Dokumentację API (interaktywną) | https://kw-api.livelyflower-540bc67c.francecentral.azurecontainerapps.io/docs |
| **Zacząć pracę jako deweloper** – instalacja, pierwsze uruchomienie, gałęzie, pull requesty, migracje bazy | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Sprawdzić, kto w sprincie pracuje w których plikach | [CONTRIBUTING.md – Sprint 1](CONTRIBUTING.md#sprint-1--kto-pracuje-w-których-plikach) |
| Administrować środowiskiem w Azure (logi, wersje, koszty, odtworzenie) | [infra/azure.md](infra/azure.md) |
| Historyjki i backlog | Jira, tablica S22IT1S0P2 |

Wersja online odpowiada gałęzi `main` i aktualizuje się automatycznie po scaleniu pull requesta. Logowanie w aplikacji
jest demonstracyjne (wybór osoby, bez hasła): wnioskodawca – np. **por. Magdalena Szewczyk (ZOL-003)**,
przełożony zatwierdzający – **ppłk Michał Stępień (ZOL-001)**. Po dłuższej przerwie pierwsze wejście trwa
kilka–kilkanaście sekund.

## Jak to działa

```
przeglądarka ──▶ kw-api: interfejs (/app) + API (/docs) ──▶ RabbitMQ ──▶ kw-agenci
                              │                                              │
                              └────────────────▶ PostgreSQL ◀────────────────┘
```

- **Agenci** to niezależne części systemu, każdy odpowiada za jeden etap (wniosek, zatwierdzenie, transport,
  zakwaterowanie, rozliczenie, zgodność, monitorowanie). Komunikują się wyłącznie wiadomościami przez RabbitMQ.
- **Koordynator** prowadzi delegację przez etapy: roboczy → złożony → w akceptacji → zatwierdzony → zorganizowany →
  w trakcie → do rozliczenia → rozliczony (oraz do uzupełnienia / odrzucony).
- **Logi:** każda operacja agenta trafia do `log_operacji`, każda decyzja (z uzasadnieniem, pewnością i wersją modelu)
  do `log_decyzji`. Przebieg wniosku widać w aplikacji w „Historii obsługi”.
- **Technologie:** Python, FastAPI, SQLAlchemy + Alembic, Jinja2 + HTMX, RabbitMQ, PostgreSQL, Docker,
  GitHub Actions, Azure Container Apps.

## Struktura repozytorium

```
kwatermistrz/
├── README.md                 ← ten plik: przegląd projektu
├── CONTRIBUTING.md           ← instrukcja dla deweloperów
├── pyproject.toml            ← zależności i konfiguracja narzędzi (pytest, ruff)
├── alembic.ini               ← konfiguracja migracji bazy
├── Dockerfile                ← obraz aplikacji (API i agenci – jeden obraz)
├── docker-compose.yml        ← cały system lokalnie (wymaga Dockera)
├── .env.example              ← wzór ustawień przy uruchamianiu poza Dockerem
├── .github/
│   ├── workflows/ci.yml      ← CI/CD: testy → obraz → wdrożenie do Azure
│   ├── pull_request_template.md
│   └── CODEOWNERS            ← kto przegląda pull requesty
├── infra/                    ← środowisko w Azure (dla administratora)
│   ├── azure.md              ← opis środowiska i typowe zadania
│   ├── utworz-aplikacje.ps1  ← tworzenie / aktualizacja bazy i aplikacji
│   └── wdrazanie-github.ps1  ← konfiguracja automatycznego wdrażania
├── src/kwatermistrz/         ← kod aplikacji
│   ├── agents/               ← agenci – jeden plik na agenta / historyjkę
│   ├── api/                  ← API (FastAPI) – moduł na obszar procesu
│   ├── web/                  ← interfejs użytkownika (/app): strony, szablony, styl
│   ├── migracje/             ← migracje bazy (Alembic)
│   ├── models.py             ← model danych (tabele)
│   ├── messaging.py          ← typy komunikatów i połączenie z RabbitMQ
│   ├── audit.py              ← zapis logu operacji i decyzji
│   ├── walidacja.py          ← reguły sprawdzania wniosku
│   ├── stawki.py             ← stawki i limity należności
│   ├── seed.py               ← dane przykładowe i słownikowe
│   ├── db.py, config.py      ← połączenie z bazą, ustawienia
└── tests/                    ← testy (pytest) – uruchamiane w CI przy każdym pull requeście
```

### Agenci (`src/kwatermistrz/agents/`)

| Plik | Agent | Za co odpowiada |
|---|---|---|
| `koordynator.py` | AG-01 Koordynator | przebieg delegacji przez etapy, zlecanie zadań |
| `wnioski.py`, `polecenie.py` | AG-02 Agent Wniosków | sprawdzenie wniosku, polecenie wyjazdu |
| `zatwierdzenia.py` | AG-03 Agent Zatwierdzeń | przełożony, przypomnienia, zastępstwo, jednokrotna decyzja |
| `transport.py` | AG-04 Agent Transportu | porównanie i wybór wariantu podróży |
| `zakwaterowanie.py` | AG-05 Agent Zakwaterowania | dobór noclegu w limicie z terminowym dojazdem |
| `rozliczenia.py`, `dokumenty_kosztowe.py` | AG-06 Agent Rozliczeń | wyliczenie należności, wykrywanie duplikatów |
| `zgodnosc.py` | AG-07 Agent Zgodności | weryfikacja wyliczenia, przekazanie do finansów |
| `monitorujacy.py` | AG-08 Agent Monitorujący | zmiany w trakcie wyjazdu (sprint 3) |
| `base.py`, `run.py` | – | wspólna obsługa agentów (logi, ponowienia), uruchamianie |

Na górze każdego pliku agenta jest opis, co ma robić w ramach swojej historyjki.

### API (`src/kwatermistrz/api/`) i interfejs (`src/kwatermistrz/web/`)

| Plik | Zawartość |
|---|---|
| `api/main.py` | składanie aplikacji, start (migracje, dane), `/health` |
| `api/wnioski.py` | składanie wniosku, podgląd, przebieg |
| `api/zatwierdzenia.py` | decyzje przełożonego |
| `api/organizacja.py` | polecenie wyjazdu, przejazd, zakwaterowanie |
| `api/rozliczenia.py` | rozliczenie, dokumenty, weryfikacja |
| `web/strony.py` | trasy stron `/app/…` |
| `web/szablony/` | szablony HTML (Jinja2) |
| `web/statyczne/` | arkusz stylów |
