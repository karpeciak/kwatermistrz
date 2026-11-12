#!/usr/bin/env bash
# Ręczne wdrożenie nowego obrazu – gdy nie da się skonfigurować logowania GitHub → Azure (infra/azure.md, krok 6).
# Użycie (Azure Cloud Shell): ./infra/deploy.sh ghcr.io/<wlasciciel>/<repo>:<sha>
set -euo pipefail

RG="${RG:-kwatermistrz-rg}"
IMG="${1:?Podaj obraz, np. ghcr.io/wlasciciel/kwatermistrz:abc1234}"

for app in kw-api kw-agenci; do
  az containerapp update -g "$RG" -n "$app" --image "$IMG"
done
