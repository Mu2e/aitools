-- memory-mcp schema for mu2e_ai_prd
--
--   psql -h ifdb11 -p 5477 mu2e_ai_prd -f create_tables.sql
--
-- Design note: every table here is APPEND-ONLY. The MCP's database role is
-- granted INSERT and SELECT and nothing else -- no UPDATE, no DELETE. So a
-- new document version is a new row in memory.versions, and a metadata
-- change is a new row in memory.metadata (latest row wins; earlier rows are
-- the archive). Nothing the MCP does can ever modify or remove existing
-- data. See "Admin operations" at the bottom for the deliberately manual
-- path to actually deleting content.

-- admin owns everything
SET ROLE ADMIN_ROLE;

CREATE SCHEMA IF NOT EXISTS memory;


-- ---------------------------------------------------------------------------
-- Document identity: one row per (owner, project, name). Written once, at
-- first put_document; never touched again.
--
-- `owner` is the name attached to the mikey bearer key that the writing agent
-- authenticated with -- NOT anything the caller passes in. Note that a shared
-- group key (or the collaboration-wide key) makes every holder of that key the
-- same owner, and therefore able to read and overwrite each other's documents.
-- That is intended for now; see the README.
-- ---------------------------------------------------------------------------
CREATE TABLE memory.documents (
    doc_id       BIGSERIAL   PRIMARY KEY,
    owner        TEXT        NOT NULL,
    project      TEXT        NOT NULL,
    name         TEXT        NOT NULL,
    create_time  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner, project, name)
);


-- ---------------------------------------------------------------------------
-- Versions: a FULL content snapshot per version, not a delta. "append" mode
-- in the MCP reads the current content, concatenates, and writes a complete
-- new row -- so reconstructing any version is a single row read, and there is
-- no chain to corrupt.
--
-- content_size is octet_length(content), stored rather than computed so that
-- listings can report document size (for an agent deciding whether something
-- fits in its context) without ever reading the content column.
-- ---------------------------------------------------------------------------
CREATE TABLE memory.versions (
    version_id   BIGSERIAL   PRIMARY KEY,
    doc_id       BIGINT      NOT NULL REFERENCES memory.documents(doc_id),
    version      INT         NOT NULL,
    content      TEXT        NOT NULL,
    content_size INT         NOT NULL,
    create_time  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (doc_id, version)
);


-- ---------------------------------------------------------------------------
-- Metadata history. All fields optional. The row with the highest metadata_id
-- for a doc_id is the current metadata; every earlier row is retained as the
-- archive. The MCP merges (carries forward unspecified fields) before
-- inserting, so a partial update does not silently blank the other fields.
--
-- `retired` is a soft-delete: since the MCP can never DELETE, this is the only
-- way to drop a document out of normal listings.
-- `expires` is advisory -- it filters reads, it does not remove anything.
-- ---------------------------------------------------------------------------
CREATE TABLE memory.metadata (
    metadata_id  BIGSERIAL   PRIMARY KEY,
    doc_id       BIGINT      NOT NULL REFERENCES memory.documents(doc_id),
    keywords     TEXT[],
    description  TEXT,
    weight       INT         CHECK (weight BETWEEN 0 AND 100),
    expires      TIMESTAMPTZ,
    retired      BOOLEAN     NOT NULL DEFAULT FALSE,
    create_time  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX documents_owner_project_idx ON memory.documents (owner, project);
CREATE INDEX versions_doc_version_idx    ON memory.versions  (doc_id, version DESC);
CREATE INDEX metadata_doc_latest_idx     ON memory.metadata  (doc_id, metadata_id DESC);
CREATE INDEX metadata_keywords_idx       ON memory.metadata  USING GIN (keywords);


-- ---------------------------------------------------------------------------
-- "Current state" views, so the latest-version / latest-metadata rule lives in
-- one place instead of being re-derived by every query that needs it.
-- ---------------------------------------------------------------------------
CREATE VIEW memory.current_versions AS
    SELECT DISTINCT ON (doc_id) *
      FROM memory.versions
     ORDER BY doc_id, version DESC;

CREATE VIEW memory.current_metadata AS
    SELECT DISTINCT ON (doc_id) *
      FROM memory.metadata
     ORDER BY doc_id, metadata_id DESC;


-- ---------------------------------------------------------------------------
-- Grants
--
-- The MCP logs in as mu2eai (Kerberos) and splits its privileges in two:
--
--   * READS run as mu2eai itself, unelevated. mu2eai is granted SELECT
--     directly, below.
--   * WRITES run inside a transaction under `SET LOCAL ROLE update_role`
--     (LOCAL, so the elevation reverts on commit/rollback and cannot leak to
--     the next request sharing a pooled connection).
--
-- The point of the split: an accidental INSERT from a code path that was only
-- meant to read is refused by the database rather than silently succeeding.
-- In an append-only store where a stray write is permanent and undeletable,
-- that is worth one extra grant. Note this guards against a coding mistake,
-- not a determined attacker -- put_document legitimately elevates, so
-- anything that can reach the write path can elevate too.
--
-- Two things that are easy to miss, both of which break inserts:
--   * BIGSERIAL columns need USAGE on the underlying sequences.
--   * update_role needs SELECT as well as INSERT -- an "append" write and a
--     metadata merge both have to read current state before inserting.
-- ---------------------------------------------------------------------------

-- Write path.
GRANT USAGE ON SCHEMA memory TO update_role;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA memory TO update_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA memory TO update_role;

-- Read path: the login role, which reads without assuming update_role.
-- (ALL TABLES covers the current_versions / current_metadata views too.)
GRANT USAGE ON SCHEMA memory TO mu2eai;
GRANT SELECT ON ALL TABLES IN SCHEMA memory TO mu2eai;

-- If tables are added to this schema later, both sets of grants have to be
-- reissued -- ALL TABLES is evaluated at grant time, not standing policy.
-- To make that automatic instead, as the owning role:
--   ALTER DEFAULT PRIVILEGES IN SCHEMA memory
--       GRANT SELECT, INSERT ON TABLES TO update_role;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA memory
--       GRANT SELECT ON TABLES TO mu2eai;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA memory
--       GRANT USAGE, SELECT ON SEQUENCES TO update_role;


-- ---------------------------------------------------------------------------
-- Admin operations (NOT available to the MCP -- run these by hand as an
-- account with UPDATE/DELETE privileges)
--
-- The MCP is structurally incapable of deleting anything. If content has to
-- actually go away -- a credential pasted into a document, personal data, a
-- mistake that must not persist -- it has to be removed here.
--
-- Delete one specific version (leaves the document and its other versions):
--   DELETE FROM memory.versions
--    WHERE doc_id = (SELECT doc_id FROM memory.documents
--                     WHERE owner = 'someone' AND project = 'proj' AND name = 'doc')
--      AND version = 3;
--
-- Delete a document entirely (children first -- FKs):
--   BEGIN;
--   DELETE FROM memory.versions  WHERE doc_id = <id>;
--   DELETE FROM memory.metadata  WHERE doc_id = <id>;
--   DELETE FROM memory.documents WHERE doc_id = <id>;
--   COMMIT;
--
-- Blank one version's content but keep the version history intact:
--   UPDATE memory.versions SET content = '', content_size = 0
--    WHERE version_id = <id>;
--
-- Note that an agent can retire a document itself, without admin help, via
-- set_metadata(retired=true) -- that hides it from listings but does not
-- remove the content. Only the statements above actually destroy data.
-- ---------------------------------------------------------------------------
