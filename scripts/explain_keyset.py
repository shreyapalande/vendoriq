"""EXPLAIN ANALYZE the two keyset page queries of GET /assessments on the dev database.

Inserts two scratch orgs with 3000 assessments each, runs ANALYZE so the planner
has real statistics, then prints the plan of a mid-list page (the cursor after row
200, i.e. page 11 at limit 20), once without and once with a status filter.

Leaves nothing behind: everything, including the ANALYZE statistics, runs in ONE
transaction that is always rolled back. The plans are recorded in docs/query-plans.md.

Usage (from the repo root, database running and migrated):
    .venv\\Scripts\\python scripts\\explain_keyset.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import Connection, text  # noqa: E402

from app.api.assessments import assessment_page_query  # noqa: E402
from app.db import engine  # noqa: E402
from app.models.enums import AssessmentStatus  # noqa: E402

ROWS_PER_ORG = 3000
PAGE_LIMIT = 20
CURSOR_AFTER_ROW = 200
SCRATCH_ORG_PREFIX = "EXPLAIN scratch"


def insert_scratch_org(conn: Connection, n: int):
    """One org with a user, a vendor and ROWS_PER_ORG assessments. Returns the org id."""
    org_id = conn.execute(text("INSERT INTO organizations (name) VALUES (:name) RETURNING id"),
                          {"name": f"{SCRATCH_ORG_PREFIX} {n}"}).scalar()
    user_id = conn.execute(text(
        "INSERT INTO users (org_id, email, full_name, role, password_hash) "
        "VALUES (:org_id, :email, 'Scratch', 'REQUESTER', 'not-a-real-hash') RETURNING id"),
        {"org_id": org_id, "email": f"scratch{n}@explain.example"}).scalar()
    vendor_id = conn.execute(text("INSERT INTO vendors (org_id, name) VALUES (:org_id, 'Scratch') RETURNING id"),
                             {"org_id": org_id}).scalar()
    # Statuses round-robin; one minute apart, but every 10 rows share a created_at,
    # like rows inserted in one transaction, so the id tie-break matters.
    conn.execute(text(
        "INSERT INTO assessments (org_id, vendor_id, created_by_user_id, title, status, created_at) "
        "SELECT :org_id, :vendor_id, :user_id, 'Scratch ' || i, "
        "       (CAST(:statuses AS text[]))[1 + i % 7], "
        "       now() - ((i / 10) * interval '1 minute') "
        "FROM generate_series(1, :n) AS i"),
        {"org_id": org_id, "vendor_id": vendor_id, "user_id": user_id,
         "statuses": [s.value for s in AssessmentStatus], "n": ROWS_PER_ORG})
    return org_id


def explain_page(conn: Connection, org_id, status: AssessmentStatus | None) -> None:
    # The cursor a client would send for page 11: the last row of the first 200.
    first_rows = conn.execute(assessment_page_query(org_id, status, None, CURSOR_AFTER_ROW - 1)).all()
    last = first_rows[-1]
    query = assessment_page_query(org_id, status, (last.created_at, last.id), PAGE_LIMIT)

    compiled = query.compile(dialect=conn.dialect)
    print(compiled)
    for (line,) in conn.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + str(compiled), compiled.params):
        print(line)


def main() -> None:
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            print("database:", conn.execute(text("SELECT current_database()")).scalar())
            # Two orgs, so the org_id condition has rows to exclude.
            org_id = [insert_scratch_org(conn, n) for n in range(2)][0]
            conn.execute(text("ANALYZE assessments"))
            print("assessments in table:", conn.execute(text("SELECT count(*) FROM assessments")).scalar())
            print("indexes:")
            for (definition,) in conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE tablename = 'assessments' ORDER BY indexname")
            ):
                print("   ", definition)

            print("\n===== 1. Unfiltered =====")
            explain_page(conn, org_id, None)
            print("\n===== 2. Status-filtered (IN_REVIEW) =====")
            explain_page(conn, org_id, AssessmentStatus.IN_REVIEW)
        finally:
            transaction.rollback()
        left = conn.execute(text("SELECT count(*) FROM organizations WHERE name LIKE :prefix"),
                            {"prefix": f"{SCRATCH_ORG_PREFIX}%"}).scalar()
        print(f"\nRolled back. Scratch orgs left: {left}")


if __name__ == "__main__":
    main()
