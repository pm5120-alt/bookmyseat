"""Read-only safety checks for the disposable PostgreSQL acceptance scripts."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookmyseat.settings")
os.environ.setdefault("DJANGO_DEBUG", "true")
os.environ.setdefault("DJANGO_SECRET_KEY", "local-preflight-only-" + "x" * 64)

import django

django.setup()

from django.db import connection


EXPECTED_NAMES = {
    "concurrency": "bookmyseat_concurrency",
    "benchmark": "bookmyseat_bench",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=EXPECTED_NAMES, required=True)
    mode = parser.parse_args().mode
    if connection.vendor != "postgresql":
        raise SystemExit(f"Refusing: PostgreSQL is required (configured vendor: {connection.vendor}).")
    if os.environ.get("BOOKMYSEAT_POSTGRES_DISPOSABLE") != "YES":
        raise SystemExit("Refusing: set BOOKMYSEAT_POSTGRES_DISPOSABLE=YES only for a database created solely for this run.")

    database_name = str(connection.settings_dict["NAME"])
    expected = EXPECTED_NAMES[mode]
    if database_name != expected:
        raise SystemExit(f"Refusing: --mode {mode} requires database name {expected!r} exactly.")

    tables = set(connection.introspection.table_names())
    if mode == "concurrency":
        if tables:
            raise SystemExit("Refusing: concurrency base database must be newly created and contain no tables.")
        test_name = connection.creation._get_test_db_name()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [test_name])
            if cursor.fetchone():
                raise SystemExit(f"Refusing: derived test database {test_name!r} already exists; inspect and remove it manually if it belongs to a prior run.")
        print(f"Preflight passed: empty base database {database_name!r}; Django will create and migrate {test_name!r}, then remove that test database.")
        return

    # A migrated but empty benchmark database is accepted. System migration,
    # content-type, and permission metadata may contain rows; application,
    # session, audit-log, and user tables must contain none.
    for table in sorted(tables):
        if not table.startswith(("movies_", "users_", "auth_", "django_")):
            raise SystemExit(f"Refusing: unexpected public table {table!r} in the benchmark database.")
        if table in {"django_migrations", "django_content_type", "auth_permission"}:
            continue
        quoted = connection.ops.quote_name(table)
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT EXISTS (SELECT 1 FROM {quoted})")
            if cursor.fetchone()[0]:
                raise SystemExit(f"Refusing: table {table!r} contains rows; use a new empty benchmark database.")
    print(f"Preflight passed: benchmark database {database_name!r} has no application, user, session, or admin-log rows.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Preflight failed ({type(exc).__name__}); no database writes were made.", file=sys.stderr)
        raise
