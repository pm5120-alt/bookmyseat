param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$EvidencePath = ""
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Python)) { throw "Python executable not found: $Python. Activate the project environment or pass -Python." }
if (-not $env:DATABASE_URL) { throw "Set DATABASE_URL to the dedicated bookmyseat_bench PostgreSQL database." }
if ($env:BOOKMYSEAT_POSTGRES_DISPOSABLE -ne "YES") { throw "For this disposable run only, set BOOKMYSEAT_POSTGRES_DISPOSABLE=YES." }
if (-not $env:DJANGO_SECRET_KEY) { $env:DJANGO_SECRET_KEY = (& $Python -c "import secrets; print(secrets.token_urlsafe(64))").Trim() }
$env:DJANGO_DEBUG = "true"

& $Python .\scripts\preflight_postgres_harness.py --mode benchmark
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $EvidencePath) {
    $EvidencePath = Join-Path "docs\evidence" ("benchmark-100000-{0}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$parent = Split-Path -Parent $EvidencePath
if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
if (Test-Path -LiteralPath $EvidencePath) { throw "Evidence file already exists; refusing to overwrite: $EvidencePath" }

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = (Resolve-Path -LiteralPath $Python).Path
$startInfo.Arguments = "manage.py benchmark_bookings --count 100000 --confirm-disposable"
$startInfo.WorkingDirectory = (Get-Location).Path
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
$startInfo.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
$startInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $startInfo
try {
    if (-not $process.Start()) { throw "Could not start the benchmark process." }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($stdout) { [Console]::Out.Write($stdout) }
    if ($stderr) { [Console]::Error.Write($stderr) }
    [System.IO.File]::WriteAllText($EvidencePath, $stdout, (New-Object System.Text.UTF8Encoding($false)))
    $result = $process.ExitCode
}
finally {
    $process.Dispose()
}
if ($result -ne 0) {
    Write-Warning "Benchmark failed with exit code $result. If seeding had started, discard and recreate only the dedicated bookmyseat_bench database before retrying. Output is at $EvidencePath."
}
exit $result
