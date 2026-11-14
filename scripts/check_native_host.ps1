[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-p]{32}$')]
    [string]$ExtensionId
)

$ErrorActionPreference = 'Stop'
$hostName = 'com.blackalt.multi_llm'
$registryPath = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$probe = Join-Path $PSScriptRoot 'native_host_probe.py'

if (-not (Test-Path -LiteralPath $registryPath)) {
    throw 'Native Messaging Host is not registered in HKCU.'
}
$manifestPath = (Get-Item -LiteralPath $registryPath).GetValue('')
if ([string]::IsNullOrWhiteSpace($manifestPath) -or -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'The HKCU registration does not point to an existing manifest.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$expectedOrigin = "chrome-extension://$ExtensionId/"
if ($manifest.name -ne $hostName -or $manifest.type -ne 'stdio') {
    throw 'The registered manifest has an unexpected name or type.'
}
if ($manifest.allowed_origins.Count -ne 1 -or $manifest.allowed_origins[0] -ne $expectedOrigin) {
    throw 'allowed_origins does not exactly match the supplied extension ID.'
}
if (-not [System.IO.Path]::IsPathRooted($manifest.path) -or -not (Test-Path -LiteralPath $manifest.path -PathType Leaf)) {
    throw 'The manifest does not point to an existing absolute Host launcher.'
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The project Python environment is unavailable.'
}

Write-Host 'Registration checks: PASS'
Write-Host "  Extension origin: $expectedOrigin"
Write-Host "  Manifest: $manifestPath"
Write-Host "  Host launcher: $($manifest.path)"
& $python $probe --host-exe $manifest.path
if ($LASTEXITCODE -ne 0) {
    throw 'Native Host stdio probe failed.'
}
