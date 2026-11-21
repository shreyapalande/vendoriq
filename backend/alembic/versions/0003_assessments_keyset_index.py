"""assessments: indexes for the keyset-paginated list (M5)

GET /assessments runs (status filter optional)

    WHERE org_id = :org [AND status = :status] AND (created_at, id) < (:c_at, :c_id)
    ORDER BY created_at DESC, id DESC LIMIT :n

1. New (org_id, created_at, id), for the unfiltered list.
2. M3's (org_id, status, created_at) becomes (org_id, status, created_at, id), for the
   filtered list. Measured with EXPLAIN ANALYZE: without id in the index, Postgres
   could only use created_at from the row comparison as an index condition (the rest
   became a Filter), and needed an Incremental Sort to order ties by id. With id in
   the index, both queries are a single backward index scan, with no sort and with
   the whole row comparison in the index condition.

The old 3-column index is dropped: the 4-column one serves every query it served,
since (org_id, status, created_at) is a prefix of it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_assessments_org_id_created_at_id", "assessments", ["org_id", "created_at", "id"])
    op.drop_index("ix_assessments_org_id_status_created_at", table_name="assessments")
    op.create_index(
        "ix_assessments_org_id_status_created_at_id", "assessments", ["org_id", "status", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assessments_org_id_status_created_at_id", table_name="assessments")
    op.create_index("ix_assessments_org_id_status_created_at", "assessments", ["org_id", "status", "created_at"])
    op.drop_index("ix_assessments_org_id_created_at_id", table_name="assessments")
