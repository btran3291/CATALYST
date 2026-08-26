from datetime import datetime, timezone

from db import get_connection
from quarters import discrete_quarters

SCALING_STREAK = 4  # consecutive QoQ increases to call it "scaling"
INFLECTION_STREAK = 2  # consecutive QoQ increases to call it "inflection"
MATURE_GRACE_QUARTERS = 2  # quarters a broken streak must stay broken before "mature" fires (fallback path only)


def _get_buildout_p50(conn, cik: str, as_of_date: str | None) -> str | None:
    """Most recent buildout_estimates.p50_date known as of as_of_date (or
    the latest on file if as_of_date is None). None if no estimate exists —
    the caller falls back to the revenue-streak heuristic in that case.
    """
    query = "SELECT as_of_date, p50_date FROM buildout_estimates WHERE cik = ?"
    params = [cik]
    if as_of_date is not None:
        query += " AND as_of_date <= ?"
        params.append(as_of_date)
    query += " ORDER BY as_of_date DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    return row[1] if row else None


def classify_series(
    quarters: list[dict], buildout_p50_date: str | None = None
) -> list[dict]:
    """Rules-based stage classification from a chronological discrete-quarter
    series (as returned by quarters.discrete_quarters). One result per input
    quarter.

    stage_min/stage_max are equal once revenue exists (2-5); (0, 1) before
    any revenue has ever been reported, since pre-product vs. building isn't
    distinguishable from revenue data alone.

    Streak is strict: any quarter-over-quarter decrease resets it to 0.
    max_streak tracks the highest streak ever reached for this company.

    Known limitation, not fixed here: a tiny pre-revenue wiggle (small
    upticks off a near-zero base — AST SpaceMobile's 2020-12-31 quarter is
    an example) reads identically to a real inflection, since the rule has
    no sense of magnitude, only direction. Tried gating on materiality
    relative to the company's own peak revenue (single-quarter and
    trailing-4-quarter-average variants); both erased real transitions
    along with the noise, because some companies' real early growth is
    genuinely an order of magnitude smaller than a later, much larger
    phase — no fixed percentage of a whole-history peak can separate
    "small but real" from "small and noise" in that case. Reverted rather
    than kept, since a gate that's net negative on real data is worse than
    no gate. Revisit only with more data to validate against.

    Maturity has two paths:
      - buildout_p50_date given (from buildout_estimates): mature (stage 5)
        is decided purely by calendar — past that date, with revenue
        already started. Streak-based deceleration is NOT used to call
        mature for a company with a real buildout estimate; capped at
        stage 4 until the estimate date passes.
      - buildout_p50_date is None (no estimate on file — true for all
        companies loaded so far, since buildout_estimates is unseeded):
        falls back to streak deceleration with a grace period
        (MATURE_GRACE_QUARTERS) so a single broken quarter right after
        first reaching SCALING_STREAK doesn't immediately read as
        deceleration.
    """
    # Left-censoring: if the earliest observed quarter already has revenue,
    # we never saw the start — the company could be anywhere from "revenue
    # began just before our data" (stage 2) to "mature for decades"
    # (stage 5). Streaks can raise stage_min, but stage_max stays 5.
    # Without this, a mature seasonal company (streak resets every year, so
    # SCALING_STREAK is never reached) reads as stage 2 "first revenue"
    # forever — Motorola and Garmin did exactly that when the survivor
    # roster landed. Pure observability logic, no magnitude thresholds.
    left_censored = bool(quarters) and quarters[0]["value"] > 0

    results = []
    ever_positive = False
    streak = 0
    max_streak = 0
    quarters_since_scaling = 0
    prev_value = None

    for q in quarters:
        value = q["value"]
        period_end = q["period_end"]
        if value > 0:
            ever_positive = True

        streak = streak + 1 if prev_value is not None and value > prev_value else 0
        max_streak = max(max_streak, streak)
        prev_value = value

        if streak >= SCALING_STREAK:
            quarters_since_scaling = 0
        elif max_streak >= SCALING_STREAK:
            quarters_since_scaling += 1

        if not ever_positive:
            stage_min, stage_max, basis = 0, 1, "no_revenue"
        elif buildout_p50_date is not None:
            if period_end > buildout_p50_date:
                stage_min, stage_max, basis = 5, 5, f"past_buildout_estimate({buildout_p50_date})"
            elif streak >= SCALING_STREAK:
                stage_min, stage_max, basis = 4, 4, f"streak={streak}"
            elif streak >= INFLECTION_STREAK:
                stage_min, stage_max, basis = 3, 3, f"streak={streak}"
            else:
                stage_min, stage_max, basis = 2, 2, f"streak={streak}"
        elif streak >= SCALING_STREAK:
            stage_min, stage_max, basis = 4, 4, f"streak={streak}"
        elif max_streak >= SCALING_STREAK and quarters_since_scaling >= MATURE_GRACE_QUARTERS:
            stage_min, stage_max, basis = 5, 5, f"streak={streak},max_streak={max_streak}"
        elif max_streak >= SCALING_STREAK:
            stage_min, stage_max, basis = 4, 4, f"streak={streak},grace={quarters_since_scaling}"
        elif streak >= INFLECTION_STREAK:
            stage_min, stage_max, basis = 3, 3, f"streak={streak}"
        else:
            stage_min, stage_max, basis = 2, 2, f"streak={streak}"

        if left_censored and stage_min < 5 and not (
            buildout_p50_date is not None and period_end > buildout_p50_date
        ):
            stage_max = 5
            basis += ",left_censored"

        results.append(
            {
                "period_end": period_end,
                "stage_min": stage_min,
                "stage_max": stage_max,
                "basis": basis,
                "corroborated": q["corroborated"],
                "streak": streak,
                "value": value,
            }
        )

    return results


def find_transitions(results: list[dict], from_stage: int, to_stage: int) -> list[dict]:
    transitions = []
    for prev, curr in zip(results, results[1:]):
        if prev["stage_max"] == from_stage and curr["stage_min"] == to_stage:
            transitions.append({"period_end": curr["period_end"], "from": from_stage, "to": to_stage})
    return transitions


def classify(
    db_path: str,
    cik: str,
    canonical: str = "revenue",
    as_of_date: str | None = None,
    persist: bool = True,
) -> list[dict]:
    quarters = discrete_quarters(db_path, cik, canonical, as_of_date)

    conn = get_connection(db_path)
    buildout_p50_date = _get_buildout_p50(conn, cik, as_of_date)

    results = classify_series(quarters, buildout_p50_date)

    if persist:
        computed_at = datetime.now(timezone.utc).isoformat()
        for r in results:
            conn.execute(
                """
                INSERT INTO stage_assignments
                    (cik, as_of_date, stage_min, stage_max, basis, computed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (cik, as_of_date) DO UPDATE SET
                    stage_min = excluded.stage_min,
                    stage_max = excluded.stage_max,
                    basis = excluded.basis,
                    computed_at = excluded.computed_at
                """,
                (cik, r["period_end"], r["stage_min"], r["stage_max"], r["basis"], computed_at),
            )
        conn.commit()
    conn.close()

    return results


def _print_results(name: str, results: list[dict]) -> None:
    print(f"--- {name} ---")
    for r in results:
        label = f"{r['stage_min']}" if r["stage_min"] == r["stage_max"] else f"{r['stage_min']}-{r['stage_max']}"
        print(f"  {r['period_end']}: stage {label}  ({r['basis']})")
    for t in find_transitions(results, 2, 3):
        print(f"  >>> 2->3 transition at {t['period_end']}")
    print()


if __name__ == "__main__":
    ciks = {
        "0001780312": "AST SpaceMobile",
        "0001819994": "Rocket Lab",
        "0001836833": "Planet Labs",
        "0001753539": "BlackSky",
    }
    for cik, name in ciks.items():
        _print_results(name, classify("catalyst.db", cik))

    # Synthetic demo of the buildout-estimate override path — no real
    # company data. Proves the mechanism without asserting anything about
    # an actual company's buildout timeline.
    print("=== synthetic buildout-estimate demo ===")
    demo_quarters = [
        {"period_end": "2024-03-31", "value": 100_000, "corroborated": False},
        {"period_end": "2024-06-30", "value": 150_000, "corroborated": False},
        {"period_end": "2024-09-30", "value": 200_000, "corroborated": False},
        {"period_end": "2024-12-31", "value": 260_000, "corroborated": False},
        {"period_end": "2025-03-31", "value": 300_000, "corroborated": False},
    ]
    print("without buildout estimate (streak-based fallback):")
    for r in classify_series(demo_quarters, buildout_p50_date=None):
        print(f"  {r['period_end']}: stage {r['stage_min']}  ({r['basis']})")
    print("with buildout estimate p50=2024-10-01 (past-date overrides to mature):")
    for r in classify_series(demo_quarters, buildout_p50_date="2024-10-01"):
        print(f"  {r['period_end']}: stage {r['stage_min']}  ({r['basis']})")
