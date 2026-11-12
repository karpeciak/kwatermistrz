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
2. `GET /delegacje/{id_delegacji}` – po chwili status `w akceptacji` (agenci zwalidowali wniosek
   i przekazali go przełożonemu).
3. `POST /delegacje/{id_delegacji}/decyzja` z treścią `{"id_osoby": "ZOL-001", "decyzja": "Zgoda"}` → status
   `zatwierdzony`. Koordynator zleca wtedy polecenie wyjazdu, transport i zakwaterowanie.
4. `GET /delegacje/{id_delegacji}/przebieg` – log operacji i decyzji każdego agenta.

Wniosek z błędem (np. pusty `cel_wyjazdu` albo data powrotu przed datą wyjazdu) dostaje status `do uzupełnienia`,
a uzasadnienie widać w przebiegu.

Etapy obsługi (S22IT1S0P2-32): `roboczy` → `złożony` → `w akceptacji` → `zatwierdzony` → `zorganizowany` →
`w trakcie` → `do rozliczenia` → `rozliczony`; boczne: `do uzupełnienia`, `odrzucony`.

## Architektura

```
klient ──HTTP──▶ API (FastAPI) ──▶ RabbitMQ (exchange „kwatermistrz”, topic) ──▶ agenci
                     │                                                          │
                     └──────────────────▶ PostgreSQL ◀──────────────────────────┘
                                         (model z Excela + LogOperacji + LogDecyzji)
```

| Agent | Kolejka | Stan |
|---|---|---|
| AG-01 Koordynator | `kwatermistrz.koordynator` | działa – pełny przebieg delegacji, przejścia według dat |
| AG-02 Agent Wniosków | `kwatermistrz.wnioski` | walidacja działa (S22IT1S0P2-7…12); w sprincie 1: -33, polecenie wyjazdu -38 |
| AG-03 Agent Zatwierdzeń | `kwatermistrz.zatwierdzenia` | działa – przełożony, eskalacja (-13), jednokrotna decyzja (-14); w sprincie 1: -35 |
| AG-04 Agent Transportu | `kwatermistrz.transport` | sprint 1: S22IT1S0P2-27 |
| AG-05 Agent Zakwaterowania | `kwatermistrz.zakwaterowanie` | sprint 1: S22IT1S0P2-39 |
| AG-06 Agent Rozliczeń | `kwatermistrz.rozliczenia` | sprint 1: S22IT1S0P2-36, -20 |
| AG-07 Agent Zgodności | `kwatermistrz.zgodnosc` | sprint 1: S22IT1S0P2-43 |
| AG-08 Agent Monitorujący | `kwatermistrz.monitorujacy` | sprint 3 |

Zadania jeszcze nieimplementowane są przyjmowane i odnotowywane w logu z numerem historyjki, żeby przebieg
był widoczny od początku. Podział pracy w sprincie 1: [CONTRIBUTING.md](CONTRIBUTING.md#sprint-1--kto-pracuje-w-których-plikach).

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
