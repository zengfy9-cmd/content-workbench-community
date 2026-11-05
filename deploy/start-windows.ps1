param([ValidateSet('default','secondary')][string]$Product = 'default', [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$pythonExe = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run deploy/install-windows.ps1 first.' }
$launchArgs = @('-m', 'content_workbench.launcher_windows', '--profile', $Product)
if ($NoBrowser) { $launchArgs += '--no-browser' }
& $pythonExe @launchArgs
if ($LASTEXITCODE -ne 0) { throw 'Startup failed; check server-error.log in your private data directory.' }
