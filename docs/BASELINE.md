# Baseline inspection

## Archive and working copy

Source: `djnago-bookmyshow-clone-main.zip`, copied unchanged in Downloads. Extracted working source: `outputs/BookMySeat/`.

The archive had a Django project, two apps, templates, media, a UTF-16 encoded requirements file, compiled Python cache files, and a SQLite database. The source declares Django 3.2.19 but the generated settings identify Django 5.1.1. The baseline database is copied to `backups/db.sqlite3.baseline` before edits.

## Data snapshot

Inspection used read-only SQLite queries against the working copy before migrations:

- `movies_movie`: 5 rows
- `movies_theater`: 3 rows
- `movies_seat`: 6 rows
- `movies_booking`: 5 rows
- `auth_user`: 1 row
- duplicate `movies_seat` `(theater_id, seat_number)` group: one group with 2 records

No user names, credentials, emails, or password hashes are reproduced here.

## Findings

- Movie image was required and templates accessed `.url` unconditionally.
- Theater represented a movie/time at a venue rather than a physical venue; seat and booking models did not model show-specific inventory.
- Booking made per-seat inserts with no encompassing transaction and used a one-to-one historical seat relation.
- Payment, webhook, tickets, QR verification, background workers, reviews, discovery filtering, analytics, and CSV export were absent.
- `book_seats` had undefined/mismatched variables and malformed errors.
- Settings enabled debug, contained a hardcoded secret and remote database URL, and lacked environment configuration.
- Existing DB has a duplicate legacy seat label, which means imposing a new uniqueness constraint on legacy records would not migrate safely.

## Database authority

The supplied archive SQLite file is treated as the canonical legacy dataset because it is included in the source and can be backed up and migrated locally. The hardcoded remote database URL is not contacted. Any remote database must be separately rotated and assessed by the user before it is reused.
