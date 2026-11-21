# Query plans: keyset pagination of GET /assessments

Reproduce with `.venv\Scripts\python scripts\explain_keyset.py` (dev database running
and migrated). The script inserts two scratch orgs with 3,000 assessments each, runs
`ANALYZE`, and prints `EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)` for page 11 (cursor after
row 200, limit 20). Everything runs in one transaction that is rolled back, so it
leaves no rows behind. It EXPLAINs `assessment_page_query()` from
`app/api/assessments.py`, the same function the endpoint calls.

Measured 2026-11-18/19 on PostgreSQL 18. UUIDs and timestamps below are shortened.

## The query

```sql
SELECT ... FROM assessments
WHERE org_id = :org_id
  [AND status = :status]                       -- optional filter
  AND (created_at, id) < (:cursor_created_at, :cursor_id)
ORDER BY created_at DESC, id DESC
LIMIT 21                                       -- limit + 1: is there a next page?
```

`(created_at, id)` is the sort key because `created_at` alone has ties (every row
inserted in one transaction gets the same `now()`). For the plan to be good, an index
must match `WHERE` equality columns first, then exactly `created_at, id`: then Postgres
can start at the cursor and read 21 entries in order, with no sort.

## 1. Unfiltered: `ix_assessments_org_id_created_at_id (org_id, created_at, id)`

Added in migration 0003. Already optimal on the first measurement:

```
Limit (actual time=0.019..0.029 rows=21.00 loops=1)
  Buffers: shared hit=3
  ->  Index Scan Backward using ix_assessments_org_id_created_at_id on assessments
        Index Cond: ((org_id = '…'::uuid) AND (ROW(created_at, id) < ROW('2026-11-19 22:35:21…'::timestamptz, '01a1f605-8f7c-…'::uuid)))
        Index Searches: 1
        Buffers: shared hit=3
Execution Time: 0.126 ms
```

- **Index Scan Backward**: the index is ascending; reading it backwards gives
  `created_at DESC, id DESC` for free, so there is no Sort node.
- **The whole row comparison is in the `Index Cond`**: the scan starts right at the
  cursor. No rows before it are read and thrown away, unlike `OFFSET 200`.
- **3 buffers, 21 rows**: the page costs the same whether it is page 1 or page 100.

## 2. Status-filtered: before and after

### Before: M3's `ix_assessments_org_id_status_created_at (org_id, status, created_at)`

```
Limit (actual time=0.080..0.084 rows=21.00 loops=1)
  Buffers: shared hit=5
  ->  Incremental Sort
        Sort Key: created_at DESC, id DESC
        Presorted Key: created_at
        Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 27kB  Peak Memory: 27kB
        ->  Index Scan Backward using ix_assessments_org_id_status_created_at on assessments (rows=22.00)
              Index Cond: ((org_id = '…'::uuid) AND ((status)::text = 'IN_REVIEW'::text) AND (created_at <= '2026-11-19 01:31:21…'::timestamptz))
              Filter: (ROW(created_at, id) < ROW('2026-11-19 01:31:21…'::timestamptz, '01a1f1ed-45ea-…'::uuid))
              Rows Removed by Filter: 1
Execution Time: 0.107 ms
```

The index has no `id`, so:

- Only the `created_at` half of the row comparison can be an index condition
  (`created_at <= cursor`). The `id` half becomes a **Filter**, so rows with the
  cursor's own timestamp are read and then discarded (`Rows Removed by Filter: 1`).
  The more rows share a timestamp, the more are read for nothing.
- The index delivers rows ordered by `created_at` only; an **Incremental Sort** orders
  ties by `id` (it sorts each group of equal `created_at` values).

Fast here, but the work grows with the number of ties at the page boundary.

### After: `ix_assessments_org_id_status_created_at_id (org_id, status, created_at, id)`

Migration 0003 replaces the 3-column index with this one. The old index's columns
are a prefix of the new one, so nothing that used it loses an index.

```
Limit (actual time=0.048..0.067 rows=21.00 loops=1)
  Buffers: shared hit=5
  ->  Index Scan Backward using ix_assessments_org_id_status_created_at_id on assessments
        Index Cond: ((org_id = '…'::uuid) AND ((status)::text = 'IN_REVIEW'::text) AND (ROW(created_at, id) < ROW('2026-11-19 20:36:21…'::timestamptz, '01a1f605-9003-…'::uuid)))
        Index Searches: 1
        Buffers: shared hit=5
Execution Time: 0.096 ms
```

The same shape as the unfiltered query: one backward index scan, the full row
comparison in the `Index Cond`, no Filter and no Sort.

## Caveats

- 6,000 rows fit in memory (every buffer is a `shared hit`). The point is the plan's
  shape, which stays the same as the table grows. The timings at this size say little.
- The audit-events list (`GET /assessments/{id}/audit-events`) has the same
  missing-`id` pattern on its M3 index, `(org_id, resource_type, resource_id,
  created_at)`. It was not changed: one assessment has only a handful of events.
