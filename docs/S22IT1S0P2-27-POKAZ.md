# Pokaz S22IT1S0P2-27 — Daniel Sobczak

Cel: pokazać porównanie i zatwierdzenie obu przejazdów dla samodzielnie złożonego
wniosku. Do prezentacji używamy fikcyjnych danych repozytorium. Oferta powrotna
jest przykładowa; aplikacja nie kupuje biletu i nie potwierdza dostępności pojazdu.

## Start i restart (PowerShell)

Zatrzymaj wcześniejszy serwer przez Ctrl+C. Następnie:

```powershell
Set-Location "C:\Users\Daniel\Documents\Codex\2026-11-14\new-chat-2\work\kwatermistrz"
& ".\.venv\Scripts\python.exe" .\podglad_transportu.py
```

Launcher wykona pełne testy i wystartuje tylko po ich przejściu. Poczekaj na
`Uvicorn running on http://127.0.0.1:8000`, otwórz `/app/logowanie` i odśwież Ctrl+F5.
Restart zachowuje dane. Launcher nie dodaje wniosków demo. Próby autora usunięto
15.11.2026 z lokalnej bazy po wykonaniu kopii w lokalne-kopie/ (poza Git).

## Przebieg pokazu

1. Zaloguj się jako ZOL-003. Lista Moje wnioski powinna być pusta.
2. Wybierz Nowy wniosek, PRZ-01 — Szkolenie z logistyki wojskowej, Toruń.
3. Ustaw wyjazd **06.12.2026**, powrót **11.12.2026**, kolej, bez zakwaterowania.
   Wpisz podstawę, np. „R 1/2026”, data 15.11.2026. To przykładowe dane do pokazu.
   Dojazd jest dzień wcześniej, bo kursy przykładowe z 7 grudnia nie zdążą na 8:00.
4. Przejdź do podsumowania i złóż wniosek. Pokaż numer oraz oczekiwanie na przełożonego.
5. Wyloguj się, zaloguj jako ZOL-001 i zatwierdź ten wniosek w Do zatwierdzenia.
6. Otwórz Podróże i przejazdy tego wniosku. Pokaż dwie kolumny, cenę, godziny,
   rozwijane źródło i kryteria. Porównaj koszt oraz czas.
7. Wybierz oba rekomendowane przejazdy. Pokaż sumę kosztów i zatwierdź.
8. Odśwież: oba przejazdy pozostają zaznaczone. We wniosku rozwiń Historię obsługi:
   rekomendacja, wybór człowieka oraz wynik agenta są zapisane w audycie.
9. ZOL-003 może zobaczyć wybór, ale nie zatwierdza go. Obcy żołnierz nie ma dostępu.

## Dodatkowe warianty

- Odstępstwo od rekomendacji wymaga uzasadnienia. Inny transport wymaga także zgody.
- Brak trasy: wpisz Bydgoszcz; po zatwierdzeniu osoba akceptująca może dodać sprawdzone
  oferty albo przekazać podróż do uzgodnienia. Nie są generowane fikcyjne kursy dla miasta.
- Powrót przed przyjazdem nie może zostać zatwierdzony. Rekomendacja uwzględnia całą parę.
- Nie uzależniaj pokazu od wyszukiwarki miejscowości: połączenie Photon nie zostało
  potwierdzone. Nazwę można wpisać ręcznie.

## Oddanie pracy

Gałąź: feature/S22IT1S0P2-27-wybor-podrozy. Opis PR: S22IT1S0P2-27-PR.md.
Uruchom z katalogu repozytorium `.\udostepnij_prace.cmd`: sprawdza gałąź, czystość
zmian, Ruff, pełne pytest oraz aktualność main. Dopiero po sukcesie wysyła własną
gałąź i podaje link do utworzenia PR. Main scala wyłącznie PO po zielonym CI.
Wersja CMD działa także przy wyłączonym uruchamianiu skryptów PowerShell.
