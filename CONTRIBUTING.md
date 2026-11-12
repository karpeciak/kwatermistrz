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

## Gdzie co dopisywać

| Zmiana | Plik |
|---|---|
| Logika agenta | `src/kwatermistrz/agents/<agent>.py` – agenta „w budowie” przenieś z `w_budowie.py` do własnego pliku |
| Nowa tabela lub kolumna | `src/kwatermistrz/models.py` |
| Nowy typ komunikatu | `src/kwatermistrz/messaging.py` (`Typ`) |
| Endpoint API | `src/kwatermistrz/api/main.py`, schematy w `api/schemas.py` |
| Testy | `tests/test_<obszar>.py` – każda historyjka powinna mieć test |

Każda operacja agenta jest logowana automatycznie przez `Agent.wykonaj`. Decyzje zapisuj przez
`ktx.decyzja(Decyzja(...))` – dla decyzji podejmowanych przez model podaj `rodzaj="Probabilistyczna"` i `pewnosc`.

Testy używają SQLite z włączonym sprawdzaniem kluczy obcych, tak jak PostgreSQL w Azure – jeśli test przechodzi
lokalnie, zachowanie w chmurze powinno być takie samo.

## Zasady

- Nie wypychaj bezpośrednio na `main` – zawsze przez pull request (gałąź nie jest technicznie chroniona,
  pilnujemy tego sami).
- Jeden pull request = jedna historyjka (lub jej spójna część).
- Nie commituj haseł, tokenów ani pliku `.env`.
- Zmieniasz schemat bazy (nowa kolumna)? Napisz o tym w pull requeście – baza w Azure wymaga wtedy migracji.
