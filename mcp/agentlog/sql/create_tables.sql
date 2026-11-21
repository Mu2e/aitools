-- ===========================================================================
-- agentlog-mcp schema
--     Draft 3 -- 2026-11-13. Simplified after review: refs narrowed to
--     subject references only (decision 1), reports trimmed to what a display
--     actually filters on (decision 12). See DESIGN DECISIONS at the bottom.
--     Run against the AI database 2026-11-19.
--     2026-11-19: refs_run_cols / refs_time_cols tightened to num_nonnulls
--     (decision 3). A database created from the earlier version needs
--     migrations/001-fix-range-projection-checks.sql; a fresh run of this
--     file already has the fix.
--
-- Run once, by a member of admin_role (owner of the memory/usage schemas):
--   psql "host=<db-host> port=<db-port> dbname=<db-name>" -f create_tables.sql
-- Safe to re-run. Connection details are deliberately not recorded in this
-- repository -- take them from the deployment notes.
--
-- WHAT THIS IS
-- A place for agents to file a report of an investigation. The motivating
-- flow: an agent is woken by an alarm (or a cron, or a human), works the
-- problem with whatever tools it has -- dqm, runs, metacat, ecl, kb -- and
-- then writes down what it found. Displays, digests, Slack summaries and
-- later agents read those reports back out.
--
-- Three tables:
--   agentlog.reports      the report itself (one row per filed report)
--   agentlog.refs         deduplicated catalog of things a report is ABOUT
--   agentlog.report_refs  which reports are about which refs
--
-- WHAT refs IS FOR, AND WHAT IT IS NOT FOR
-- One question only: "which reports are relevant to this run / this night /
-- this subsystem?" A ref is a subject handle, an index entry.
--
-- It is NOT a provenance record. "This report consulted the dqm tool with
-- these arguments" is a different concept -- it is about how the report was
-- developed, not what it is about -- and it lives in the report body, as
-- prose, where an agent explains its reasoning anyway. Nobody needs to query
-- "which reports called dqm.list_alarms". Keeping that concept out of the
-- tables is what makes this schema small, and it is what makes dedup safe:
-- a refs row is shared between reports, so it can only ever hold facts about
-- the thing itself, never facts about one report's use of it. (Draft 1 mixed
-- the two and a reviewer broke it in three steps -- see decision 1.)
--
-- An MCP server or tool can still be a ref, because it can be the SUBJECT of
-- a report: "dqm.list_alarms with this key is too slow". That is a report
-- about the tool, not a report that used the tool.
--
-- DESIGN NOTE: APPEND-ONLY, like memory and usage. The MCP's database role is
-- granted INSERT and SELECT and nothing else, on every table. A report is
-- never edited and never hidden: a correction, a follow-up or a human's
-- comment is a NEW report pointing at the old one through report_refs (roles
-- 'supersedes', 'followup_of', 'comments_on').
--
-- NOTHING IS EVER FILTERED OUT OF THE LOG. The whole trail is exposed, wrong
-- reports included -- a report that was later corrected still reads back
-- exactly as it was filed, with the correction linked to it. Displays decide
-- what to foreground; the database does not decide for them, because a row
-- that silently disappears from a log is worse than a row that is wrong.
-- agentlog.report_links exposes those report-to-report edges in one place.
-- See "Admin operations" at the bottom for the deliberately manual path to
-- actually removing content.
--
-- All times are TIMESTAMPTZ and should be written as UTC.
--
-- Requires PostgreSQL 12+ (generated column on reports.search_vec).
-- ===========================================================================

SET ROLE admin_role;

CREATE SCHEMA IF NOT EXISTS agentlog AUTHORIZATION admin_role;


-- ---------------------------------------------------------------------------
-- reports -- the main table.
--
-- ONE ROW, ONE REPORT, AND NOT MUCH ELSE. There are only two itemized,
-- searchable classifications -- `triggered_by` and `severity` -- because
-- those are what a display or a digest filters on. Everything else about the
-- report (how it was reached, which model wrote it, how confident the agent
-- is, what kind of report it is) belongs in the body, put there by the
-- prompt, in prose. See decision 12.
--
-- `author` is the name attached to the mikey bearer key the writing agent
-- authenticated with -- NOT anything the caller passes in, same rule as the
-- memory MCP. It is the accountability field: who (which key) stands behind
-- this report. `agent` is caller-supplied and is the *role* that wrote it
-- ('dqm-alarm-watch', 'nightly-validation'), which a shared key cannot tell
-- us. Expect many agents per author.
--
-- A HUMAN COMMENT IS JUST A REPORT -- triggered_by = 'human', pointing at the
-- report being commented on with role = 'comments_on'. There is no separate
-- annotation table, and deliberately so: a person saying "this was actually
-- the DAQ" is a contribution to the trail on exactly the same footing as the
-- agent's original, it wants the same references and the same display
-- treatment, and it must not be able to rewrite what the agent said. `author`
-- is still the authenticated key name, so a comment filed through a display
-- carries that display's key -- put the actual person in `agent` (or attrs)
-- if the write path knows who they are.
--
-- Content is three fields on purpose, because three different consumers want
-- three different lengths:
--   title    one line, for a list display or a Slack notification
--   summary  a short paragraph, for a digest or a hover card
--   body     the full report, markdown by convention -- including how the
--            conclusion was reached, which tools were consulted and what they
--            returned. That narrative is prose here, not rows in refs.
-- Only title and body are required; an agent that writes no summary gets
-- title used in its place by the listing views.
--
-- `attrs` is the structured escape hatch: the alarm payload, the metric values
-- the conclusion rests on, anything a downstream procedure would rather parse
-- than regex out of the body. It is safe here -- unlike in refs -- because a
-- reports row belongs to exactly one report and is shared with nothing.
--
-- The two vocabularies are TEXT + CHECK rather than enum types or lookup
-- tables: readable in a raw query, cheap to extend with ALTER TABLE, and --
-- the real reason -- *constrained*, so we do not end up with 'warning',
-- 'Warning', 'warn' and 'WARN' in one column. triggered_by has an 'other'
-- escape so an agent is never blocked.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentlog.reports (
    report_id     BIGSERIAL   PRIMARY KEY,

    -- provenance
    author        TEXT        NOT NULL,          -- mikey key name (never a tool arg)
    agent         TEXT,                          -- self-declared agent/procedure name
    triggered_by  TEXT        NOT NULL DEFAULT 'other'
                              CHECK (triggered_by IN ('alarm','schedule','human','agent','other')),

    -- classification
    severity      TEXT        NOT NULL DEFAULT 'info'
                              CHECK (severity IN ('info','warning','error','critical')),

    -- content
    title         TEXT        NOT NULL,
    summary       TEXT,
    body          TEXT        NOT NULL,
    body_format   TEXT        NOT NULL DEFAULT 'markdown'
                              CHECK (body_format IN ('markdown','text','json')),
    tags          TEXT[],
    attrs         JSONB,

    create_time   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Full-text search target, maintained by the database so no writer can
    -- forget it. English stemming on title+summary+body. This is how
    -- "which reports mention the dqm tool" gets answered now that provenance
    -- lives in the body rather than in refs.
    search_vec    TSVECTOR    GENERATED ALWAYS AS (
                      to_tsvector('english',
                          coalesce(title,   '') || ' ' ||
                          coalesce(summary, '') || ' ' ||
                          coalesce(body,    ''))
                  ) STORED
);


-- ---------------------------------------------------------------------------
-- refs -- the deduplicated catalog of things reports are about. One row per
-- distinct thing, however many reports point at it. This is what makes
-- "every report about run 10542" a single indexed lookup.
--
-- IDENTITY IS (ref_type, ref_key). Nothing else. Two columns, both NOT NULL,
-- no nullable column in the unique constraint, no JSON in a btree index.
--
-- Three shapes of ref, by type:
--
--   run     a run or an inclusive run range. ref_key is the canonical text
--           ('10542' or '10500-10600') and run_lo/run_hi carry the same thing
--           as integers for range queries. A single run has run_lo = run_hi.
--
--   time    an instant or an interval. ref_key is canonical ISO-8601 UTC
--           ('2026-11-12T03:14:00Z', or '<from>/<to>' for an interval) and
--           time_lo/time_hi carry the same thing as timestamps. An instant
--           has time_lo = time_hi.
--
--   everything else   ref_key alone, a canonical text handle:
--     system    subsystem or service, lowercase as used elsewhere in the
--               Mu2e schemas                                  'trk'
--     metric    a DQM metric, 'groupx/subgroup/namex'          'trk/track/meanChi2'
--     alarm     a DQM alarm identity                           'trk/track/meanChi2@valNightly'
--     dataset   metacat dataset, namespace:name                'mu2e:mcs.mu2e.CeEndpoint...'
--     file      a file name                                    'nts.mu2e....root'
--     document  docdb/ecl/wiki item, 'source:id'               'docdb:12345', 'ecl:67890'
--     url       the URL itself                                 'https://...'
--     report    another agentlog report, its report_id as text '417'
--     mcp       an MCP server or tool AS A SUBJECT -- a report
--               about the tool, not a report that used it      'dqm.list_alarms'
--     free      the freeform option: ref_key is the text, and
--               `label` can carry a tidier rendering of it
--
-- The run_lo/run_hi and time_lo/time_hi columns are a redundant, queryable
-- projection of ref_key, not independent data -- they are derived from it by
-- the server, and the CHECK constraints below force them to be present for
-- exactly the type they belong to and absent everywhere else. That keeps the
-- rule that EVERY non-key column of this table is a function of the key,
-- which is what makes a shared, write-once row safe.
--
-- CANONICALIZATION IS THE MCP'S JOB. Run 10542 must always arrive as
-- ref_type='run', ref_key='10542' -- not 'Run 10542', not '010542' -- or the
-- index grows a long tail of near-duplicates and a search misses reports.
-- Keep that logic in one function per type; it is the only interesting code
-- in the write path.
--
-- `label` is a display rendering ('run 10542', 'DocDB 12345: Tracker HV
-- interlock'). It is descriptive, not identifying, and since the MCP has no
-- UPDATE the first writer of a ref sets it permanently (ON CONFLICT DO
-- NOTHING). Accepted: a stale label is a cosmetic defect, and an admin can
-- fix one by hand.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentlog.refs (
    ref_id       BIGSERIAL   PRIMARY KEY,
    ref_type     TEXT        NOT NULL
                             CHECK (ref_type IN ('run','time','system','metric','alarm',
                                                 'dataset','file','document','url',
                                                 'report','mcp','free')),
    ref_key      TEXT        NOT NULL,          -- canonical identity within ref_type
    label        TEXT,                          -- display rendering, optional

    -- Typed projection of ref_key. Set for their own ref_type, NULL otherwise.
    run_lo       INTEGER,
    run_hi       INTEGER,
    time_lo      TIMESTAMPTZ,
    time_hi      TIMESTAMPTZ,

    create_time  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (ref_type, ref_key),

    -- A run ref has BOTH run columns and no time columns; a time ref has BOTH
    -- time columns and no run columns; every other type has none of the four.
    -- This catches the server forgetting to fill the projection -- which would
    -- silently make a run invisible to every range query -- at insert time
    -- instead of at display time.
    --
    -- Counting non-nulls rather than writing `(ref_type='run') = (run_lo IS
    -- NOT NULL AND run_hi IS NOT NULL)`: that form is satisfied by setting
    -- exactly ONE of the two columns on a type that should have neither (the
    -- conjunction is false, so the equality holds), which let a half-filled
    -- range through on the wrong type. num_nonnulls pins the count exactly.
    CONSTRAINT refs_run_cols
        CHECK (num_nonnulls(run_lo,  run_hi)  = CASE WHEN ref_type = 'run'  THEN 2 ELSE 0 END),
    CONSTRAINT refs_time_cols
        CHECK (num_nonnulls(time_lo, time_hi) = CASE WHEN ref_type = 'time' THEN 2 ELSE 0 END),
    -- Ordered ranges. NULL >= NULL is NULL, and a CHECK passes on NULL, so
    -- these are automatically satisfied for the types that have no range.
    CONSTRAINT refs_run_order  CHECK (run_hi  >= run_lo),
    CONSTRAINT refs_time_order CHECK (time_hi >= time_lo)
);


-- ---------------------------------------------------------------------------
-- report_refs -- the link table. Many reports to many refs, with the *role*
-- carried on the link, because the same run can be the thing one report is
-- about and only a passing mention in another.
--
-- role vocabulary -- five values, all of them answering "how is this report
-- related to this thing":
--   subject     what the report is ABOUT. The default, and what displays
--               filter on; a report should have at least one.
--   mentions    a secondary or passing reference, worth indexing but not what
--               the report is about
--   supersedes  this report corrects/replaces the referenced report. NOTE
--               this is a claim recorded in the trail, not a delete: the
--               referenced report stays fully readable. (ref_type='report')
--   followup_of this report continues the referenced report (ref_type='report')
--   comments_on this report is a comment on the referenced report -- the
--               human annotation path (ref_type='report')
--
-- Draft 1's 'evidence', 'consulted', 'cited' and 'produced' roles are gone
-- with the provenance concept, as are its `source` and `note` columns: what
-- a report consulted, and why a reference mattered, is prose in the body.
--
-- There is no create_time here: a report's links are written in the same
-- transaction as the report itself and can never be added later, so
-- reports.create_time is already the answer.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentlog.report_refs (
    report_id    BIGINT      NOT NULL REFERENCES agentlog.reports(report_id),
    ref_id       BIGINT      NOT NULL REFERENCES agentlog.refs(ref_id),
    role         TEXT        NOT NULL DEFAULT 'subject'
                             CHECK (role IN ('subject','mentions','supersedes',
                                             'followup_of','comments_on')),
    PRIMARY KEY (report_id, ref_id, role)
);


-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- reports: the ways a display slices the log, plus free-text search.
CREATE INDEX IF NOT EXISTS reports_create_time_idx ON agentlog.reports (create_time DESC);
CREATE INDEX IF NOT EXISTS reports_author_time_idx ON agentlog.reports (author, create_time DESC);
CREATE INDEX IF NOT EXISTS reports_agent_time_idx  ON agentlog.reports (agent, create_time DESC);
CREATE INDEX IF NOT EXISTS reports_severity_idx    ON agentlog.reports (severity, create_time DESC);
CREATE INDEX IF NOT EXISTS reports_tags_idx        ON agentlog.reports USING GIN (tags);
CREATE INDEX IF NOT EXISTS reports_attrs_idx       ON agentlog.reports USING GIN (attrs);
CREATE INDEX IF NOT EXISTS reports_search_idx      ON agentlog.reports USING GIN (search_vec);

-- refs: exact lookup is covered by the UNIQUE constraint. These two serve the
-- range and overlap queries, and are partial -- only run refs have a run
-- range, so there is no point indexing the NULLs.
CREATE INDEX IF NOT EXISTS refs_run_idx  ON agentlog.refs (run_lo, run_hi)
       WHERE ref_type = 'run';
CREATE INDEX IF NOT EXISTS refs_time_idx ON agentlog.refs (time_lo, time_hi)
       WHERE ref_type = 'time';

-- report_refs: the primary key serves report -> refs. This one serves the
-- reverse ("what reports point at this ref"), which is the common display
-- query, and is a covering index for it.
CREATE INDEX IF NOT EXISTS report_refs_ref_idx ON agentlog.report_refs (ref_id, role, report_id);


-- ---------------------------------------------------------------------------
-- Views -- so "flatten the join" and "resolve report-to-report links" live in
-- one place instead of being re-derived by every display and procedure.
--
-- None of these filter anything out. agentlog.reports is the complete log and
-- stays the read target; the views only reshape it.
-- ---------------------------------------------------------------------------

-- Report-to-report edges, resolved: which report corrects, continues or
-- comments on which. One row per edge, pointing from the newer report to the
-- older one. This is how a display shows the whole trail around a report --
-- in both directions, since it can be queried on either end.
--
-- The join is on report_id::TEXT rather than casting ref_key to BIGINT, so a
-- malformed or dangling ref_key (decision 7: best effort, rare defects
-- tolerated) can never raise an error in a view other queries read through --
-- it just matches nothing.
CREATE OR REPLACE VIEW agentlog.report_links AS
    SELECT rr.report_id    AS from_report,   -- the newer report
           r.report_id     AS to_report,     -- the one it points at
           rr.role                           -- supersedes|followup_of|comments_on|...
      FROM agentlog.report_refs rr
      JOIN agentlog.refs f    ON f.ref_id = rr.ref_id
                             AND f.ref_type = 'report'
      JOIN agentlog.reports r ON r.report_id::TEXT = f.ref_key;

-- One flat row per (report, ref) for displays that want to print a report's
-- reference list, or find reports by ref, without the join of their own.
CREATE OR REPLACE VIEW agentlog.report_refs_full AS
    SELECT rr.report_id,
           rr.role,
           f.ref_id,
           f.ref_type,
           f.ref_key,
           f.label,
           f.run_lo,  f.run_hi,
           f.time_lo, f.time_hi
      FROM agentlog.report_refs rr
      JOIN agentlog.refs f ON f.ref_id = rr.ref_id;

-- Listing view: every report, with everything a list/table display needs
-- except the body, and the subjects already rolled up, so a page of 50
-- reports is one query.
--
-- `corrected_by` and `commented_by` are flags, not filters: a superseded
-- report is still listed here, with the id of what corrected it, so a display
-- can mark it ("corrected by #419") instead of pretending it never happened.
-- Empty arrays, never NULL. They are per-row subqueries, so filter or LIMIT
-- this view rather than scanning it whole.
CREATE OR REPLACE VIEW agentlog.report_index AS
    SELECT r.report_id,
           r.create_time,
           r.author,
           r.agent,
           r.triggered_by,
           r.severity,
           r.title,
           coalesce(r.summary, r.title) AS summary,
           r.tags,
           octet_length(r.body)                       AS body_size,
           count(DISTINCT rr.ref_id)                  AS n_refs,
           array_remove(array_agg(DISTINCT f.ref_type || ':' || f.ref_key)
                        FILTER (WHERE rr.role = 'subject'), NULL) AS subjects,
           ARRAY(SELECT l.from_report FROM agentlog.report_links l
                  WHERE l.to_report = r.report_id
                    AND l.role = 'supersedes'
                  ORDER BY l.from_report)                  AS corrected_by,
           ARRAY(SELECT l.from_report FROM agentlog.report_links l
                  WHERE l.to_report = r.report_id
                    AND l.role = 'comments_on'
                  ORDER BY l.from_report)                  AS commented_by
      FROM agentlog.reports r
      LEFT JOIN agentlog.report_refs rr ON rr.report_id = r.report_id
      LEFT JOIN agentlog.refs f         ON f.ref_id     = rr.ref_id
     GROUP BY r.report_id;


-- ---------------------------------------------------------------------------
-- Grants -- same split as the memory schema.
--
--   * READS run as mu2eai itself, unelevated (SELECT granted directly below).
--   * WRITES run inside a transaction under `SET LOCAL ROLE update_role`
--     (LOCAL, so the elevation reverts on commit/rollback and cannot leak to
--     the next request on a pooled connection).
--
-- The point of the split: an INSERT from a code path that was only meant to
-- read is refused by the database instead of silently succeeding. In an
-- append-only store a stray write is permanent.
--
-- Two things that are easy to miss, both of which break inserts:
--   * BIGSERIAL columns need USAGE on the underlying sequences.
--   * update_role needs SELECT as well as INSERT -- the ref dedup path reads
--     back the existing ref_id after ON CONFLICT DO NOTHING.
-- ---------------------------------------------------------------------------

-- Write path.
GRANT USAGE ON SCHEMA agentlog TO update_role;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA agentlog TO update_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA agentlog TO update_role;

-- Read path: the login role, which reads without assuming update_role.
-- (ALL TABLES covers the views too.)
GRANT USAGE ON SCHEMA agentlog TO mu2eai;
GRANT SELECT ON ALL TABLES IN SCHEMA agentlog TO mu2eai;

-- If tables are added to this schema later, both sets of grants have to be
-- reissued -- ALL TABLES is evaluated at grant time, not standing policy.
-- To make that automatic instead, as admin_role:
--   ALTER DEFAULT PRIVILEGES IN SCHEMA agentlog
--       GRANT SELECT, INSERT ON TABLES TO update_role;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA agentlog
--       GRANT SELECT ON TABLES TO mu2eai;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA agentlog
--       GRANT USAGE, SELECT ON SEQUENCES TO update_role;

RESET ROLE;


-- ===========================================================================
-- How the MCP writes a report (one transaction, for reference)
--
--   BEGIN;
--   SET LOCAL ROLE update_role;
--
--   INSERT INTO agentlog.reports
--          (author, agent, triggered_by, severity, title, summary, body,
--           tags, attrs)
--   VALUES ('mu2e-ops', 'dqm-alarm-watch', 'alarm', 'warning',
--           'trk meanChi2 alarm on 2026-11-11 is a low-stats artifact',
--           'The alarm fired on a 40-event interval ...',
--           '## What fired ... ## How I checked -- dqm.list_alarms for run '
--           '10542, then dqm.query_metrics ... ## Verdict -- benign, high '
--           'confidence: the interval is below the stats threshold ...',
--           ARRAY['trk','dqm'], '{"alarmcode": 2, "nevents": 40}'::JSONB)
--   RETURNING report_id;                                      -- -> 418
--
--   -- each subject: dedup, then link. A run ref carries the integer range.
--   INSERT INTO agentlog.refs (ref_type, ref_key, label, run_lo, run_hi)
--   VALUES ('run', '10542', 'run 10542', 10542, 10542)
--       ON CONFLICT (ref_type, ref_key) DO NOTHING;
--   SELECT ref_id FROM agentlog.refs
--    WHERE ref_type = 'run' AND ref_key = '10542';            -- -> 77
--
--   INSERT INTO agentlog.report_refs (report_id, ref_id, role)
--   VALUES (418, 77, 'subject');
--
--   -- a text-keyed subject needs no projection columns
--   INSERT INTO agentlog.refs (ref_type, ref_key, label)
--   VALUES ('metric', 'trk/track/meanChi2', 'tracker mean chi2')
--       ON CONFLICT (ref_type, ref_key) DO NOTHING;
--
--   COMMIT;
--
-- A human comment on report 418, or an agent's correction of it, is the same
-- two inserts -- a report plus a link -- with ref_type='report':
--
--   INSERT INTO agentlog.reports (author, agent, triggered_by, severity,
--                                 title, body)
--   VALUES ('mu2e-ops', 'display:rlc', 'human', 'info',
--           'Not low stats -- the HV trip explains it', '...')
--   RETURNING report_id;                                      -- -> 421
--
--   INSERT INTO agentlog.refs (ref_type, ref_key, label)
--   VALUES ('report', '418', 'report 418')
--       ON CONFLICT (ref_type, ref_key) DO NOTHING;
--   SELECT ref_id FROM agentlog.refs
--    WHERE ref_type = 'report' AND ref_key = '418';
--
--   INSERT INTO agentlog.report_refs (report_id, ref_id, role)
--   VALUES (421, <that ref_id>, 'comments_on');
--   -- role='supersedes' instead, for a correction. Either way 418 is
--   -- unchanged and still served.
--
-- And how displays read it back:
--   -- latest 50 reports for a list page. Nothing excluded; corrected_by and
--   -- commented_by tell the display what to annotate.
--   SELECT * FROM agentlog.report_index
--    ORDER BY create_time DESC LIMIT 50;
--
--   -- everything ever filed about run 10542, including range refs that
--   -- contain it
--   SELECT DISTINCT r.report_id, r.create_time, r.title
--     FROM agentlog.reports r
--     JOIN agentlog.report_refs rr ON rr.report_id = r.report_id
--     JOIN agentlog.refs f         ON f.ref_id = rr.ref_id
--    WHERE f.ref_type = 'run' AND 10542 BETWEEN f.run_lo AND f.run_hi
--    ORDER BY r.create_time;
--
--   -- anything about runs 10500-10600 (overlap, not containment)
--   SELECT DISTINCT r.report_id, r.title FROM agentlog.reports r
--     JOIN agentlog.report_refs_full v ON v.report_id = r.report_id
--    WHERE v.ref_type = 'run' AND v.run_lo <= 10600 AND v.run_hi >= 10500;
--
--   -- anything about last night
--   SELECT DISTINCT r.report_id, r.title FROM agentlog.reports r
--     JOIN agentlog.report_refs_full v ON v.report_id = r.report_id
--    WHERE v.ref_type = 'time'
--      AND v.time_lo < '2026-11-13T12:00:00Z'
--      AND v.time_hi > '2026-11-12T20:00:00Z';
--
--   -- reports about the tracker
--   SELECT r.* FROM agentlog.reports r
--     JOIN agentlog.report_refs_full v ON v.report_id = r.report_id
--    WHERE v.ref_type = 'system' AND v.ref_key = 'trk' AND v.role = 'subject';
--
--   -- the trail around one report, both directions
--   SELECT * FROM agentlog.report_links WHERE to_report   = 418;  -- points at it
--   SELECT * FROM agentlog.report_links WHERE from_report = 418;  -- it points at
--
--   -- free text: how reasoning and tool use are searched now
--   SELECT report_id, title FROM agentlog.reports
--    WHERE search_vec @@ websearch_to_tsquery('english', 'list_alarms low stats')
--    ORDER BY create_time DESC;
-- ===========================================================================


-- ===========================================================================
-- Admin operations (NOT available to the MCP -- run by hand as an account
-- with UPDATE/DELETE privileges)
--
-- The MCP is structurally incapable of deleting or hiding anything -- a wrong
-- report stays in the log and stays served, with its correction linked. If
-- content has to actually go away -- a credential pasted into a body,
-- personal data, something that must not be served at all -- it has to be
-- removed here.
--
-- Retract a report but keep the row and its references:
--   UPDATE agentlog.reports
--      SET title = '[retracted]', summary = NULL, body = '[retracted]'
--    WHERE report_id = <id>;
--
-- Delete a report entirely (children first -- FKs):
--   BEGIN;
--   DELETE FROM agentlog.report_refs WHERE report_id = <id>;
--   DELETE FROM agentlog.reports     WHERE report_id = <id>;
--   COMMIT;
--   -- refs rows are shared; orphans are harmless, but to sweep them:
--   DELETE FROM agentlog.refs f
--    WHERE NOT EXISTS (SELECT 1 FROM agentlog.report_refs rr WHERE rr.ref_id = f.ref_id);
--
-- Fix a wrong label on a ref (the MCP cannot -- see decision 6):
--   UPDATE agentlog.refs SET label = 'run 10542'
--    WHERE ref_type = 'run' AND ref_key = '10542';
--
-- Merge two refs that should have been one (bad canonicalization):
--   BEGIN;
--   UPDATE agentlog.report_refs SET ref_id = <keep> WHERE ref_id = <drop>;
--   DELETE FROM agentlog.refs WHERE ref_id = <drop>;
--   COMMIT;
--   -- note: this can violate the (report_id, ref_id, role) PK if a report
--   -- already linked both; delete the loser's duplicate links first.
--
-- Extend a vocabulary (no table rewrite, but it does take a brief ACCESS
-- EXCLUSIVE lock):
--   ALTER TABLE agentlog.refs DROP CONSTRAINT refs_ref_type_check;
--   ALTER TABLE agentlog.refs ADD CONSTRAINT refs_ref_type_check
--       CHECK (ref_type IN (..., 'newtype'));
--   -- a new type with its own typed projection columns also needs the
--   -- refs_run_cols / refs_time_cols pattern extended; a new TEXT-keyed type
--   -- needs nothing else.
--
-- Note there is NO non-admin retraction path, by design. An agent can file a
-- correction with role='supersedes', but that adds to the trail rather than
-- removing anything -- the original stays readable and stays in every listing.
-- Only the statements above change or destroy data.
-- ===========================================================================


-- ===========================================================================
-- DESIGN DECISIONS -- recorded so the next person to read this schema knows
-- what was chosen deliberately, and does not "fix" it. (1) is new in draft 2
-- and subsumes several of draft 1's.
--
-- (1) refs ANSWERS ONE QUESTION: "which reports are about this thing?" Draft 1
--     also tried to record provenance -- which tool a report called, with
--     which arguments, in refs.attrs. A reviewer broke it: the second report
--     to call the same tool hit ON CONFLICT DO NOTHING, its arguments were
--     discarded, and it was linked to the first report's arguments instead --
--     silently, unqueryably, and unrepairable without UPDATE. The root cause
--     was conceptual, not a missing key column: a refs row is SHARED and
--     WRITE-ONCE, so it can only hold facts about the thing itself, never
--     facts about one report's use of it. Rather than widen the key (jsonb in
--     a btree index, with a 2704-byte cliff) or add a per-use column on the
--     link, draft 2 removes the second concept. How a report was developed is
--     prose in its body, where an agent explains its reasoning anyway, and
--     the full-text index covers "which reports mention list_alarms". Nobody
--     needs to query that structurally. THE INVARIANT TO PRESERVE: every
--     non-key column of refs must be a function of (ref_type, ref_key). If a
--     value depends on WHICH REPORT points at the ref, it does not belong in
--     these tables at all.
--
-- (2) 'mcp' SURVIVES AS A ref_type, with no arguments and no attrs, because an
--     MCP server or tool can be the SUBJECT of a report -- "dqm.list_alarms
--     with this key is too slow". That is a report about the tool. A report
--     that merely used the tool does not get a ref.
--
--     Draft 1's 'subrun' and 'agent' types are dropped -- say so if either is
--     wanted back. A report about one subrun still refs its run, which is how
--     anyone would look for it, and the subrun belongs in the title; a report
--     about an agent is rare enough for 'free'. Both are one ALTER away
--     (see Admin operations) and need no new columns.
--
-- (3) TWO TYPED PROJECTIONS, run_lo/run_hi AS INTEGER AND time_lo/time_hi AS
--     TIMESTAMPTZ; every other type is text-keyed only. Those are the two
--     questions that need range and overlap queries ("reports about runs
--     10500-10600", "reports about last night"); text equality serves the
--     rest. The projections are derived from ref_key, and CHECK constraints
--     force them to be present for exactly their own type -- so a run ref
--     cannot be filed without its integers and go missing from every range
--     query.
--
--     Those two CHECKs count non-nulls (num_nonnulls(run_lo, run_hi) = 2 or
--     0) rather than testing `(ref_type='run') = (both NOT NULL)`, which the
--     file shipped with until 2026-11-19. The old form had a hole: on a type
--     that should carry neither column, setting exactly ONE of them made the
--     conjunction false and so satisfied the equality. A time ref with a
--     stray run_lo and no run_hi passed both CHECKs -- exactly the
--     half-filled, silently-wrong row these constraints exist to reject.
--     See migrations/001-fix-range-projection-checks.sql.
--
-- (4) IDENTITY IS JUST (ref_type, ref_key). Draft 1's `version` column is
--     gone: a dataset version or docdb revision folds into the canonical
--     ref_key ('mu2e:dataset@v3'), which removes a nullable column from the
--     unique constraint and the NULLs-are-distinct trap that came with it.
--
-- (5) FIVE ROLES: subject, mentions, and the three report-to-report relations.
--     Draft 1's evidence/consulted/cited/produced went with the provenance
--     concept, as did its `source` and `note` columns on the link.
--     `role` STAYS IN THE PRIMARY KEY as settled at the first review, but note
--     that the reason has weakened -- the surviving roles are mutually
--     exclusive in practice, so PK (report_id, ref_id) with a single role is
--     now also defensible if a further simplification is wanted.
--
-- (6) NO UPDATE PRIVILEGE, ON ANY TABLE. The MCP has INSERT and SELECT only.
--     So the first writer of a ref sets its `label` permanently -- accepted,
--     because "nothing the MCP does can modify existing data" is worth more
--     than tidier labels, and a stale label is cosmetic now that no
--     load-bearing data lives in a shared row. Consequence for the server:
--     the dedup path must be ON CONFLICT DO NOTHING followed by a SELECT,
--     never ON CONFLICT DO UPDATE, which the grants would refuse.
--
-- (7) ref_type='report' IS A TEXT report_id, with no foreign key. Nothing
--     stops an agent writing ref_key='99999' for a report that does not
--     exist. Best effort, rare defects tolerated: report_links simply does
--     not match a dangling id, so a bad reference is invisible rather than an
--     error, and since reports are never deleted a correctly written id
--     always resolves.
--
-- (8) HUMAN COMMENTS ARE REPORTS, not a fourth table. triggered_by='human',
--     linked with role='comments_on'. A person's correction sits in the same
--     trail as the agent's original, with the same reference machinery, and
--     -- the point -- cannot overwrite what the agent wrote. The link role is
--     what makes it a comment; there is no `kind` column marking it (see 12).
--
-- (9) NOTHING IS EVER HIDDEN. There was a draft `current_reports` view that
--     filtered superseded reports out of the log; it is gone. The entire
--     trail is exposed, wrong reports included, because a row vanishing from
--     a log is more confusing than a row that is wrong. 'supersedes' is
--     purely a recorded claim: report_links exposes the edge and report_index
--     flags it (corrected_by / commented_by) so a display can mark a
--     corrected report, but every query still sees every report.
--
-- (10) NO OWNER BOUNDARY, unlike the memory MCP. Every report is readable by
--     anyone who can read the schema; `author` is provenance, not access
--     control. Right default for an operations log whose purpose is to be
--     consumed by other agents and displays. Two consequences accepted: an
--     agent cannot file anything private, and a shared key makes every holder
--     of it the same `author`.
--
-- (11) NO SUBJECT-TIME COLUMNS ON reports. "When the thing being investigated
--     happened" is a ref (ref_type='time'); reports.create_time is only when
--     the report was filed. One authoritative place for the subject time
--     instead of two that can disagree.
--
-- (12) THE REPORT TABLE WAS TRIMMED (draft 3), dropping session_id, model,
--     kind, verdict and confidence. Earlier drafts itemized these; the
--     judgment at review was that they do not earn a column. The reasoning,
--     the agent's confidence and the sort of report it is are things the
--     PROMPT should tell the agent to write into the body, in prose, where a
--     reader gets them with their justification attached -- not as a bare
--     enum a display would have to explain. Nobody is going to query
--     "reports with confidence > 80". The model is deliberately not tracked
--     here at all.
--
--     WHAT SURVIVES AND WHY: `triggered_by` and `severity`, because a display
--     or a digest filters on them ("tonight's critical reports", "everything
--     the alarm watch filed"); `author`/`agent` for accountability; `tags`
--     and `attrs` as the open-ended escapes if something turns out to need
--     filtering before anyone wants a schema change.
--
--     Any of the five is a one-line ALTER TABLE ADD COLUMN away if a real
--     query turns up that needs it -- which is a better reason to add a
--     column than anticipating one.
-- ===========================================================================
