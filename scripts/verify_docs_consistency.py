#!/usr/bin/env python
"""Check documentation consistency and cross-check counts when local audit logs exist.

Run from the repository root:  python scripts/verify_docs_consistency.py
Exit status 0 = consistent, 1 = at least one problem (each is printed).
Raw audit logs are intentionally excluded from the public package, so count parsing
is skipped when those local-only files are absent.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "evidence" / "final-audit-20261024-rerun"
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
problems = []


def parse(log):
    text = (EVIDENCE / log).read_text(encoding="utf-8", errors="replace")
    ran = re.search(r"^Ran (\d+) tests? in", text, re.M)
    status = re.search(r"^(OK|FAILED)(?: \(([^)]*)\))?", text, re.M)
    if not ran or not status:
        problems.append(f"{log}: cannot find the unittest summary")
        return None
    return {"ran": int(ran.group(1)), "ok": status.group(1) == "OK", "detail": status.group(2) or ""}


expected_logs = ["sqlite-full-suite-final.txt", "postgres-full-suite-final.txt", "postgres-concurrency-final.txt"]
if all((EVIDENCE / name).is_file() for name in expected_logs):
    sqlite = parse(expected_logs[0])
    postgres = parse(expected_logs[1])
    conc = parse(expected_logs[2])
else:
    sqlite = postgres = conc = None
    print("Local-only raw audit logs are absent; historical test counts were not cross-checked.")

if sqlite and postgres and conc:
    if not sqlite["ok"] or "skipped" in sqlite["detail"]:
        problems.append(f"SQLite log is not a clean pass: {sqlite}")
    if not postgres["ok"] or postgres["detail"] != "skipped=1":
        problems.append(f"PostgreSQL log is not 'OK (skipped=1)': {postgres}")
    if not conc["ok"] or conc["ran"] != 8 or conc["detail"]:
        problems.append(f"Concurrency log is not a clean 8/8: {conc}")
    if sqlite["ran"] != postgres["ran"]:
        problems.append("SQLite and PostgreSQL suites ran different test counts")
    total = sqlite["ran"]
    print(f"Evidence: SQLite {sqlite['ran']} OK | PostgreSQL {postgres['ran']} {postgres['detail']} | concurrency {conc['ran']} OK")

    # Every test-count claim in the docs must equal the evidence; older counts are stale.
    count_claim = re.compile(r"\b(\d{2,3})\s+(?:passed|run|tests)\b")
    for doc in DOCS:
        for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for match in count_claim.finditer(line):
                if re.search(r"earlier|superseded|original|historical|baseline|previous", line[max(0, match.start() - 60):match.end() + 20], re.I):
                    continue  # this number deliberately describes an older run
                value = int(match.group(1))
                if value != total and value not in (8, 17, 30, 100, 126, 127) and 90 <= value <= 130:
                    problems.append(f"{doc.relative_to(ROOT)}:{number}: stale test count {value} (evidence says {total})")

STALE = [
    r"PostgreSQL concurrency NOT VERIFIED", r"remains unrun", r"not been executed against PostgreSQL",
    r"PostgreSQL race suite not run", r"PostgreSQL NOT RUN", r"race with payment confirmation/refund webhook is \*\*NOT RUN",
    r"Codex did not rerun", r"user-reported PostgreSQL concurrency", r"not rerun in this audit",
    r"\*\*Benchmark:\*\* not rerun", r"PostgreSQL-only module skipped 8/8 on SQLite;", r"real Redis worker/beat startup",
    r"Redis/SMTP E2E NOT RUN", r"worker NOT RUN", r"real worker NOT VERIFIED", r"Node v26", r"BLOCKED pending disposable PostgreSQL",
    r"Actual PostgreSQL execution is still NOT RUN", r"\*\*NOT RUN:\*\* all 8 PostgreSQL",
    r"locking tests are NOT RUN", r"Task 6 PostgreSQL locking and live Celery",
    r"PostgreSQL[^.\n|]{0,80}\b(?:NOT RUN|not run)\b", r"\b(?:NOT RUN|not run)\b[^.\n|]{0,40}against PostgreSQL",
    r"Celery[^.\n|]{0,60}\b(?:NOT RUN|not run)\b",
]
for doc in DOCS:
    text = doc.read_text(encoding="utf-8")
    for pattern in STALE:
        for match in re.finditer(pattern, text):
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{doc.relative_to(ROOT)}:{line}: stale phrase /{pattern}/")

REQUIRED = {
    "README.md": ["8/8", "NOT VERIFIED", "Razorpay", "SMTP", "Docker", "rotat"],
    "docs/TEST_REPORT.md": ["8 passed", "Razorpay", "Docker", "SMTP"],
    "docs/IMPLEMENTATION_STATUS.md": ["8/8", "Razorpay", "SMTP", "Docker", "rotate"],
    "docs/TRACEABILITY.md": ["8/8", "Razorpay", "SMTP", "Docker", "superseded"],
    "docs/LOCAL_VERIFICATION_CHECKLIST.md": ["8 passed", "Razorpay", "SMTP", "Docker", "Credential rotation"],
}
for name, needles in REQUIRED.items():
    text = (ROOT / name).read_text(encoding="utf-8")
    for needle in needles:
        if needle.lower() not in text.lower():
            problems.append(f"{name}: missing required statement containing '{needle}'")

# Referenced evidence files must exist.
reference = re.compile(r"`?((?:docs/)?evidence/[A-Za-z0-9_.\-/]+\.(?:txt|json|log|md))`?")
for doc in DOCS:
    for match in reference.finditer(doc.read_text(encoding="utf-8")):
        relative = match.group(1)
        path = ROOT / relative if relative.startswith("docs/") else ROOT / "docs" / relative
        if not path.exists() and not (relative.startswith("docs/evidence/") or relative.startswith("evidence/")):
            problems.append(f"{doc.relative_to(ROOT)}: references missing evidence file {relative}")

# Evidence and docs must not carry credentials.
secret = re.compile(r"postgres(?:ql)?://[^:@/\s]+:(?!\*\*\*|PASSWORD|<)[^@\s]{4,}@")
for path in list(EVIDENCE.glob("*")) + DOCS:
    if path.is_file() and secret.search(path.read_text(encoding="utf-8", errors="replace")):
        problems.append(f"{path.relative_to(ROOT)}: contains a credential-bearing database URL")

if problems:
    print(f"{len(problems)} problem(s):")
    for item in problems:
        print(" -", item)
    sys.exit(1)
print("Documentation is consistent with the bundled evidence.")
