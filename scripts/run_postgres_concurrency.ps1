param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Python)) { throw "Python executable not found: $Python. Activate the project environment or pass -Python." }
if (-not $env:DATABASE_URL) { throw "Set DATABASE_URL to the dedicated bookmyseat_concurrency PostgreSQL database." }
if ($env:BOOKMYSEAT_POSTGRES_DISPOSABLE -ne "YES") { throw "For this disposable run only, set BOOKMYSEAT_POSTGRES_DISPOSABLE=YES." }
if (-not $env:DJANGO_SECRET_KEY) { $env:DJANGO_SECRET_KEY = (& $Python -c "import secrets; print(secrets.token_urlsafe(64))").Trim() }
$env:DJANGO_DEBUG = "true"

& $Python .\scripts\preflight_postgres_harness.py --mode concurrency
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Django creates a fresh test_bookmyseat_concurrency database from template0,
# applies the full migration chain, runs TransactionTestCase workers, and drops
# only that derived test database after the run. Do not add --keepdb or --noinput.
& $Python manage.py test movies.postgres_concurrency_tests --verbosity 2
exit $LASTEXITCODE
