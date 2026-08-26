from datetime import date

from db import get_connection
from stages import classify

NOT_YET_INFLECTED_MAX_STAGE = 2  # rank companies not yet past first-revenue stage

# A row is stale when its latest observed quarter ended more than this many
# days before the reference date. 6 quarters (~18 months): a quarterly filer
# normally lags <= 2 quarters and an annual-only 20-F filer up to ~5, so this
# is the smallest cutoff that doesn't punish a legitimate annual filer.
# Stale rows are DEMOTED below fresh ones and labeled, never dropped: the
# company is still listed and filing — often a pre-revenue biotech that
# stopped tagging a zero revenue line — so "old observation" is not "gone".
STALE_DAYS = 6 * 91


def active_companies(db_path: str, as_of_date: str | None = None) -> list[tuple[str, str]]:
    """(cik, entity_name) for every company whose LATEST status event is
    'active', or that has no status events at all.

    Checking the latest event rather than the mere existence of one matters:
    a company can emerge from bankruptcy and resume operating, then fail
    again years later — Humanigen (formerly KaloBios Pharmaceuticals) did
    exactly that, bankrupt in 2015 and again in 2024 under the same CIK.
    Treating any event as permanently disqualifying would wrongly exclude
    such a company during the years it was genuinely operating.

    Note this only works if re-emergence is actually recorded as an
    'active' event. We don't currently record those (an emerged company
    just has its old bankrupt/delisted events and nothing newer), so such a
    company still reads as inactive until someone verifies and adds the
    event. The schema supports it; the data doesn't have it yet.

    as_of_date restricts to events known by that date, so this answers
    "was this company active as of X" rather than only "is it active now" —
    required for any point-in-time backtest (invariant 1).
    """
    conn = get_connection(db_path)
    params: list[str] = []
    date_filter = ""
    if as_of_date is not None:
        date_filter = "AND e.effective_date <= ?"
        params.append(as_of_date)

    rows = conn.execute(
        f"""
        SELECT c.cik, c.entity_name
        FROM companies c
        LEFT JOIN (
            SELECT cik, status,
                   ROW_NUMBER() OVER (
                       PARTITION BY cik
                       ORDER BY effective_date DESC, id DESC
                   ) AS rn
            FROM company_status_events e
            WHERE 1=1 {date_filter}
        ) latest
          ON latest.cik = c.cik AND latest.rn = 1
        WHERE latest.status IS NULL OR latest.status = 'active'
        """,
        params,
    ).fetchall()
    conn.close()
    return rows


def _latest_estimate(conn, table: str, cik: str) -> dict | None:
    row = conn.execute(
        f"""
        SELECT as_of_date, p10_date, p50_date, p90_date, basis
        FROM {table} WHERE cik = ? ORDER BY as_of_date DESC LIMIT 1
        """,
        (cik,),
    ).fetchone()
    if row is None:
        return None
    return {"as_of_date": row[0], "p10_date": row[1], "p50_date": row[2], "p90_date": row[3], "basis": row[4]}


def rank(db_path: str, as_of_date: str | None = None) -> list[dict]:
    """Rank active, not-yet-inflected (stage <= 2) companies by revenue
    momentum (current QoQ growth streak) — the only signal we actually
    have real data for right now.

    time_to_catalyst and materiality are read from catalyst_estimates /
    buildout_estimates when a real estimate is on file for that company;
    otherwise explicitly marked "no estimate on file", never guessed. Both
    tables are unpopulated as of this writing, so every entry currently
    shows that for both — the lookup is real and will start returning
    real values automatically once estimates are actually entered.

    A company with zero revenue facts at all is skipped rather than
    assumed pre-revenue: we can't yet tell "genuinely pre-revenue" apart
    from "haven't ingested a concept for this company yet."
    """
    conn = get_connection(db_path)
    companies = active_companies(db_path, as_of_date)
    reference_date = date.fromisoformat(as_of_date) if as_of_date else date.today()

    entries = []
    for cik, name in companies:
        results = classify(db_path, cik, as_of_date=as_of_date, persist=False)
        if not results:
            continue

        latest = results[-1]
        catalyst_est = _latest_estimate(conn, "catalyst_estimates", cik)
        buildout_est = _latest_estimate(conn, "buildout_estimates", cik)

        if latest["stage_max"] > NOT_YET_INFLECTED_MAX_STAGE and catalyst_est is None:
            # Rank only companies we KNOW haven't inflected (stage_max <= 2),
            # not companies that merely might not have. Excludes both the
            # already-inflected (stage_min > 2) and left-censored series
            # (stage 2..5: revenue predates our data, so "approaching
            # commercialization" can't be established from filings alone).
            # A hand-verified catalyst estimate on file is exactly the
            # extra information that resolves that ambiguity, so
            # estimate-backed companies stay in regardless of stage range.
            continue

        age_days = (reference_date - date.fromisoformat(latest["period_end"])).days
        entries.append(
            {
                "cik": cik,
                "name": name,
                "stage": (
                    f"{latest['stage_min']}"
                    if latest["stage_min"] == latest["stage_max"]
                    else f"{latest['stage_min']}-{latest['stage_max']}"
                ),
                "as_of": latest["period_end"],
                "stale": age_days > STALE_DAYS,
                "age_days": age_days,
                "streak": latest["streak"],
                "latest_quarterly_revenue": latest["value"],
                "time_to_catalyst": catalyst_est["p50_date"] if catalyst_est else "no estimate on file",
                "materiality": buildout_est["basis"] if buildout_est else "no estimate on file",
            }
        )

    conn.close()
    # Fresh rows first: estimate-backed (nearest p50), then momentum by
    # streak. Stale rows sink below all fresh ones, most recently observed
    # first — demoted and labeled, never dropped.
    entries.sort(
        key=lambda e: (
            e["stale"],
            e["time_to_catalyst"] == "no estimate on file",
            e["time_to_catalyst"],
            e["age_days"] if e["stale"] else 0,
            -e["streak"],
        )
    )
    return entries


if __name__ == "__main__":
    for e in rank("catalyst.db"):
        stale_marker = f"  STALE({e['age_days'] / 365:.1f}y old)" if e["stale"] else ""
        print(
            f"{e['name']:35s} stage {e['stage']:>4s}  streak={e['streak']}  "
            f"latest_rev={e['latest_quarterly_revenue']:,.0f}  "
            f"as_of={e['as_of']}  "
            f"time_to_catalyst={e['time_to_catalyst']}  "
            f"materiality={e['materiality']}"
            f"{stale_marker}"
        )
