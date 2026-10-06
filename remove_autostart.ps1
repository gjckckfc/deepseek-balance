# Remove auto-start shortcut:
#   powershell -ExecutionPolicy Bypass -File remove_autostart.ps1
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'DeepSeek-Balance-Widget.lnk'
if (Test-Path -LiteralPath $lnkPath) {
    Remove-Item -LiteralPath $lnkPath
    Write-Host 'Auto-start shortcut removed.'
} else {
    Write-Host 'Auto-start shortcut not found.'
}
