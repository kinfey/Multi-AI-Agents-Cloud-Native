<#
.SYNOPSIS
    Build and push the podcast pipeline container image to ACR via `az acr build`.

.DESCRIPTION
    Uses ACR's server-side builder, so a local Docker daemon is not required.
    The image installs hyperlight-sandbox from PyPI and extracts the
    prebuilt python-sandbox.aot at build time (no Rust toolchain involved).

    On Windows we also force UTF-8 console encoding to work around the
    `az acr build` cp1252 UnicodeEncodeError when the build log contains
    non-ASCII characters.

.EXAMPLE
    $env:ACR_NAME = "myregistry"
    $env:RG       = "my-rg"
    $env:IMAGE_TAG = "v0.1.0"
    .\Infra\scripts\build-and-push.ps1
#>
[CmdletBinding()]
param(
    [string]$AcrName    = $env:ACR_NAME,
    [string]$ResourceGroup = $env:RG,
    [string]$ImageName  = ($env:IMAGE_NAME ?? "fifa-2026-podcast"),
    [string]$ImageTag   = ($env:IMAGE_TAG ?? "latest")
)

$ErrorActionPreference = "Stop"
if (-not $AcrName)        { throw "Set -AcrName or `$env:ACR_NAME (e.g. 'myregistry')." }
if (-not $ResourceGroup)  { throw "Set -ResourceGroup or `$env:RG (the RG that owns the ACR)." }

# UTF-8 console — works around `az acr build` log streaming on Windows.
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$registry  = "$AcrName.azurecr.io"
$fullImage = "$registry/$ImageName`:$ImageTag"
$repoRoot  = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Push-Location $repoRoot
try {
    Write-Host "==> az acr build $fullImage"
    az acr build `
        --registry $AcrName `
        --resource-group $ResourceGroup `
        --image "$ImageName`:$ImageTag" `
        --file Infra/Dockerfile `
        .
    if ($LASTEXITCODE -ne 0) { throw "az acr build failed (exit $LASTEXITCODE)." }

    Write-Host "==> Last 3 runs:"
    az acr task list-runs -r $AcrName --top 3 -o table

    Write-Host "==> Done: $fullImage"
}
finally {
    Pop-Location
}
