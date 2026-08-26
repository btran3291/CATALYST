-- Catalyst storage schema.
-- Core invariant: facts is append-only. Never UPDATE or DELETE a row in it —
-- restatements are new rows with a later knowledge_date, not overwrites.

CREATE TABLE IF NOT EXISTS companies (
    cik          TEXT PRIMARY KEY,   -- SEC CIK, zero-padded string (preserves leading zeros)
    entity_name  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_status_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cik            TEXT NOT NULL REFERENCES companies(cik),
    effective_date TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('active', 'delisted', 'bankrupt', 'acquired')),
    UNIQUE (cik, effective_date, status)
);

CREATE TABLE IF NOT EXISTS facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id      TEXT NOT NULL REFERENCES companies(cik),
    concept        TEXT NOT NULL,     -- e.g. "Revenues", "ActiveSatelliteCount", "FCCGrantedMHz"
    period_start   TEXT,              -- NULL for instant facts
    period_end     TEXT NOT NULL,
    value          REAL NOT NULL,
    unit           TEXT,
    knowledge_date TEXT NOT NULL,     -- when this became knowable: filing/scrape/publication date
    source         TEXT NOT NULL CHECK (source IN ('sec_xbrl', 'celestrak', 'fcc')),
    source_ref     TEXT NOT NULL,     -- accession_number | TLE catalog id | FCC filing id
    corroborated   INTEGER NOT NULL DEFAULT 0,  -- 1 if >=1 other alias tag agreed within tolerance
                                                  -- for this (period, filing); 0 if no alias existed
                                                  -- to cross-check against — not itself an error signal
    UNIQUE (entity_id, concept, period_start, period_end, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_facts_as_of
    ON facts (entity_id, concept, period_end, knowledge_date);

CREATE INDEX IF NOT EXISTS idx_facts_knowledge_date
    ON facts (knowledge_date);

-- Buildout timeline estimates as a range, never a single date.
-- Time-to-first-revenue catalyst.
CREATE TABLE IF NOT EXISTS catalyst_estimates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cik        TEXT NOT NULL REFERENCES companies(cik),
    as_of_date TEXT NOT NULL,
    p10_date   TEXT NOT NULL,
    p50_date   TEXT NOT NULL,
    p90_date   TEXT NOT NULL,
    basis      TEXT,               -- method/assumptions used to derive the range
    UNIQUE (cik, as_of_date)
);

-- Time-to-full-buildout catalyst — a distinct milestone from first revenue
-- (catalyst_estimates above). Prospective: doesn't require the buildout to
-- have actually completed, just an estimate to classify "mature" against.
-- Deliberately separate rather than extra columns on catalyst_estimates so
-- the two milestones can't get conflated under one p10/p50/p90.
CREATE TABLE IF NOT EXISTS buildout_estimates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cik        TEXT NOT NULL REFERENCES companies(cik),
    as_of_date TEXT NOT NULL,
    p10_date   TEXT NOT NULL,
    p50_date   TEXT NOT NULL,
    p90_date   TEXT NOT NULL,
    basis      TEXT,
    UNIQUE (cik, as_of_date)
);

-- Quarantine for alias tags that disagree beyond tolerance for the same
-- (entity, period, filing) — e.g. a stale/unmaintained XBRL tag carrying a
-- prior year's value. Never merged into facts; kept for inspection.
CREATE TABLE IF NOT EXISTS concept_conflicts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id      TEXT NOT NULL REFERENCES companies(cik),
    canonical      TEXT NOT NULL,     -- e.g. "revenue"
    period_start   TEXT,
    period_end     TEXT NOT NULL,
    source_ref     TEXT NOT NULL,     -- accession number where the conflict occurred
    concept        TEXT NOT NULL,     -- the raw XBRL tag
    value          REAL NOT NULL,
    unit           TEXT,
    knowledge_date TEXT NOT NULL,
    UNIQUE (entity_id, canonical, period_start, period_end, source_ref, concept)
);

-- Derived, reproducible cache: always a pure function of facts as of as_of_date.
-- Never hand-edited. Rebuildable by rerunning the classifier.
-- stage_min/stage_max: equal when the stage is determined outright (2-5);
-- (0, 1) when revenue data alone can't distinguish pre-product from
-- building — that needs asset data (satellite counts, FCC grants) not yet
-- ingested. Never collapse this to a single guessed integer.
CREATE TABLE IF NOT EXISTS stage_assignments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cik          TEXT NOT NULL REFERENCES companies(cik),
    as_of_date   TEXT NOT NULL,
    stage_min    INTEGER NOT NULL CHECK (stage_min BETWEEN 0 AND 5),
    stage_max    INTEGER NOT NULL CHECK (stage_max BETWEEN 0 AND 5 AND stage_max >= stage_min),
    basis        TEXT,               -- e.g. "streak=3" — which rule fired
    computed_at  TEXT NOT NULL,
    UNIQUE (cik, as_of_date)
);
