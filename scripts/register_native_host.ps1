[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-p]{32}$')]
    [string]$ExtensionId,

    [Parameter(Mandatory = $true)]
    [string]$HostExePath,

    [string]$ManifestPath,

    [switch]$AllowUpgrade
)

$ErrorActionPreference = 'Stop'
$hostName = 'com.blackalt.multi_llm'
$registryPath = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName"

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA is required for Native Messaging registration.'
}

$managedRoot = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Multi-LLM\NativeMessaging'))
$resolvedHost = [System.IO.Path]::GetFullPath($HostExePath)
if (-not [System.IO.Path]::IsPathRooted($HostExePath) -or -not (Test-Path -LiteralPath $resolvedHost -PathType Leaf)) {
    throw 'HostExePath must be an absolute path to an existing launcher.'
}
if ([System.IO.Path]::GetExtension($resolvedHost) -ne '.exe') {
    throw 'HostExePath must point to the Windows .exe launcher.'
}

if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestPath = Join-Path $managedRoot "$hostName.json"
}
$resolvedManifest = [System.IO.Path]::GetFullPath($ManifestPath)
$managedPrefix = $managedRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $resolvedManifest.StartsWith($managedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'ManifestPath must remain inside LOCALAPPDATA\Multi-LLM\NativeMessaging.'
}

$existingRegistration = $null
if (Test-Path -LiteralPath $registryPath) {
    $existingRegistration = (Get-Item -LiteralPath $registryPath).GetValue('')
    if ($existingRegistration -and $existingRegistration -ne $resolvedManifest -and -not $AllowUpgrade) {
        throw 'A different Native Messaging registration already exists. Re-run with -AllowUpgrade only after reviewing it.'
    }
    if ($existingRegistration -and $existingRegistration -eq $resolvedManifest -and
        (Test-Path -LiteralPath $resolvedManifest -PathType Leaf)) {
        try {
            $existingManifest = Get-Content -LiteralPath $resolvedManifest -Raw | ConvertFrom-Json
            if (($existingManifest.name -ne $hostName -or $existingManifest.type -ne 'stdio') -and -not $AllowUpgrade) {
                throw 'The existing manifest is not owned by Multi-LLM. Re-run with -AllowUpgrade only after reviewing it.'
            }
        } catch {
            if (-not $AllowUpgrade) {
                throw 'The existing manifest cannot be verified. Re-run with -AllowUpgrade only after reviewing it.'
            }
        }
    }
}

$manifest = [ordered]@{
    name = $hostName
    description = 'Multi-LLM local research session host'
    path = $resolvedHost
    type = 'stdio'
    allowed_origins = @("chrome-extension://$ExtensionId/")
}
$manifestJson = $manifest | ConvertTo-Json -Depth 4

Write-Host 'Native Messaging registration preflight:'
Write-Host "  Extension ID: $ExtensionId"
Write-Host "  Host launcher: $resolvedHost"
Write-Host "  Manifest: $resolvedManifest"
Write-Host "  Registry: $registryPath"

if (-not $PSCmdlet.ShouldProcess($registryPath, "Register $hostName for extension $ExtensionId")) {
    return
}

$manifestDirectory = Split-Path -Parent $resolvedManifest
$temporaryManifest = "$resolvedManifest.new"
$backupManifest = $null
$registryChanged = $false

try {
    New-Item -ItemType Directory -Path $manifestDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $resolvedManifest) {
        $backupManifest = "$resolvedManifest.backup-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))"
        Copy-Item -LiteralPath $resolvedManifest -Destination $backupManifest
    }
    $utf8NoBom = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($temporaryManifest, $manifestJson, $utf8NoBom)
    $verified = Get-Content -LiteralPath $temporaryManifest -Raw | ConvertFrom-Json
    if ($verified.name -ne $hostName -or $verified.path -ne $resolvedHost -or
        $verified.allowed_origins.Count -ne 1 -or $verified.allowed_origins[0] -ne "chrome-extension://$ExtensionId/") {
        throw 'Generated manifest failed validation.'
    }
    Move-Item -LiteralPath $temporaryManifest -Destination $resolvedManifest -Force
    New-Item -Path $registryPath -Force | Out-Null
    Set-Item -LiteralPath $registryPath -Value $resolvedManifest
    $registryChanged = $true
    Write-Host 'Native Messaging Host registered for the current user.'
} catch {
    if (Test-Path -LiteralPath $temporaryManifest) {
        Remove-Item -LiteralPath $temporaryManifest -Force
    }
    if ($backupManifest -and (Test-Path -LiteralPath $backupManifest)) {
        Copy-Item -LiteralPath $backupManifest -Destination $resolvedManifest -Force
    } elseif (Test-Path -LiteralPath $resolvedManifest) {
        Remove-Item -LiteralPath $resolvedManifest -Force
    }
    if ($registryChanged) {
        if ($existingRegistration) {
            Set-Item -LiteralPath $registryPath -Value $existingRegistration
        } elseif (Test-Path -LiteralPath $registryPath) {
            Remove-Item -LiteralPath $registryPath -Force
        }
    }
    throw
}
