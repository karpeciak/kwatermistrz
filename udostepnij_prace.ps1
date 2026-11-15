#requires -Version 5.1
# Publikacja wylacznie galezi Daniela; bez scalania main i bez force push.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$expectedBranch = "feature/S22IT1S0P2-27-wybor-podrozy"

function Invoke-Checked {
    param([string]$Program, [string[]]$NativeArgs)
    & $Program @NativeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Polecenie $Program zakonczylo sie bledem ($LASTEXITCODE). Publikacja przerwana."
    }
}

function Assert-WorkBranch {
    $currentBranch = & git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $currentBranch -ne $expectedBranch) {
        throw "Oczekiwana galaz: $expectedBranch. Aktualna: $currentBranch. Publikacja przerwana."
    }
    $changes = & git status --porcelain
    if ($LASTEXITCODE -ne 0 -or $changes) {
        throw "Repozytorium ma niezapisane zmiany. Sprawdz git status przed publikacja."
    }
}

Assert-WorkBranch
Invoke-Checked ".\.venv\Scripts\python.exe" @("-m", "ruff", "check", ".")
Invoke-Checked ".\.venv\Scripts\python.exe" @("-m", "ruff", "format", "--check", ".")
Invoke-Checked ".\.venv\Scripts\python.exe" @("-m", "pytest")
Invoke-Checked "git" @("fetch", "origin", "--prune")
& git merge-base --is-ancestor origin/main HEAD
if ($LASTEXITCODE -ne 0) {
    throw "Main ma nowe zmiany lub nie mozna go sprawdzic. Najpierw przeanalizuj i scal origin/main."
}
Assert-WorkBranch
Invoke-Checked "git" @("status", "--short", "--branch")
Invoke-Checked "git" @("push", "-u", "origin", $expectedBranch)
Write-Host "Galaz wyslana. Utworz pull request do main:"
Write-Host "https://github.com/karpeciak/kwatermistrz/compare/main...${expectedBranch}?expand=1"
Write-Host "Tytul: S22IT1S0P2-27: wybor wariantu podrozy w obu kierunkach"
Write-Host "Opis: docs/S22IT1S0P2-27-PR.md"
Write-Host "Scalenie wykonuje PO po zielonym CI."
