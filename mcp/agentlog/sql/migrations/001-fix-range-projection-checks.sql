-- ===========================================================================
-- agentlog migration 001 -- tighten the typed-projection CHECKs on refs.
--
-- Apply to any agentlog schema created from create_tables.sql BEFORE
-- 2026-11-19. A fresh run of the current create_tables.sql already has this.
--
-- Run as a member of admin_role:
--   psql "host=<db-host> port=<db-port> dbname=<db-name>" \
--        -f 001-fix-range-projection-checks.sql
-- Safe to re-run.
--
-- WHAT WAS WRONG
-- The original pair was
--     CHECK ((ref_type = 'run')  = (run_lo  IS NOT NULL AND run_hi  IS NOT NULL))
--     CHECK ((ref_type = 'time') = (time_lo IS NOT NULL AND time_hi IS NOT NULL))
-- which is only an equality against a CONJUNCTION. For a type that should
-- carry neither column, setting exactly one of the two leaves the conjunction
-- false, so the equality holds and the row is accepted:
--
--     INSERT INTO agentlog.refs (ref_type, ref_key, time_lo, time_hi, run_lo)
--     VALUES ('time', '2026-11-12T03:00:00Z/2026-11-12T09:00:00Z',
--             '2026-11-12T03:00:00Z', '2026-11-12T09:00:00Z', 10542);
--
-- The ordering CHECKs do not catch it either: run_hi >= run_lo evaluates to
-- NULL when run_hi is NULL, and a CHECK passes on NULL. So the row lands with
-- a half-filled run range that no range query can match on (every run query
-- needs both columns) and no display will show. A silently-invisible
-- projection is precisely the failure these constraints exist to prevent.
--
-- THE FIX
-- Count the non-nulls instead, which pins the number exactly: a run ref has
-- both run columns and no time columns, a time ref has both time columns and
-- no run columns, and every other ref_type has none of the four.
--
-- Validation is immediate (no NOT VALID): the ADD CONSTRAINT takes an ACCESS
-- EXCLUSIVE lock and scans the table, which is what we want while the log is
-- new -- a pre-existing bad row should stop this script rather than be
-- grandfathered in. If it does fail, the offending rows are:
--
--   SELECT ref_id, ref_type, ref_key, run_lo, run_hi, time_lo, time_hi
--     FROM agentlog.refs
--    WHERE num_nonnulls(run_lo,  run_hi)  <> CASE WHEN ref_type = 'run'  THEN 2 ELSE 0 END
--       OR num_nonnulls(time_lo, time_hi) <> CASE WHEN ref_type = 'time' THEN 2 ELSE 0 END;
-- ===========================================================================

SET ROLE admin_role;

BEGIN;

ALTER TABLE agentlog.refs DROP CONSTRAINT IF EXISTS refs_run_cols;
ALTER TABLE agentlog.refs DROP CONSTRAINT IF EXISTS refs_time_cols;

ALTER TABLE agentlog.refs ADD CONSTRAINT refs_run_cols
    CHECK (num_nonnulls(run_lo,  run_hi)  = CASE WHEN ref_type = 'run'  THEN 2 ELSE 0 END);

ALTER TABLE agentlog.refs ADD CONSTRAINT refs_time_cols
    CHECK (num_nonnulls(time_lo, time_hi) = CASE WHEN ref_type = 'time' THEN 2 ELSE 0 END);

COMMIT;

RESET ROLE;

-- Confirm what is now in force:
--   SELECT conname, pg_get_constraintdef(oid)
--     FROM pg_constraint
--    WHERE conrelid = 'agentlog.refs'::regclass AND contype = 'c'
--    ORDER BY conname;
