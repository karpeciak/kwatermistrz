# Wdrożenie w Azure (Azure for Students)

## Docelowy układ

```
GitHub Actions ──obraz──▶ GitHub Container Registry (ghcr.io, bezpłatny)
                                   │
Azure Container Apps (środowisko kw-env)
├── kw-api      FastAPI, publiczny adres HTTPS, skaluje się do zera
└── kw-agenci   wszyscy agenci w jednym kontenerze (`run all`), 1 replika – stale nasłuchuje kolejek
Azure Database for PostgreSQL – Flexible Server (Burstable B1ms)
RabbitMQ – CloudAMQP (plan darmowy) albo kontener w środowisku z VNet (patrz krok 3)
```

**Dlaczego agenci w jednym kontenerze:** agent musi stale nasłuchiwać kolejki, więc nie może skalować się do zera.
Osiem stale działających kontenerów szybko wyczerpałoby środki Azure for Students. Jeden kontener z `run all`
kosztuje tyle co jeden. Logicznie agenci pozostają osobni: własne kolejki, własna obsługa błędów, własne wpisy
w logach. Gdy budżet pozwoli, wystarczy utworzyć osobne aplikacje z poleceniem `run <nazwa>`.

> Koszty zmieniają się – przed startem sprawdź je w [kalkulatorze Azure](https://azure.microsoft.com/pricing/calculator/)
> i ustaw alert budżetowy (krok 7).

## Ograniczenia Azure for Students (sprawdzone)

- Zasoby można tworzyć tylko w regionach `francecentral`, `austriaeast`, `germanywestcentral`, `swedencentral`,
  `belgiumcentral` (polityka „Allowed resource deployment regions”).
- PostgreSQL Flexible Server B1ms jest zablokowany w `germanywestcentral` i niedostępny w `swedencentral`
  (`SkuNotAvailable`), działa w **`francecentral`** – dlatego cały projekt stoi w France Central.
- RabbitMQ: CloudAMQP, plan darmowy *Little Lemur*, Azure / West Europe (konto CloudAMQP, nie nasza subskrypcja).

## Szybka ścieżka: skrypt

Po utworzeniu grupy zasobów i środowiska (kroki 1–2) bazę i aplikacje tworzy lub aktualizuje skrypt. Pyta o adres
RabbitMQ i token GitHub, generuje hasło do bazy i zapisuje wszystko wyłącznie w sekretach Azure. Można go uruchamiać
wielokrotnie, np. po wygaśnięciu tokenu GitHub:

```powershell
powershell -ExecutionPolicy Bypass -File .\infra\utworz-aplikacje.ps1
```

Poniżej te same kroki ręcznie. Polecenia uruchamiasz lokalnie (Azure CLI po `az login`) albo w **Azure Cloud Shell**
(portal Azure → `>_` → Bash).

## 1. Zmienne

```bash
RG=kwatermistrz-rg
LOC=francecentral         # patrz „Ograniczenia Azure for Students”
ENV=kw-env
PG=kw-pg-$RANDOM          # nazwa serwera musi być unikalna w całym Azure
PG_HASLO='<silne-haslo>'  # nie commituj go do repozytorium
GH_LOGIN='<login-github>'
IMG="ghcr.io/<wlasciciel>/<repo>:latest"   # małymi literami
```

## 2. Grupa zasobów, baza, środowisko

```bash
az group create -n $RG -l $LOC

az postgres flexible-server create -g $RG -n $PG -l $LOC \
  --tier Burstable --sku-name Standard_B1ms --storage-size 32 --version 16 \
  --admin-user kwadmin --admin-password "$PG_HASLO" --public-access 0.0.0.0
az postgres flexible-server db create -g $RG -s $PG -n kwatermistrz

az containerapp env create -g $RG -n $ENV -l $LOC
```

`--public-access 0.0.0.0` dopuszcza połączenia z usług Azure (w tym Container Apps).

## 3. RabbitMQ

**Wariant prosty (polecany na start):** załóż bezpłatną instancję na [cloudamqp.com](https://www.cloudamqp.com/)
i skopiuj adres `amqps://…`.

**Wariant w całości w Azure:** RabbitMQ jako kontener z wejściem TCP. Container Apps obsługuje wejście TCP tylko
w środowisku z własną siecią VNet, więc środowisko z kroku 2 trzeba utworzyć z parametrem
`--infrastructure-subnet-resource-id`. To więcej konfiguracji i stały koszt kolejnego kontenera.

```bash
AMQP_URL='amqps://<uzytkownik>:<haslo>@<host>/<vhost>'
DB_URL="postgresql+psycopg://kwadmin:${PG_HASLO}@${PG}.postgres.database.azure.com:5432/kwatermistrz?sslmode=require"
```

## 4. Dostęp do obrazów w ghcr.io

Pierwszy push na `main` buduje obraz `ghcr.io/<wlasciciel>/<repo>:latest`. Pakiet jest domyślnie prywatny – utwórz
token GitHub (Settings → Developer settings → Personal access tokens → *classic*, uprawnienie `read:packages`):

```bash
GH_TOKEN='<token-read-packages>'
```

## 5. Aplikacje

```bash
az containerapp create -g $RG -n kw-api --environment $ENV --image "$IMG" \
  --registry-server ghcr.io --registry-username "$GH_LOGIN" --registry-password "$GH_TOKEN" \
  --target-port 8000 --ingress external --min-replicas 0 --max-replicas 2 --cpu 0.25 --memory 0.5Gi \
  --secrets db-url="$DB_URL" amqp-url="$AMQP_URL" \
  --env-vars DATABASE_URL=secretref:db-url AMQP_URL=secretref:amqp-url

az containerapp create -g $RG -n kw-agenci --environment $ENV --image "$IMG" \
  --registry-server ghcr.io --registry-username "$GH_LOGIN" --registry-password "$GH_TOKEN" \
  --min-replicas 1 --max-replicas 1 --cpu 0.25 --memory 0.5Gi \
  --command "kwatermistrz-agenci" \
  --secrets db-url="$DB_URL" amqp-url="$AMQP_URL" \
  --env-vars DATABASE_URL=secretref:db-url AMQP_URL=secretref:amqp-url

az containerapp show -g $RG -n kw-api --query properties.configuration.ingress.fqdn -o tsv
```

Ostatnie polecenie wypisze adres API – dokumentacja pod `https://<adres>/docs`.

## 6. Automatyczne wdrażanie z GitHub Actions

Job `deploy` w `.github/workflows/ci.yml` loguje się do Azure bez haseł (OIDC):

```bash
APP_ID=$(az ad app create --display-name kwatermistrz-github --query appId -o tsv)
az ad sp create --id $APP_ID
az role assignment create --assignee $APP_ID --role Contributor \
  --scope $(az group show -n $RG --query id -o tsv)
az ad app federated-credential create --id $APP_ID --parameters '{
  "name": "github-production",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<wlasciciel>/<repo>:environment:production",
  "audiences": ["api://AzureADTokenExchange"]
}'
echo "AZURE_CLIENT_ID=$APP_ID"
echo "AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID=$(az account show --query id -o tsv)"
```

W repozytorium: Settings → Secrets and variables → Actions → **Variables**: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP` (= `kwatermistrz-rg`).

**Jeśli `az ad app create` zwróci brak uprawnień** (konta uczelniane często nie mogą rejestrować aplikacji),
pipeline nadal testuje i buduje obrazy, a wdrożenie robisz ręcznie: `./infra/deploy.sh ghcr.io/<wlasciciel>/<repo>:<sha>`.

## 7. Kontrola kosztów

Portal Azure → **Cost Management → Budgets → Add**: budżet np. 80 USD z alertami przy 50% i 80%.
Po zajęciach możesz zatrzymać koszty: `az containerapp update -g $RG -n kw-agenci --min-replicas 0`
oraz `az postgres flexible-server stop -g $RG -n $PG`.
