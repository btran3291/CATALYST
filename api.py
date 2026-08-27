"""HTTP layer over the Catalyst pipeline.

Read-only by construction: every query this module runs itself goes through
a `mode=ro` sqlite connection, and the pipeline functions it calls are all
invoked with persist=False. Nothing reachable over HTTP can write to the
point-in-time store.

Point-in-time (invariant 1) is preserved through the HTTP surface: every
endpoint that reads derived values takes `as_of`, which is threaded down
into `knowledge_date <= as_of` filters in quarters/stages and
`effective_date <= as_of` in the status-event join. An unset `as_of` means
"today", resolved once at the request boundary so the value that reaches
the cache key and the value that reaches the classifier are the same date.

Run it:
    uvicorn api:app --reload
Interactive docs at /docs; the OpenAPI schema at /openapi.json is what the
front end (planning step 5) will be generated against.
"""

import os
import sqlite3
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from quarters import discrete_quarters
from ranking import STALE_DAYS, rank
from stages import classify, find_transitions

DB_PATH = os.environ.get("CATALYST_DB", str(Path(__file__).parent / "catalyst.db"))

# Comma-separated origins, or "*". The API serves only public SEC-derived
# research data and holds no credentials, so a permissive default is safe
# and keeps a locally-served front end from needing a proxy.
CORS_ORIGINS = os.environ.get("CATALYST_CORS_ORIGINS", "*").split(",")

MAX_PAGE = 500

app = FastAPI(
    title="Catalyst",
    version="0.1.0",
    summary="Point-in-time research API for pre-revenue and early-revenue public companies.",
    description=__doc__,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------


def ro_conn() -> sqlite3.Connection:
    """Read-only connection. `mode=ro` makes any write attempt an error at
    the sqlite level rather than a code-review question."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:  # missing/unreadable file
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _db_version() -> tuple[int, int]:
    """Identity of the current database file, used as part of every cache
    key. Rebuilding catalyst.db (bulk_import.py) changes mtime and almost
    always size, so cached rankings computed from the old file are never
    served against the new one."""
    try:
        st = os.stat(DB_PATH)
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    return st.st_mtime_ns, st.st_size


@lru_cache(maxsize=64)
def _ranking_cached(as_of: str, version: tuple[int, int]) -> tuple[dict, ...]:
    return tuple(rank(DB_PATH, as_of))


@lru_cache(maxsize=64)
def _transitions_cached(
    as_of: str, from_stage: int, to_stage: int, version: tuple[int, int]
) -> tuple[dict, ...]:
    """Every from_stage -> to_stage crossing anywhere in the universe.

    Deliberately swept over ALL companies, not just active ones: a
    transition that happened inside a company that later went bankrupt is
    exactly the observation survivorship bias would delete (invariant 2),
    and it is the training signal for any future calibration work.
    """
    conn = ro_conn()
    companies = conn.execute("SELECT cik, entity_name FROM companies").fetchall()
    conn.close()

    found = []
    for row in companies:
        results = classify(DB_PATH, row["cik"], as_of_date=as_of, persist=False)
        for t in find_transitions(results, from_stage, to_stage):
            found.append(
                {
                    "cik": row["cik"],
                    "name": row["entity_name"],
                    "period_end": t["period_end"],
                    "from_stage": t["from"],
                    "to_stage": t["to"],
                }
            )
    found.sort(key=lambda t: t["period_end"], reverse=True)
    return tuple(found)


def resolve_as_of(as_of: date | None) -> str:
    """Resolve the optional `as_of` query param to a concrete ISO date.

    Resolving None to today() here rather than passing None downstream keeps
    the cache key stable and honest: two requests a day apart get different
    keys, so a long-running server can't serve yesterday's staleness
    judgement as today's.
    """
    return (as_of or date.today()).isoformat()


def normalize_cik(raw: str) -> str:
    """Accept 1865631, 0001865631, or CIK0001865631 for the same company.
    CIKs are stored zero-padded to 10 chars so leading zeros survive."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        raise HTTPException(status_code=422, detail=f"not a CIK: {raw!r}")
    if len(digits) > 10:
        raise HTTPException(status_code=422, detail=f"CIK too long: {raw!r}")
    return digits.zfill(10)


def _stage_label(stage_min: int, stage_max: int) -> str:
    return str(stage_min) if stage_min == stage_max else f"{stage_min}-{stage_max}"


def _require_company(conn: sqlite3.Connection, cik: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT cik, entity_name FROM companies WHERE cik = ?", (cik,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown CIK {cik}")
    return row


def _latest_status(conn: sqlite3.Connection, cik: str, as_of: str) -> dict | None:
    """Latest status event known as of `as_of`, or None for a company that
    has never had one (a survivor). The LATEST event decides current status
    — not the mere existence of one — because a company can go bankrupt,
    reorganize, and operate again (Humanigen, Mallinckrodt)."""
    row = conn.execute(
        """
        SELECT effective_date, status FROM company_status_events
        WHERE cik = ? AND effective_date <= ?
        ORDER BY effective_date DESC, id DESC LIMIT 1
        """,
        (cik, as_of),
    ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# response models
# --------------------------------------------------------------------------


class EstimateRange(BaseModel):
    """A dated milestone as a distribution, never a single date (invariant 3)."""

    as_of_date: str
    p10_date: str
    p50_date: str
    p90_date: str
    basis: str | None = Field(None, description="Filing-level reasoning behind the range.")


class RankEntry(BaseModel):
    cik: str
    name: str
    stage: str = Field(description="stage_min, or 'min-max' when the range is undetermined.")
    as_of: str = Field(description="Period end of the latest quarter observed for this company.")
    stale: bool = Field(description=f"Latest observation older than {STALE_DAYS} days.")
    age_days: int
    streak: int = Field(description="Consecutive quarter-over-quarter revenue increases.")
    latest_quarterly_revenue: float = Field(
        description="Unit is whatever the filer reported in; mostly USD, some EUR/CHF/CAD/GBP. Not currency-normalized."
    )
    catalyst: EstimateRange | None = Field(
        None, description="Time to first revenue. Null means no estimate on file — never a guess."
    )
    buildout: EstimateRange | None = Field(None, description="Time to full buildout.")


class RankingResponse(BaseModel):
    as_of: str
    total: int = Field(description="Entries matching the filters, before pagination.")
    fresh: int
    stale: int
    limit: int
    offset: int
    entries: list[RankEntry]


class CompanySummary(BaseModel):
    cik: str
    name: str
    status: str = Field(description="'active', or the latest recorded status event.")
    status_date: str | None = None
    fact_count: int
    latest_period_end: str | None = None


class CompanyListResponse(BaseModel):
    as_of: str
    total: int
    limit: int
    offset: int
    companies: list[CompanySummary]


class StatusEvent(BaseModel):
    effective_date: str
    status: str


class SeriesPoint(BaseModel):
    period_end: str
    value: float
    corroborated: bool = Field(
        description="Another alias tag agreed within tolerance. Confidence signal, not a correctness guarantee."
    )
    streak: int
    stage: str
    stage_min: int
    stage_max: int
    basis: str = Field(description="Which classifier rule fired.")


class Transition(BaseModel):
    cik: str
    name: str
    period_end: str
    from_stage: int
    to_stage: int


class CompanyDetail(BaseModel):
    cik: str
    name: str
    as_of: str
    status: str
    status_date: str | None = None
    status_events: list[StatusEvent]
    fact_count: int
    concepts: list[str]
    units: list[str]
    first_period_end: str | None = None
    latest_period_end: str | None = None
    latest_knowledge_date: str | None = None
    conflict_count: int = Field(
        description="Alias disagreements quarantined out of `facts` for this company. Non-zero is a caveat on the series below, not a bug."
    )
    catalyst_estimates: list[EstimateRange]
    buildout_estimates: list[EstimateRange]
    series: list[SeriesPoint]
    transitions_2_to_3: list[Transition]


class QuarterPoint(BaseModel):
    period_start: str | None = None
    period_end: str
    value: float
    corroborated: bool


class UniverseStats(BaseModel):
    as_of: str
    companies: int
    active: int
    inactive: int
    by_status: dict[str, int]
    status_events: int
    facts: int
    companies_with_facts: int
    concept_conflicts: int
    catalyst_estimates: int
    buildout_estimates: int
    earliest_knowledge_date: str | None = None
    latest_knowledge_date: str | None = None


class Health(BaseModel):
    status: str
    db_path: str
    companies: int
    facts: int
    latest_knowledge_date: str | None = None


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health", response_model=Health, tags=["meta"])
def health() -> Health:
    conn = ro_conn()
    try:
        companies = conn.execute("SELECT count(*) FROM companies").fetchone()[0]
        facts, latest = conn.execute(
            "SELECT count(*), max(knowledge_date) FROM facts"
        ).fetchone()
    finally:
        conn.close()
    return Health(
        status="ok",
        db_path=DB_PATH,
        companies=companies,
        facts=facts,
        latest_knowledge_date=latest,
    )


@app.get("/universe/stats", response_model=UniverseStats, tags=["meta"])
def universe_stats(as_of: date | None = None) -> UniverseStats:
    """Universe composition as of a date. Non-survivors are counted, never
    excluded — they stay in the universe permanently (invariant 2)."""
    resolved = resolve_as_of(as_of)
    conn = ro_conn()
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(latest.status, 'active') AS status, count(*) AS n
            FROM companies c
            LEFT JOIN (
                SELECT cik, status,
                       ROW_NUMBER() OVER (
                           PARTITION BY cik ORDER BY effective_date DESC, id DESC
                       ) AS rn
                FROM company_status_events WHERE effective_date <= ?
            ) latest ON latest.cik = c.cik AND latest.rn = 1
            GROUP BY 1
            """,
            (resolved,),
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in rows}

        companies = sum(by_status.values())
        events = conn.execute(
            "SELECT count(*) FROM company_status_events WHERE effective_date <= ?",
            (resolved,),
        ).fetchone()[0]
        facts, with_facts, earliest, latest = conn.execute(
            """
            SELECT count(*), count(DISTINCT entity_id), min(knowledge_date), max(knowledge_date)
            FROM facts WHERE knowledge_date <= ?
            """,
            (resolved,),
        ).fetchone()
        conflicts = conn.execute(
            "SELECT count(*) FROM concept_conflicts WHERE knowledge_date <= ?", (resolved,)
        ).fetchone()[0]
        catalysts = conn.execute(
            "SELECT count(*) FROM catalyst_estimates WHERE as_of_date <= ?", (resolved,)
        ).fetchone()[0]
        buildouts = conn.execute(
            "SELECT count(*) FROM buildout_estimates WHERE as_of_date <= ?", (resolved,)
        ).fetchone()[0]
    finally:
        conn.close()

    active = by_status.get("active", 0)
    return UniverseStats(
        as_of=resolved,
        companies=companies,
        active=active,
        inactive=companies - active,
        by_status=by_status,
        status_events=events,
        facts=facts,
        companies_with_facts=with_facts,
        concept_conflicts=conflicts,
        catalyst_estimates=catalysts,
        buildout_estimates=buildouts,
        earliest_knowledge_date=earliest,
        latest_knowledge_date=latest,
    )


@app.get("/ranking", response_model=RankingResponse, tags=["ranking"])
def ranking(
    as_of: date | None = Query(None, description="Simulated present. Omit for today."),
    include_stale: bool = Query(True, description="Stale rows are demoted, never dropped."),
    estimates_only: bool = Query(False, description="Only rows with a real catalyst estimate on file."),
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
) -> RankingResponse:
    """Active companies we KNOW haven't inflected (stage_max <= 2), ranked by
    revenue momentum, plus any company with a hand-verified catalyst estimate
    regardless of stage range.

    Ordering: fresh rows first — estimate-backed by nearest p50, then momentum
    by streak — with stale rows below all of them, most recently observed
    first. "Stale" means the latest observed quarter ended more than ~18
    months before `as_of`; such a company is still listed and filing, so the
    row is demoted and labelled rather than dropped.

    `catalyst` is null when no estimate is on file. That is a real answer —
    the absence of an estimate is never filled in with a guess.
    """
    resolved = resolve_as_of(as_of)
    entries = _ranking_cached(resolved, _db_version())

    fresh_count = sum(1 for e in entries if not e["stale"])
    selected = [e for e in entries if include_stale or not e["stale"]]
    if estimates_only:
        selected = [e for e in selected if e["catalyst_estimate"] is not None]

    page = selected[offset : offset + limit]
    return RankingResponse(
        as_of=resolved,
        total=len(selected),
        fresh=fresh_count,
        stale=len(entries) - fresh_count,
        limit=limit,
        offset=offset,
        entries=[
            RankEntry(
                cik=e["cik"],
                name=e["name"],
                stage=e["stage"],
                as_of=e["as_of"],
                stale=e["stale"],
                age_days=e["age_days"],
                streak=e["streak"],
                latest_quarterly_revenue=e["latest_quarterly_revenue"],
                catalyst=e["catalyst_estimate"],
                buildout=e["buildout_estimate"],
            )
            for e in page
        ],
    )


@app.get("/transitions", response_model=list[Transition], tags=["ranking"])
def transitions(
    as_of: date | None = None,
    from_stage: int = Query(2, ge=0, le=5),
    to_stage: int = Query(3, ge=0, le=5),
    since: date | None = Query(None, description="Only transitions at or after this period end."),
    limit: int = Query(100, ge=1, le=MAX_PAGE),
) -> list[Transition]:
    """Detected stage crossings across the whole universe, most recent first.

    The default 2 -> 3 (first revenue -> inflection) is the signal this
    project exists to find. Non-survivors are included: excluding them is
    precisely the survivorship bias invariant 2 forbids.
    """
    resolved = resolve_as_of(as_of)
    found = _transitions_cached(resolved, from_stage, to_stage, _db_version())
    if since is not None:
        cutoff = since.isoformat()
        found = tuple(t for t in found if t["period_end"] >= cutoff)
    return [Transition(**t) for t in found[:limit]]


@app.get("/companies", response_model=CompanyListResponse, tags=["companies"])
def list_companies(
    as_of: date | None = None,
    q: str | None = Query(None, description="Case-insensitive substring of name or CIK."),
    status: str = Query("all", pattern="^(all|active|inactive|bankrupt|delisted|acquired)$"),
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
) -> CompanyListResponse:
    resolved = resolve_as_of(as_of)

    where: list[str] = []
    params: list[str] = [resolved]
    if q:
        where.append("(LOWER(c.entity_name) LIKE ? OR c.cik LIKE ?)")
        params += [f"%{q.lower()}%", f"%{q}%"]
    if status == "active":
        where.append("COALESCE(latest.status, 'active') = 'active'")
    elif status == "inactive":
        where.append("COALESCE(latest.status, 'active') <> 'active'")
    elif status != "all":
        where.append("latest.status = ?")
        params.append(status)

    base = f"""
        FROM companies c
        LEFT JOIN (
            SELECT cik, status, effective_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY cik ORDER BY effective_date DESC, id DESC
                   ) AS rn
            FROM company_status_events WHERE effective_date <= ?
        ) latest ON latest.cik = c.cik AND latest.rn = 1
        {"WHERE " + " AND ".join(where) if where else ""}
    """

    conn = ro_conn()
    try:
        total = conn.execute(f"SELECT count(*) {base}", params).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT c.cik, c.entity_name,
                   COALESCE(latest.status, 'active') AS status,
                   latest.effective_date AS status_date
            {base}
            ORDER BY c.entity_name
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

        # Fact counts only for the page actually being returned — a
        # universe-wide GROUP BY over 69k facts to render 50 rows is waste.
        ciks = [r["cik"] for r in rows]
        counts: dict[str, tuple[int, str | None]] = {}
        if ciks:
            placeholders = ",".join("?" * len(ciks))
            for fr in conn.execute(
                f"""
                SELECT entity_id, count(*) AS n, max(period_end) AS latest
                FROM facts
                WHERE entity_id IN ({placeholders}) AND knowledge_date <= ?
                GROUP BY entity_id
                """,
                [*ciks, resolved],
            ):
                counts[fr["entity_id"]] = (fr["n"], fr["latest"])
    finally:
        conn.close()

    return CompanyListResponse(
        as_of=resolved,
        total=total,
        limit=limit,
        offset=offset,
        companies=[
            CompanySummary(
                cik=r["cik"],
                name=r["entity_name"],
                status=r["status"],
                status_date=r["status_date"],
                fact_count=counts.get(r["cik"], (0, None))[0],
                latest_period_end=counts.get(r["cik"], (0, None))[1],
            )
            for r in rows
        ],
    )


@app.get("/companies/{cik}", response_model=CompanyDetail, tags=["companies"])
def company_detail(cik: str, as_of: date | None = None) -> CompanyDetail:
    """Everything known about one company as of a date, in one round trip:
    status history, fact coverage, estimates, and the classified quarter
    series the ranking is derived from."""
    resolved = resolve_as_of(as_of)
    normalized = normalize_cik(cik)

    conn = ro_conn()
    try:
        company = _require_company(conn, normalized)
        latest_status = _latest_status(conn, normalized, resolved)
        events = conn.execute(
            """
            SELECT effective_date, status FROM company_status_events
            WHERE cik = ? AND effective_date <= ? ORDER BY effective_date
            """,
            (normalized, resolved),
        ).fetchall()
        fact_count, first_end, last_end, last_kd = conn.execute(
            """
            SELECT count(*), min(period_end), max(period_end), max(knowledge_date)
            FROM facts WHERE entity_id = ? AND knowledge_date <= ?
            """,
            (normalized, resolved),
        ).fetchone()
        concepts = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT concept FROM facts
                WHERE entity_id = ? AND knowledge_date <= ? ORDER BY concept
                """,
                (normalized, resolved),
            )
        ]
        units = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT unit FROM facts
                WHERE entity_id = ? AND knowledge_date <= ? AND unit IS NOT NULL
                ORDER BY unit
                """,
                (normalized, resolved),
            )
        ]
        conflicts = conn.execute(
            "SELECT count(*) FROM concept_conflicts WHERE entity_id = ? AND knowledge_date <= ?",
            (normalized, resolved),
        ).fetchone()[0]
        catalysts = [
            EstimateRange(**dict(r))
            for r in conn.execute(
                """
                SELECT as_of_date, p10_date, p50_date, p90_date, basis
                FROM catalyst_estimates WHERE cik = ? AND as_of_date <= ?
                ORDER BY as_of_date DESC
                """,
                (normalized, resolved),
            )
        ]
        buildouts = [
            EstimateRange(**dict(r))
            for r in conn.execute(
                """
                SELECT as_of_date, p10_date, p50_date, p90_date, basis
                FROM buildout_estimates WHERE cik = ? AND as_of_date <= ?
                ORDER BY as_of_date DESC
                """,
                (normalized, resolved),
            )
        ]
    finally:
        conn.close()

    results = classify(DB_PATH, normalized, as_of_date=resolved, persist=False)
    series = [
        SeriesPoint(
            period_end=r["period_end"],
            value=r["value"],
            corroborated=bool(r["corroborated"]),
            streak=r["streak"],
            stage=_stage_label(r["stage_min"], r["stage_max"]),
            stage_min=r["stage_min"],
            stage_max=r["stage_max"],
            basis=r["basis"],
        )
        for r in results
    ]

    return CompanyDetail(
        cik=normalized,
        name=company["entity_name"],
        as_of=resolved,
        status=latest_status["status"] if latest_status else "active",
        status_date=latest_status["effective_date"] if latest_status else None,
        status_events=[StatusEvent(**dict(e)) for e in events],
        fact_count=fact_count,
        concepts=concepts,
        units=units,
        first_period_end=first_end,
        latest_period_end=last_end,
        latest_knowledge_date=last_kd,
        conflict_count=conflicts,
        catalyst_estimates=catalysts,
        buildout_estimates=buildouts,
        series=series,
        transitions_2_to_3=[
            Transition(
                cik=normalized,
                name=company["entity_name"],
                period_end=t["period_end"],
                from_stage=t["from"],
                to_stage=t["to"],
            )
            for t in find_transitions(results, 2, 3)
        ],
    )


@app.get("/companies/{cik}/quarters", response_model=list[QuarterPoint], tags=["companies"])
def company_quarters(
    cik: str, as_of: date | None = None, concept: str = "revenue"
) -> list[QuarterPoint]:
    """Discrete per-quarter values, before classification. A missing quarter
    is a real gap — a period quarantined into concept_conflicts is absent
    here rather than estimated."""
    resolved = resolve_as_of(as_of)
    normalized = normalize_cik(cik)
    conn = ro_conn()
    try:
        _require_company(conn, normalized)
    finally:
        conn.close()
    try:
        quarters = discrete_quarters(DB_PATH, normalized, concept, resolved)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"unknown concept {concept!r}") from exc
    return [QuarterPoint(**q) for q in quarters]


@app.get("/companies/{cik}/stages", response_model=list[SeriesPoint], tags=["companies"])
def company_stages(cik: str, as_of: date | None = None) -> list[SeriesPoint]:
    """Stage assignment per observed quarter. Computed fresh and never
    persisted — stage_assignments is a rebuildable cache, and an HTTP read
    has no business writing to it."""
    resolved = resolve_as_of(as_of)
    normalized = normalize_cik(cik)
    conn = ro_conn()
    try:
        _require_company(conn, normalized)
    finally:
        conn.close()
    return [
        SeriesPoint(
            period_end=r["period_end"],
            value=r["value"],
            corroborated=bool(r["corroborated"]),
            streak=r["streak"],
            stage=_stage_label(r["stage_min"], r["stage_max"]),
            stage_min=r["stage_min"],
            stage_max=r["stage_max"],
            basis=r["basis"],
        )
        for r in classify(DB_PATH, normalized, as_of_date=resolved, persist=False)
    ]
