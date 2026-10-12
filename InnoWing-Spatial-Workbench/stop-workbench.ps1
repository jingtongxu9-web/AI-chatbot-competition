$ErrorActionPreference='Stop'
$appDirectory=Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile=Join-Path $appDirectory 'data\server-pid.txt'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No running workbench found.'; exit 0 }
$workbenchProcessId=[int](Get-Content -LiteralPath $pidFile -Raw)
$workbenchProcess=Get-Process -Id $workbenchProcessId -ErrorAction SilentlyContinue
$expectedExecutable=[IO.Path]::GetFullPath((Join-Path $appDirectory 'runtime\python.exe'))
if ($null -eq $workbenchProcess) { Write-Output 'Workbench is already stopped.'; exit 0 }
if (-not [string]::Equals($workbenchProcess.Path,$expectedExecutable,[StringComparison]::OrdinalIgnoreCase)) { throw 'The process is not this package runtime; it was not stopped.' }
Stop-Process -Id $workbenchProcessId
Write-Output 'Workbench stopped. Saved annotations remain on disk.'
