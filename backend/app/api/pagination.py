"""Keyset pagination on (created_at, id).

A page query looks like this (newest first):

    SELECT ... FROM assessments
    WHERE org_id = :org_id
      AND (created_at, id) < (:cursor_created_at, :cursor_id)   -- only after page 1
    ORDER BY created_at DESC, id DESC
    LIMIT :limit + 1

- (created_at, id) is unique and totally ordered, so every row has exactly one place
  in the order. created_at alone is not enough: all rows inserted in one transaction
  share the same now(), and ties would be cut arbitrarily (duplicates or gaps
  between pages). id breaks the tie.
- The row comparison (a, b) < (x, y) means a < x OR (a = x AND b < y): "strictly
  after the last row of the previous page", in the page's order.
- Unlike OFFSET, the database does not read and throw away the earlier pages; with
  an index on (org_id, created_at, id) it starts right at the cursor. And rows
  inserted meanwhile do not shift later pages.
- One extra row (LIMIT + 1) tells us whether there is a next page, without a COUNT.

The cursor is the last row's (created_at, id), base64url-encoded so clients treat
it as opaque. It carries no secret; a client that edits it only gets a different
page of its own org's rows, because the org filter is always applied as well.
"""

import base64
import binascii
import uuid
from datetime import datetime

from app.errors import ApiError

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """(created_at, id) from a cursor, or 422 invalid_cursor."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at_text, id_text = raw.split("|")
        created_at = datetime.fromisoformat(created_at_text)
        row_id = uuid.UUID(id_text)
    except (binascii.Error, UnicodeError, ValueError):
        raise ApiError(422, "invalid_cursor", "Invalid cursor") from None
    if created_at.tzinfo is None:  # created_at is timestamptz; a naive value is not one of ours
        raise ApiError(422, "invalid_cursor", "Invalid cursor")
    return created_at, row_id


def split_page[R](rows: list[R], limit: int) -> tuple[list[R], str | None]:
    """rows came from a query with LIMIT limit + 1. Returns (this page, next cursor
    or None). Each row needs .created_at and .id."""
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    return page, encode_cursor(page[-1].created_at, page[-1].id)
