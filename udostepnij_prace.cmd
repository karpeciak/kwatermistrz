@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "EXPECTED_BRANCH=feature/S22IT1S0P2-27-wybor-podrozy"
if /i "%~1"=="--help" goto help

call :check_branch
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m ruff check .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m ruff format --check .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pytest
if errorlevel 1 goto failed
git fetch origin --prune
if errorlevel 1 goto failed
git merge-base --is-ancestor origin/main HEAD
if errorlevel 1 (
    echo Main ma nowe zmiany lub nie mozna go sprawdzic. Najpierw przeanalizuj i scal origin/main.
    goto failed
)
call :check_branch
if errorlevel 1 goto failed
git status --short --branch
if errorlevel 1 goto failed
git push -u origin "%EXPECTED_BRANCH%"
if errorlevel 1 goto failed
echo Galaz wyslana. Utworz pull request do main:
echo https://github.com/karpeciak/kwatermistrz/compare/main...feature/S22IT1S0P2-27-wybor-podrozy?expand=1
echo Tytul: S22IT1S0P2-27: wybor wariantu podrozy w obu kierunkach
echo Opis: docs/S22IT1S0P2-27-PR.md
echo Scalenie wykonuje PO po zielonym CI.
exit /b 0

:check_branch
set "CURRENT_BRANCH="
for /f "delims=" %%G in ('git branch --show-current') do set "CURRENT_BRANCH=%%G"
if not "%CURRENT_BRANCH%"=="%EXPECTED_BRANCH%" (
    echo Oczekiwana galaz: %EXPECTED_BRANCH%. Publikacja przerwana.
    exit /b 1
)
git status --porcelain >nul
if errorlevel 1 exit /b 1
for /f "delims=" %%G in ('git status --porcelain') do (
    echo Repozytorium ma niezapisane zmiany. Sprawdz git status.
    exit /b 1
)
exit /b 0

:failed
echo Publikacja przerwana. Sprawdz blad powyzej. Nie scalono main.
exit /b 1

:help
echo Uruchom udostepnij_prace.cmd z PowerShella lub CMD.
echo Sprawdza galaz, czystosc zmian, Ruff, pelne pytest i aktualnosc main.
echo Wysyla wylacznie feature/S22IT1S0P2-27-wybor-podrozy, bez force push.
echo Nie zmienia ustawien PowerShella ani nie scala main.
exit /b 0
