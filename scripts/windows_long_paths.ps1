[CmdletBinding()]
param(
    [ValidateSet('Check', 'Enable', 'Restore')]
    [string]$Action = 'Check'
)

$ErrorActionPreference = 'Stop'
$auditRegistryPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
$auditProperty = 'LongPathsEnabled'
$auditBackupPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'data\system\windows-long-paths-backup.json'
$auditSettings = Get-ItemProperty -LiteralPath $auditRegistryPath
$auditPresent = $null -ne $auditSettings.PSObject.Properties[$auditProperty]
$auditValue = if ($auditPresent) { [int]$auditSettings.$auditProperty } else { 0 }
$auditAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($Action -eq 'Check') {
    [pscustomobject]@{ LongPathsEnabled = $auditValue; Administrator = $auditAdmin; BackupPath = $auditBackupPath }
    exit 0
}
if (-not $auditAdmin) {
    throw 'Enable/Restore requires an Administrator PowerShell. Check is read-only.'
}
if ($Action -eq 'Enable') {
    if (-not (Test-Path -LiteralPath $auditBackupPath)) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $auditBackupPath) -Force | Out-Null
        @{ existed = $auditPresent; value = $auditValue } | ConvertTo-Json | Set-Content -LiteralPath $auditBackupPath -Encoding UTF8
    }
    New-ItemProperty -LiteralPath $auditRegistryPath -Name $auditProperty -Value 1 -PropertyType DWord -Force | Out-Null
} else {
    if (-not (Test-Path -LiteralPath $auditBackupPath)) { throw 'Original setting backup not found; nothing was changed.' }
    $auditOriginal = Get-Content -LiteralPath $auditBackupPath -Raw | ConvertFrom-Json
    if ($auditOriginal.value -notin @(0, 1)) { throw 'Backup has an invalid setting value.' }
    if ($auditOriginal.existed) {
        New-ItemProperty -LiteralPath $auditRegistryPath -Name $auditProperty -Value ([int]$auditOriginal.value) -PropertyType DWord -Force | Out-Null
    } else {
        Remove-ItemProperty -LiteralPath $auditRegistryPath -Name $auditProperty -ErrorAction SilentlyContinue
    }
}
Get-ItemProperty -LiteralPath $auditRegistryPath -Name $auditProperty | Select-Object LongPathsEnabled
Write-Output 'Setting updated. Restart the project processes before verification; this script does not reboot Windows.'
