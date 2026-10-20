param([switch]$View, [switch]$Verify, [string]$Python)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $candidates = @(
        (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
        (Join-Path (Split-Path -Parent $PSScriptRoot) 'tmp\native-tools\Scripts\python.exe')
    )
    $Python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $Python) {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notlike '*WindowsApps*') { $Python = $command.Source }
    }
}
if (-not $Python) { throw 'Python 3.10+ not found. Use .\run.ps1 -Python C:\path\to\python.exe' }
if ($Verify) {
    & $Python (Join-Path $PSScriptRoot 'scripts\verify.py') --renode
} elseif ($View) {
    & $Python (Join-Path $PSScriptRoot 'scripts\run_demo.py') --view
} else {
    & $Python (Join-Path $PSScriptRoot 'scripts\run_demo.py')
}
exit $LASTEXITCODE
