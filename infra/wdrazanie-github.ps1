# Jednorazowo: pozwala GitHub Actions wdrażać do grupy kwatermistrz-rg (logowanie OIDC, bez haseł).
# Tworzy (lub używa istniejącej) tożsamość "kwatermistrz-github" z rolą Contributor TYLKO w grupie kwatermistrz-rg.
# Wymaga: az login, gh auth login. Uruchomienie: powershell -ExecutionPolicy Bypass -File .\infra\wdrazanie-github.ps1
$ErrorActionPreference = "Stop"
$az = (Get-Command az -ErrorAction SilentlyContinue).Source
if (-not $az) { $az = "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd" }

$RG = "kwatermistrz-rg"
$repo = "karpeciak/kwatermistrz"

Write-Host "[1/5] Tożsamość kwatermistrz-github..." -ForegroundColor Cyan
$appId = & $az ad app list --display-name "kwatermistrz-github" --query "[0].appId" -o tsv
if (-not $appId) { $appId = & $az ad app create --display-name "kwatermistrz-github" --query appId -o tsv }

Write-Host "[2/5] Konto usługi..." -ForegroundColor Cyan
$sp = & $az ad sp list --filter "appId eq '$appId'" --query "[0].id" -o tsv
if (-not $sp) { & $az ad sp create --id $appId -o none; Start-Sleep -Seconds 20 }

Write-Host "[3/5] Rola Contributor w grupie $RG..." -ForegroundColor Cyan
$scope = & $az group show -n $RG --query id -o tsv
& $az role assignment create --assignee $appId --role Contributor --scope $scope -o none

Write-Host "[4/5] Zaufanie do GitHub Actions (środowisko production)..." -ForegroundColor Cyan
# GitHub podaje repozytorium w formacie z numerami kont (repo:wlasciciel@ID/repo@ID:...) albo bez nich –
# dodajemy oba warianty, żeby logowanie działało niezależnie od ustawień GitHuba.
$idWlasciciela = gh api "repos/$repo" -q ".owner.id"
$idRepo = gh api "repos/$repo" -q ".id"
$wlasciciel, $nazwa = $repo.Split("/")
$wpisy = @{
    "github-production"    = "repo:${repo}:environment:production"
    "github-production-id" = "repo:${wlasciciel}@${idWlasciciela}/${nazwa}@${idRepo}:environment:production"
}
foreach ($wpis in $wpisy.GetEnumerator()) {
    $jest = & $az ad app federated-credential list --id $appId --query "[?name=='$($wpis.Key)'].name" -o tsv
    if ($jest) { continue }
    $plik = Join-Path $env:TEMP "fc-kwatermistrz.json"
    $json = '{"name":"' + $wpis.Key + '","issuer":"https://token.actions.githubusercontent.com","subject":"' +
            $wpis.Value + '","audiences":["api://AzureADTokenExchange"]}'
    [IO.File]::WriteAllText($plik, $json)
    & $az ad app federated-credential create --id $appId --parameters "@$plik" -o none
    Remove-Item $plik
}

Write-Host "[5/5] Zmienne w repozytorium GitHub..." -ForegroundColor Cyan
gh variable set AZURE_CLIENT_ID --repo $repo --body $appId
gh variable set AZURE_TENANT_ID --repo $repo --body (& $az account show --query tenantId -o tsv)
gh variable set AZURE_SUBSCRIPTION_ID --repo $repo --body (& $az account show --query id -o tsv)
gh variable set AZURE_RESOURCE_GROUP --repo $repo --body $RG
Write-Host "Gotowe – kolejny merge do main wdroży się automatycznie." -ForegroundColor Green
