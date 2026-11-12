# Praca w zespole

Wspólne repozytorium zbiera efekty pracy całego zespołu. Każdy deweloper pracuje na **własnej gałęzi**,
a zmiany trafiają do `main` wyłącznie przez **pull request**, który sprawdza i scala Product Owner.

## Pierwsze uruchomienie

```bash
git clone https://github.com/karpeciak/kwatermistrz.git
cd kwatermistrz
python -m venv .venv
```

Aktywacja środowiska – Windows: `.venv\Scripts\activate`, Linux/macOS: `source .venv/bin/activate`.

```bash
pip install -e ".[dev]"
pytest
```

Testy działają bez Dockera (SQLite i atrapa brokera). Cały system z PostgreSQL i RabbitMQ uruchamia
`docker compose up --build` – szczegóły w [README.md](README.md).

## Codzienna praca

1. Weź historyjkę z Jiry i zacznij od aktualnego `main`:
   ```bash
   git switch main
   git pull
   git switch -c feature/S22IT1S0P2-15-zakwaterowanie
   ```
2. Commituj małymi krokami, z kluczem historyjki na początku:
   ```bash
   git commit -m "S22IT1S0P2-15: wyszukiwanie obiektów wojskowych w miejscu delegacji"
   ```
3. Przed wysłaniem sprawdź kod:
   ```bash
   ruff check .
   ruff format .
   pytest
   ```
4. Wypchnij gałąź i otwórz pull request do `main`:
   ```bash
   git push -u origin feature/S22IT1S0P2-15-zakwaterowanie
   ```
   Na GitHubie: **Compare & pull request**, uzupełnij szablon.
5. Poczekaj na zielone CI i akceptację. Poprawki po recenzji wypychasz na tę samą gałąź.
6. Gdy `main` się zmieni w trakcie pracy, zaktualizuj gałąź:
   ```bash
   git switch main
   git pull
   git switch -
   git merge main
   ```

## Nazwy gałęzi

| Rodzaj | Wzór |
|---|---|
| Nowa funkcja | `feature/S22IT1S0P2-<nr>-<krotki-opis>` |
| Poprawka błędu | `fix/S22IT1S0P2-<nr>-<krotki-opis>` |
| Dokumentacja, konfiguracja | `chore/<krotki-opis>` |

## Gdzie co dopisywać

| Zmiana | Plik |
|---|---|
| Logika agenta | `src/kwatermistrz/agents/<agent>.py` – agent w budowie przenieś z `w_budowie.py` do własnego pliku |
| Nowa tabela lub kolumna | `src/kwatermistrz/models.py` |
| Nowy typ komunikatu | `src/kwatermistrz/messaging.py` (`Typ`) |
| Endpoint API | `src/kwatermistrz/api/main.py`, schematy w `api/schemas.py` |
| Testy | `tests/test_<obszar>.py` – każda historyjka powinna mieć test |

Każda operacja agenta jest logowana automatycznie przez `Agent.wykonaj`. Decyzje zapisuj przez
`ktx.decyzja(Decyzja(...))` – dla decyzji podejmowanych przez model podaj `rodzaj="Probabilistyczna"` i `pewnosc`.

## Zasady

- Nie commituj haseł, tokenów ani pliku `.env`.
- Jeden pull request = jedna historyjka (lub jej spójna część).
- Nie wypychaj bezpośrednio na `main`.
