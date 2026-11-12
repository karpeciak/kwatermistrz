# Tworzy bazę PostgreSQL i aplikacje kw-api oraz kw-agenci w istniejącym środowisku Container Apps.
# Sekrety (adres RabbitMQ, token GitHub) są wpisywane w terminalu i trafiają wyłącznie do sekretów Azure.
# Uruchomienie (PowerShell, z katalogu repozytorium):  .\infra\utworz-aplikacje.ps1

$ErrorActionPreference = "Stop"

$RG = "kwatermistrz-rg"
# Azure for Students dopuszcza tylko: francecentral, austriaeast, germanywestcentral, swedencentral, belgiumcentral.
# PostgreSQL B1ms był niedostępny w germanywestcentral i swedencentral – całość stoi w francecentral.
$LOC = "francecentral"
$ENVNAME = "kw-env"
$IMG = "ghcr.io/karpeciak/kwatermistrz:latest"
$GH_LOGIN = "karpeciak"

$az = (Get-Command az -ErrorAction SilentlyContinue).Source
if (-not $az) { $az = "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd" }

function Odczytaj-Sekret([string]$opis) {
    $s = Read-Host -AsSecureString $opis
    [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}

$AMQP_URL = Odczytaj-Sekret "Wklej AMQP URL z CloudAMQP (amqps://...)"
$GH_TOKEN = Odczytaj-Sekret "Wklej token GitHub z uprawnieniem read:packages"
if (-not $AMQP_URL.StartsWith("amqp")) { throw "Adres RabbitMQ powinien zaczynać się od amqps://" }

# Hasło do bazy: tylko litery i cyfry, żeby nie wymagało kodowania w adresie połączenia
$znaki = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
$PG_HASLO = "Kw" + (-join (1..22 | ForEach-Object { $znaki | Get-Random })) + "7"
# Serwer bazy może już istnieć (utworzony wcześniej) – wtedy tylko ustawiamy mu nowe hasło
$PG = & $az postgres flexible-server list -g $RG --query "[0].name" -o tsv
if ($PG) {
    Write-Host "`n[1/4] Baza $PG już istnieje – ustawianie nowego hasła..." -ForegroundColor Cyan
    & $az postgres flexible-server update -g $RG -n $PG --admin-password $PG_HASLO --only-show-errors -o none
    if ($LASTEXITCODE) { throw "Nie udało się ustawić hasła bazy" }
} else {
    $PG = "kw-pg-" + (Get-Random -Minimum 10000 -Maximum 99999)
    Write-Host "`n[1/4] Tworzenie bazy PostgreSQL $PG (kilka minut)..." -ForegroundColor Cyan
    & $az postgres flexible-server create -g $RG -n $PG -l $LOC `
        --tier Burstable --sku-name Standard_B1ms --storage-size 32 --version 16 `
        --admin-user kwadmin --admin-password $PG_HASLO --public-access 0.0.0.0 `
        --yes --only-show-errors -o none
    if ($LASTEXITCODE) { throw "Nie udało się utworzyć serwera PostgreSQL" }
}
$istnieje = & $az postgres flexible-server db list -g $RG -s $PG --query "[?name=='kwatermistrz'].name" -o tsv
if (-not $istnieje) { & $az postgres flexible-server db create -g $RG -s $PG -n kwatermistrz --only-show-errors -o none }

$DB_URL = "postgresql+psycopg://kwadmin:$PG_HASLO@$PG.postgres.database.azure.com:5432/kwatermistrz?sslmode=require"

$wersja = "r" + (Get-Date -Format "MMddHHmmss")   # nowa rewizja, żeby aplikacja wczytała nowe sekrety

function Utworz-LubZaktualizuj([string]$nazwa, [string[]]$tylkoPrzyTworzeniu, [string[]]$polecenie) {
    $jest = & $az containerapp list -g $RG --query "[?name=='$nazwa'].name" -o tsv
    if ($jest) {
        Write-Host "    $nazwa istnieje – aktualizacja sekretów i obrazu"
        & $az containerapp secret set -g $RG -n $nazwa --secrets "db-url=$DB_URL" "amqp-url=$AMQP_URL" --only-show-errors -o none
        & $az containerapp registry set -g $RG -n $nazwa --server ghcr.io --username $GH_LOGIN --password $GH_TOKEN --only-show-errors -o none
        & $az containerapp update -g $RG -n $nazwa --image $IMG --revision-suffix $wersja @polecenie --only-show-errors -o none
    } else {
        & $az containerapp create -g $RG -n $nazwa --environment $ENVNAME --image $IMG `
            --registry-server ghcr.io --registry-username $GH_LOGIN --registry-password $GH_TOKEN `
            --cpu 0.25 --memory 0.5Gi `
            --secrets "db-url=$DB_URL" "amqp-url=$AMQP_URL" `
            --env-vars "DATABASE_URL=secretref:db-url" "AMQP_URL=secretref:amqp-url" `
            @tylkoPrzyTworzeniu @polecenie --only-show-errors -o none
    }
    if ($LASTEXITCODE) { throw "Nie udało się przygotować $nazwa" }
}

Write-Host "[2/4] Aplikacja kw-api..." -ForegroundColor Cyan
Utworz-LubZaktualizuj "kw-api" @("--target-port", "8000", "--ingress", "external", "--min-replicas", "0", "--max-replicas", "2") @()

Write-Host "[3/4] Aplikacja kw-agenci..." -ForegroundColor Cyan
Utworz-LubZaktualizuj "kw-agenci" @("--min-replicas", "1", "--max-replicas", "1") `
    @("--command", "kwatermistrz-agenci")

$FQDN = & $az containerapp show -g $RG -n kw-api --query properties.configuration.ingress.fqdn -o tsv
Write-Host "[4/4] Gotowe." -ForegroundColor Green
Write-Host "API:            https://$FQDN"
Write-Host "Dokumentacja:   https://$FQDN/docs"
Write-Host "Serwer bazy:    $PG (użytkownik kwadmin; hasło zapisane tylko w sekrecie db-url aplikacji)"