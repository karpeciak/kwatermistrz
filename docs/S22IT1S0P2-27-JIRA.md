# Aktualizacja Jiry przygotowana 15.11.2026

Nie opublikowano: narzędzie przeglądarki odrzuca dostęp konfiguracją integracji.
Poniższe informacje opierają się na kodzie i potwierdzonym push, a nie na statusach tablicy.
Nie potwierdzono numeru PR, wyniku CI ani scalenia przez PO.

## S22IT1S0P2-27 — komentarz o stanie implementacji

Implementacja wyboru podróży jest opublikowana na gałęzi
`feature/S22IT1S0P2-27-wybor-podrozy`, commit
`0119d65b7aa00c7a5cdf3728f0dd2d778f2648f2`.

- Agent przygotowuje i porównuje spójną parę przejazdów, uwzględniając wnioskowany
  transport, terminy, koszt, czas, punktualność, przesiadki i bezpieczeństwo.
- API i ekrany umożliwiają wybór, uzasadnienie odstępstwa i odrzucenie wszystkich.
  Zmiana transportu wymaga osobnej zgody. Nowy ekran delegowanego z main jest
  połączony ze wspólnym kontraktem i audytem Agenta Transportu.
- Dostęp opiera się na podpisanej sesji; operacje podróży mają CSRF. Obcy użytkownik
  nie ma dostępu. Nieznana trasa umożliwia dodanie sprawdzonych ofert ze źródłem.
- Przed push wykonano Ruff, format i pełne pytest: 155 passed, 1 warning.
  Ostrzeżenie dotyczy deprecjacji httpx w Starlette TestClient.
- Uwzględniono main 04c561b. Osobne zmiany wspólnego koordynatora i messaging
  są opisane w docs/S22IT1S0P2-27-PR.md.

Implementacja gotowa do przeglądu. Odbiór zadania wymaga potwierdzenia PR, zielonego
CI i decyzji PO. Nie oznaczać jako odebrane wyłącznie na podstawie pusha.
Brak integracji z rzeczywistymi rozkładami przewoźników; oferty seeda są przykładowe.
Żywe połączenie z Photon nie jest potwierdzone.

## S22IT1S0P2-57 — komentarz o obu kierunkach

Zapis obu kierunków jest w opublikowanym commicie 0119d65. Wybór jest atomowy,
walidowany ponownie przez agenta i odporny na ponowienie komunikatu. Rekomendowana
para sprawdza kolejność godzin także dla podróży jednodniowej. Agent zapisuje audyt
i wysyła wynik.transport; Koordynator wymaga obu wybranych kierunków.
Testy obejmują zapis, odrzucenie, ponowienie, obcą delegację, konflikt godzin oraz
integrację nowego ekranu delegowanego z rzeczywistą obsługą wiadomości.
Stan: implementacja do przeglądu; odbiór przez PO niepotwierdzony.

## Kontynuacja MVP — formularze i sesja

Nowa lokalna poprawka, jeszcze nieopublikowana w GitHubie: wspólna ochrona CSRF
zapisów /app, natywne formularze i nagłówki HTMX, zachowanie tokenu po błędzie
oraz przekierowanie całej strony po wygaśnięciu sesji autozapisu.
Po poprawce pełne pytest: 173 passed, 1 warning in 53.44s. Do oznaczenia jako
opublikowane dopiero po push. Nie przypisano wymyślonego klucza nowego zadania.
