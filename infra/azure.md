# Azure – środowisko online

Ten dokument jest dla **administratora** (Product Ownera). Deweloperzy nie potrzebują dostępu do Azure –
ich zmiany trafiają tu automatycznie po scaleniu pull requesta do `main`.

## Stan obecny

| Zasób | Nazwa | Region | Uwagi |
|---|---|---|---|
| Grupa zasobów | `kwatermistrz-rg` | Germany West Central (metadane) | wszystko poniżej należy do tej grupy |
| Środowisko Container Apps | `kw-env` | France Central | typ Express |
| API | `kw-api` | France Central | publiczny adres, skaluje się do zera (pierwsze wywołanie po przerwie trwa kilka–kilkanaście sekund) |
| Agenci | `kw-agenci` | France Central | wszyscy agenci w jednym kontenerze, 1 replika |
| PostgreSQL Flexible Server | `kw-pg-81826`, baza `kwatermistrz` | France Central | Burstable B1ms |
| RabbitMQ | CloudAMQP, plan *Little Lemur* | Azure West Europe | konto CloudAMQP, poza naszą subskrypcją |
| Obrazy | `ghcr.io/karpeciak/kwatermistrz:<sha>` | GitHub | budowane przez CI |
| Budżet | `kwatermistrz-budzet` | subskrypcja | 80 USD/mies., maile przy 50%, 80% i prognozie 100% |
| Tożsamość wdrożeń | `kwatermistrz-github` | Entra ID | rola Contributor tylko w `kwatermistrz-rg` |

**Adres aplikacji:** https://kw-api.livelyflower-540bc67c.francecentral.azurecontainerapps.io
(dokumentacja interaktywna: `/docs`).

```
merge do main ─▶ GitHub Actions: test ─▶ build (obraz do ghcr.io) ─▶ deploy (OIDC, bez haseł)
                                                                        │
                                       az containerapp update kw-api, kw-agenci --image …:<sha>
```

**Dlaczego agenci w jednym kontenerze:** agent musi stale nasłuchiwać kolejki, więc nie może skalować się do zera.
Osiem stale działających kontenerów szybko wyczerpałoby środki Azure for Students. Logicznie agenci pozostają
osobni: własne kolejki, obsługa błędów i wpisy w logach. Osobną aplikację dla agenta tworzy się poleceniem
`kwatermistrz-agenci <nazwa>`.

## Ograniczenia Azure for Students (sprawdzone)

- Zasoby można tworzyć tylko w `francecentral`, `austriaeast`, `germanywestcentral`, `swedencentral`,
  `belgiumcentral` (polityka „Allowed resource deployment regions”).
- PostgreSQL B1ms: zablokowany w `germanywestcentral`, `SkuNotAvailable` w `swedencentral` – działa w `francecentral`.
- Środowisko Container Apps typu **Express** nie obsługuje `--revision-suffix`; nową rewizję wymusza zmiana
  zmiennej środowiskowej (skrypt używa `WDROZENIE`).
- `az containerapp --args` nie przyjmuje wartości zaczynających się od `-` – dlatego agenci startują poleceniem
  `kwatermistrz-agenci`, a nie `python -m …`.
- GitHub może przedstawiać repozytorium w logowaniu OIDC z numerami kont
  (`repo:karpeciak@<id>/kwatermistrz@<id>:…`) – zaufanie w Azure musi mieć oba warianty (robi to skrypt).

## Typowe zadania

Polecenia dla PowerShell po `az login`. Jeśli `az` nie jest widoczne, użyj pełnej ścieżki
`& "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"`.

**Logi aplikacji** (np. gdy po wdrożeniu coś nie działa):

```powershell
az containerapp logs show -g kwatermistrz-rg -n kw-api --tail 50
az containerapp logs show -g kwatermistrz-rg -n kw-agenci --tail 50
```

**Która wersja działa:**

```powershell
az containerapp list -g kwatermistrz-rg --query "[].{nazwa:name, stan:properties.runningStatus, obraz:properties.template.containers[0].image}" -o table
```

**Ręczne wdrożenie konkretnej wersji** (np. powrót do poprzedniej) – tag to pierwsze 7 znaków commita z `main`:

```powershell
az containerapp update -g kwatermistrz-rg -n kw-api --image ghcr.io/karpeciak/kwatermistrz:<sha>
az containerapp update -g kwatermistrz-rg -n kw-agenci --image ghcr.io/karpeciak/kwatermistrz:<sha>
```

**Odnowienie tokenu GitHub** (Azure przestaje pobierać obrazy po jego wygaśnięciu) albo zmiana adresu RabbitMQ:
wygeneruj nowy token (`read:packages`) i uruchom `infra/utworz-aplikacje.ps1` – zaktualizuje sekrety obu aplikacji.

**Wstrzymanie kosztów** (np. przerwa semestralna) i wznowienie:

```powershell
az containerapp update -g kwatermistrz-rg -n kw-agenci --min-replicas 0
az postgres flexible-server stop -g kwatermistrz-rg -n kw-pg-81826
# wznowienie
az postgres flexible-server start -g kwatermistrz-rg -n kw-pg-81826
az containerapp update -g kwatermistrz-rg -n kw-agenci --min-replicas 1
```

Zatrzymany serwer PostgreSQL Azure uruchamia automatycznie po 7 dniach.

## Odtworzenie środowiska od zera

1. RabbitMQ: konto na [cloudamqp.com](https://www.cloudamqp.com/) → instancja *Little Lemur* (Azure, West Europe)
   → skopiuj **AMQP URL**.
2. Token GitHub (classic) z uprawnieniem `read:packages`.
3. Grupa i środowisko:
   ```powershell
   az provider register -n Microsoft.App --wait
   az provider register -n Microsoft.OperationalInsights --wait
   az provider register -n Microsoft.DBforPostgreSQL --wait
   az group create -n kwatermistrz-rg -l francecentral
   az containerapp env create -g kwatermistrz-rg -n kw-env -l francecentral
   ```
4. Baza i aplikacje – skrypt pyta o AMQP URL i token, generuje hasło do bazy i zapisuje je tylko w sekretach Azure:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\infra\utworz-aplikacje.ps1
   ```
5. Automatyczne wdrażanie (tożsamość, rola, zaufanie OIDC, zmienne w repozytorium):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\infra\wdrazanie-github.ps1
   ```
6. Budżet: portal Azure → **Cost Management → Budgets → Add**, alerty przy 50% i 80%.
