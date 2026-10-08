param([switch]$Demo, [switch]$Desktop, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }
$launchArgs = @('-m', 'meeting_assistant', '--port', "$Port")
if ($Demo) { $launchArgs += '--demo' }
if ($Desktop) { $launchArgs += '--desktop' }
& $projectPython @launchArgs
exit $LASTEXITCODE
