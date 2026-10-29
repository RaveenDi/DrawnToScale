param([switch]$Rebuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot 'apps\web'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Set up the Python environment using README.md before starting the app.'
}
if ($Rebuild -or -not (Test-Path -LiteralPath (Join-Path $webRoot 'dist\index.html'))) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $nodePath = if ($nodeCommand) { $nodeCommand.Source } else { $null }
    $bundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
    if ($nodePath) { $nodeMajor = [int]((& $nodePath --version).TrimStart('v').Split('.')[0]) } else { $nodeMajor = 0 }
    if ($nodeMajor -lt 22 -and (Test-Path -LiteralPath $bundledNode)) { $nodePath = $bundledNode; $nodeMajor = 24 }
    if (-not $nodePath -or $nodeMajor -lt 22) { throw 'Install Node.js 22 or newer to build the interface.' }
    if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'node_modules'))) { throw 'Run npm ci in apps/web before building.' }
    Push-Location $webRoot
    try {
        & $nodePath node_modules/typescript/bin/tsc -b
        if ($LASTEXITCODE -ne 0) { throw 'Frontend type check failed.' }
        & $nodePath node_modules/vite/bin/vite.js build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
Write-Host 'Drawn to Scale: http://127.0.0.1:8000  (Ctrl+C to stop)'
Push-Location $projectRoot
try { & $pythonPath -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 }
finally { Pop-Location }
