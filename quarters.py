from collections import defaultdict
from datetime import date

from db import get_connection
from concepts import tags_for

# A real single-quarter duration is ~89-92 days. This range comfortably
# separates it from 6-month (~181-184), 9-month, and 12-month cumulative
# durations without hardcoding a calendar-quarter assumption.
QUARTER_MIN_DAYS = 80
QUARTER_MAX_DAYS = 100


def discrete_quarters(
    db_path: str, cik: str, canonical: str = "revenue", as_of_date: str | None = None
) -> list[dict]:
    """Derive discrete-period values for a canonical concept, one entry per
    real calendar quarter.

    Two ways a quarter's discrete value can be known:
      1. Natively: the filer reported a ~3-month-duration fact directly.
      2. Derived: periods sharing a period_start belong to the same
         fiscal-year YTD chain (Q1, Q1+Q2, Q1+Q2+Q3, annual); the discrete
         value is itself minus the prior entry in the chain.

    Native is preferred whenever both exist for the same period_end — it's
    not just redundant with the derived value, it can catch a chain that's
    derived wrong. A fiscal year whose first XBRL disclosure starts
    mid-year (e.g. only a Q2 YTD fact, no Q1) has no prior chain entry to
    subtract, so the derived value for that first entry is actually the
    full cumulative span mislabeled as a single quarter — AST SpaceMobile's
    2020 H1 is exactly this: derived gives 1,175,000 (all of H1 mislabeled
    as Q2) where the native Q2 tag correctly gives 402,000.

    Reads only from `facts`, so any (period_start, period_end, filing)
    quarantined into concept_conflicts is simply absent here, not
    estimated — a gap in the output reflects a gap in what's trustworthy.

    Each result carries `corroborated`: False if any fact used to compute
    it had no competing alias tag to cross-check against. That's a
    confidence signal, not an error — corroborated=True is not guaranteed
    correct, and corroborated=False is not guaranteed wrong.
    """
    tags = tags_for(canonical)
    tag_priority = {tag: i for i, tag in enumerate(tags)}

    conn = get_connection(db_path)
    placeholders = ",".join("?" * len(tags))
    query = f"""
        SELECT period_start, period_end, value, concept, knowledge_date, corroborated
        FROM facts
        WHERE entity_id = ? AND concept IN ({placeholders})
    """
    params = [cik, *tags]
    if as_of_date is not None:
        query += " AND knowledge_date <= ?"
        params.append(as_of_date)
    rows = conn.execute(query, params).fetchall()
    conn.close()

    # Collapse agreeing alias rows for the same (period_start, period_end)
    # into one representative value: most recently known, tie-broken by
    # concept priority order in concepts.py.
    best = {}
    for period_start, period_end, value, concept, knowledge_date, corroborated in rows:
        key = (period_start, period_end)
        rank = (knowledge_date, -tag_priority.get(concept, len(tags)))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, value, bool(corroborated))

    by_start = defaultdict(list)
    native_quarterly = {}  # period_end -> (rank, period_start, value, corroborated)
    for (period_start, period_end), (rank, value, corroborated) in best.items():
        if period_start is None:
            # Instant-dated fact under a duration concept — e.g. ASC 606
            # transition-date cumulative-effect disclosures tagged as
            # revenue "as of 2018-01-01" (Sinovac, Alnylam). Not a period's
            # revenue; no quarter can be derived from it.
            continue
        by_start[period_start].append((period_end, value, corroborated))

        duration_days = (date.fromisoformat(period_end) - date.fromisoformat(period_start)).days
        if QUARTER_MIN_DAYS <= duration_days <= QUARTER_MAX_DAYS:
            existing = native_quarterly.get(period_end)
            if existing is None or rank > existing[0]:
                native_quarterly[period_end] = (rank, period_start, value, corroborated)

    derived = {}
    for period_start, entries in by_start.items():
        entries.sort()
        prev_end, prev_value, prev_corroborated = period_start, 0.0, True
        for period_end, value, corroborated in entries:
            derived[period_end] = {
                "period_start": prev_end,
                "period_end": period_end,
                "value": value - prev_value,
                "corroborated": corroborated and prev_corroborated,
            }
            prev_end, prev_value, prev_corroborated = period_end, value, corroborated

    result = dict(derived)
    for period_end, (_, period_start, value, corroborated) in native_quarterly.items():
        result[period_end] = {
            "period_start": period_start,
            "period_end": period_end,
            "value": value,
            "corroborated": corroborated,
        }

    return sorted(result.values(), key=lambda r: r["period_end"])


if __name__ == "__main__":
    for r in discrete_quarters("catalyst.db", "0001780312"):
        flag = "" if r["corroborated"] else "  [uncorroborated]"
        print(f"{r['period_start']} -> {r['period_end']}: {r['value']:,.0f}{flag}")
