# Instrukcja dla zespołu

Wspólne repozytorium zbiera efekty pracy całego zespołu. Każdy deweloper pracuje na **własnej gałęzi**,
a zmiany trafiają do `main` wyłącznie przez **pull request**, który sprawdza i scala Product Owner
(Kacper, `karpeciak`). Po scaleniu zmiana sama trafia do wersji online.

```
twoja gałąź → push → pull request → CI (testy) → przegląd PO → merge do main → automatycznie w Azure
```

## 1. Przygotowanie komputera (jednorazowo)

| Program | Instalacja (Windows, PowerShell) | Sprawdzenie |
|---|---|---|
| Git | `winget install --id Git.Git -e` | `git --version` |
| Python 3.11 lub nowszy | `winget install --id Python.Python.3.12 -e` | `python --version` |
| VS Code | `winget install --id Microsoft.VisualStudioCode -e` | `code --version` |
| GitHub CLI (opcjonalnie) | `winget install --id GitHub.cli -e` | `gh --version` |

Po instalacji zamknij i otwórz ponownie terminal. W VS Code zainstaluj rozszerzenia:

```bash
code --install-extension ms-python.python
code --install-extension charliermarsh.ruff
```

Ustaw autora commitów (ten sam adres co na GitHubie):

```bash
git config --global user.name "Imię Nazwisko"
git config --global user.email "twoj@email"
```

**Docker nie jest potrzebny** – testy działają bez niego, a obrazy buduje CI na GitHubie.

## 2. Dostęp do repozytorium

Repozytorium jest prywatne. Podaj Product Ownerowi swój login GitHub i przyjmij zaproszenie z maila albo
z https://github.com/karpeciak/kwatermistrz/invitations.

## 3. Pierwsze uruchomienie

```bash
git clone https://github.com/karpeciak/kwatermistrz.git
cd kwatermistrz
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

Na końcu powinno być `18 passed` (lub więcej). Na Linuxie/macOS zamiast `.venv\Scripts\python` użyj `.venv/bin/python`.

W VS Code: **File → Open Folder** → `kwatermistrz`, potem `Ctrl+Shift+P` → *Python: Select Interpreter* → `.venv`.
Wbudowany terminal VS Code sam użyje tego środowiska.

> Jeśli PowerShell zgłasza „running scripts is disabled” przy aktywacji `.venv\Scripts\activate`,
> nie musisz niczego zmieniać – używaj poleceń z pełną ścieżką jak wyżej (`.venv\Scripts\python -m …`).

## 4. Codzienna praca

1. Weź historyjkę z Jiry i zacznij od aktualnego `main`:
   ```bash
   git switch main
   git pull
   git switch -c feature/S22IT1S0P2-15-zakwaterowanie
   ```
2. Commituj małymi krokami, z kluczem historyjki na początku:
   ```bash
   git add .
   git commit -m "S22IT1S0P2-15: wyszukiwanie obiektów wojskowych w miejscu delegacji"
   ```
3. Przed wysłaniem sprawdź kod (to samo sprawdzi CI):
   ```bash
   .venv\Scripts\python -m ruff check .
   .venv\Scripts\python -m ruff format .
   .venv\Scripts\python -m pytest
   ```
4. Wypchnij gałąź:
   ```bash
   git push -u origin feature/S22IT1S0P2-15-zakwaterowanie
   ```
5. Na GitHubie kliknij **Compare & pull request**, uzupełnij szablon (klucz historyjki, co zmieniasz, jak sprawdzić).
   Product Owner dostanie prośbę o przegląd automatycznie.
6. Poczekaj na zielone CI (✔ przy pull requeście) i akceptację. Poprawki po recenzji wypychasz na tę samą gałąź –
   pull request zaktualizuje się sam.
7. Po scaleniu usuń gałąź i wróć na `main`:
   ```bash
   git switch main
   git pull
   git branch -d feature/S22IT1S0P2-15-zakwaterowanie
   ```
8. Gdy `main` zmieni się w trakcie Twojej pracy, zaktualizuj swoją gałąź:
   ```bash
   git switch main
   git pull
   git switch -
   git merge main
   ```

Po scaleniu Twoja zmiana jest po kilku minutach w wersji online (adres w [README.md](README.md#aplikacja-online)).
Postęp wdrożenia widać w zakładce **Actions** na GitHubie.

## Nazwy gałęzi

| Rodzaj | Wzór |
|---|---|
| Nowa funkcja | `feature/S22IT1S0P2-<nr>-<krotki-opis>` |
| Poprawka błędu | `fix/S22IT1S0P2-<nr>-<krotki-opis>` |
| Dokumentacja, konfiguracja | `chore/<krotki-opis>` lub `docs/<krotki-opis>` |

## Sprint 1 – kto pracuje w których plikach

Każda historyjka ma przygotowane miejsce: plik z opisem kroków do zrobienia (docstring na górze pliku) i zaślepką,
która przyjmuje zadanie i zapisuje je w logu. Dzięki temu cały przebieg delegacji działa od pierwszego dnia,
a każdy pracuje głównie we własnych plikach.

| Historyjka | Osoba | Agent / logika | API |
|---|---|---|---|
| 33 Rejestracja wniosku | Piotr Rowiński | `agents/wnioski.py`, `walidacja.py` | `api/wnioski.py` |
| 37 Wniosek roboczy | Piotr Rowiński | – | `api/wnioski.py` |
| 32 Status i miejsce oczekiwania | Piotr Rowiński | – | `api/wnioski.py` |
| 35 Zbiorcze zatwierdzenie | Piotr Rowiński | `agents/zatwierdzenia.py` | `api/zatwierdzenia.py` |
| 14 Jednokrotne zatwierdzenie | Marcel Zając | `agents/zatwierdzenia.py` (gotowe – do odbioru) | `api/zatwierdzenia.py` |
| 38 Polecenie wyjazdu | Kornel Rybacki | `agents/polecenie.py` | `api/organizacja.py` |
| 39 Zakwaterowanie | Kornel Rybacki | `agents/zakwaterowanie.py` | `api/organizacja.py` |
| 27 Wybór wariantu podróży | Daniel Sobczak | `agents/transport.py` | `api/organizacja.py` |
| 36 Wyliczenie należności | Piotr Rowiński | `agents/rozliczenia.py`, `stawki.py` | `api/rozliczenia.py` |
| 20 Duplikaty dokumentów | Marcel Zając | `agents/dokumenty_kosztowe.py` | `api/rozliczenia.py` |
| 43 Weryfikacja i przekazanie | Kornel Rybacki | `agents/zgodnosc.py` | `api/rozliczenia.py` |

Wspólne pliki (`models.py`, `messaging.py`, `agents/koordynator.py`) zawierają już wszystko, czego potrzebują
powyższe historyjki. Jeśli musisz je zmienić, zrób to małym, osobnym commitem i napisz o tym w pull requeście –
łatwiej wtedy rozwiązać ewentualny konflikt z inną gałęzią.

### Przebieg delegacji

```
złożony → (Agent Wniosków: walidacja) → w akceptacji → (przełożony) → zatwierdzony
   │                                                        │
   └→ do uzupełnienia                       odrzucony / do uzupełnienia
zatwierdzony → równolegle: polecenie wyjazdu (38), transport (27), zakwaterowanie (39)
            → zorganizowany → w trakcie → do rozliczenia (daty – zadanie cykliczne Koordynatora)
do rozliczenia → wyliczenie (36, 20) → weryfikacja i przekazanie (43) → rozliczony
```

Agent kończy swoją część, wysyłając komunikat `wynik.*` (np. `Typ.WYNIK_TRANSPORTU`) – Koordynator sam sprawdzi,
czy delegacja może przejść dalej. Lista komunikatów: `messaging.py` (`Typ`).

## Gdzie co dopisywać

| Zmiana | Plik |
|---|---|
| Logika agenta | `src/kwatermistrz/agents/<agent>.py` |
| Stawki, limity należności | `src/kwatermistrz/stawki.py` |
| Nowa tabela lub kolumna | `src/kwatermistrz/models.py` + migracja (niżej) |
| Nowy typ komunikatu | `src/kwatermistrz/messaging.py` (`Typ`) |
| Endpoint API | moduł obszaru w `src/kwatermistrz/api/` (schematy żądań/odpowiedzi możesz trzymać w tym samym module) |
| Ekran interfejsu | `src/kwatermistrz/web/strony.py` (trasy `/app/...`) i szablony w `web/szablony/` (Jinja2 + HTMX); lista ekranów do zrobienia w historyjkach – docstring `strony.py` |
| Dane przykładowe | `src/kwatermistrz/seed.py` |
| Testy | `tests/test_<obszar>.py` – każda historyjka powinna mieć test |

Każda operacja agenta jest logowana automatycznie przez `Agent.wykonaj`. Decyzje zapisuj przez
`ktx.decyzja(Decyzja(...))` – dla decyzji podejmowanych przez model podaj `rodzaj="Probabilistyczna"` i `pewnosc`.

Testy budują bazę tymi samymi migracjami co Azure (SQLite z włączonymi kluczami obcymi) – jeśli test przechodzi
lokalnie, zachowanie w chmurze powinno być takie samo.

## Zmiana bazy danych (migracje)

Po zmianie w `models.py` (nowa tabela, kolumna, zmiana typu) wygeneruj migrację i dołącz ją do pull requesta:

```bash
.venv\Scripts\alembic revision --autogenerate -m "S22IT1S0P2-15 obiekty zakwaterowania"
```

Plik pojawi się w `src/kwatermistrz/migracje/versions/` – przejrzyj go. Szczególnie uważaj na:

- **nową kolumnę `NOT NULL` w tabeli, która ma już dane** – dodaj `server_default` albo dodaj kolumnę jako pustą,
  uzupełnij wartości i dopiero potem ustaw `nullable=False` (przykład: `0002_sprint_1_fundament.py`),
- **zmianę nazwy kolumny** – Alembic widzi ją jako usunięcie i dodanie (utrata danych); zamień na `alter_column`.

Test `test_migracje_odpowiadaja_modelom` nie przejdzie, jeśli zapomnisz o migracji. W Azure migracje uruchamia API
przy starcie – nic nie trzeba robić ręcznie. Dwie osoby dodały migrację równolegle? Ta, która scala później,
zmienia w swojej migracji `down_revision` na numer migracji kolegi.

## Zasady

- Nie wypychaj bezpośrednio na `main` – zawsze przez pull request (gałąź nie jest technicznie chroniona,
  pilnujemy tego sami).
- Jeden pull request = jedna historyjka (lub jej spójna część).
- Nie commituj haseł, tokenów ani pliku `.env`.
