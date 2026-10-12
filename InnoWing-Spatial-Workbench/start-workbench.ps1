$ErrorActionPreference='Stop'
$appDirectory=Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath=Join-Path $appDirectory 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Portable Python not found. See README for the Python installation option.' }
$urlFile=Join-Path $appDirectory 'data\server-url.txt'
if (Test-Path -LiteralPath $urlFile) {
    $existingUrl=(Get-Content -LiteralPath $urlFile -Raw).Trim()
    if ($existingUrl -match '^http://127\.0\.0\.1:\d+$') {
        try {
            $health=Invoke-RestMethod -Uri ($existingUrl+'/api/health') -TimeoutSec 2
            if ($health.version -eq '2.0.0' -and $health.test_instance -eq $false) { Start-Process $existingUrl; exit 0 }
        } catch {}
    }
}
$serverPath=Join-Path $appDirectory 'server.py'
Start-Process -FilePath $pythonPath -ArgumentList @('-B','-u',('"'+$serverPath+'"'),'--open') -WorkingDirectory $appDirectory -WindowStyle Hidden
Write-Output 'InnoWing workbench is starting. Your browser will open automatically.'
