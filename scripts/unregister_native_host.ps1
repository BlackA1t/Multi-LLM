[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param()

$ErrorActionPreference = 'Stop'
$hostName = 'com.blackalt.multi_llm'
$registryPath = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName"

if (-not (Test-Path -LiteralPath $registryPath)) {
    Write-Host 'Multi-LLM Native Messaging Host is not registered for the current user.'
    return
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA is required to verify the managed registration.'
}

$managedRoot = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Multi-LLM\NativeMessaging'))
$managedPrefix = $managedRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
$registeredManifest = (Get-Item -LiteralPath $registryPath).GetValue('')
if ([string]::IsNullOrWhiteSpace($registeredManifest)) {
    throw 'The registration has no manifest path; refusing to remove an unknown entry.'
}
$resolvedManifest = [System.IO.Path]::GetFullPath($registeredManifest)
if (-not $resolvedManifest.StartsWith($managedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The registered manifest is not managed by Multi-LLM; refusing to remove it.'
}
if (-not (Test-Path -LiteralPath $resolvedManifest -PathType Leaf)) {
    throw 'The managed manifest is missing; refusing to remove the registry entry automatically.'
}
$manifest = Get-Content -LiteralPath $resolvedManifest -Raw | ConvertFrom-Json
if ($manifest.name -ne $hostName -or $manifest.type -ne 'stdio') {
    throw 'The registered manifest does not identify the expected host; refusing removal.'
}

Write-Host 'Native Messaging unregistration preflight:'
Write-Host "  Manifest: $resolvedManifest"
Write-Host "  Registry: $registryPath"

if (-not $PSCmdlet.ShouldProcess($registryPath, "Unregister $hostName and remove its verified manifest")) {
    return
}

$backupManifest = "$resolvedManifest.unregister-backup-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))"
Copy-Item -LiteralPath $resolvedManifest -Destination $backupManifest
try {
    Remove-Item -LiteralPath $registryPath -Force
    Remove-Item -LiteralPath $resolvedManifest -Force
    Write-Host "Native Messaging Host unregistered. Recovery copy: $backupManifest"
} catch {
    if (-not (Test-Path -LiteralPath $resolvedManifest) -and (Test-Path -LiteralPath $backupManifest)) {
        Copy-Item -LiteralPath $backupManifest -Destination $resolvedManifest
    }
    if (-not (Test-Path -LiteralPath $registryPath)) {
        New-Item -Path $registryPath -Force | Out-Null
        Set-Item -LiteralPath $registryPath -Value $resolvedManifest
    }
    throw
}
