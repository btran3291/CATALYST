import json
import sys

from db import get_connection, init_db
from ingest_sec import ingest

DEFAULT_SEED_PATH = "companies.json"
DEFAULT_ESTIMATES_PATH = "estimates.json"
DEFAULT_CANONICAL_CONCEPTS = ["revenue"]
ESTIMATE_TABLES = ("catalyst_estimates", "buildout_estimates")


def load_seed(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def import_company(db_path: str, entry: dict, canonical_concepts: list[str]) -> dict:
    """Apply one seed entry: insert the company, sync its (already-verified)
    status events, then ingest its revenue facts. This tool does NOT
    verify anything — status_events must already carry real, filing-backed
    dates before they go in the seed file. Idempotent: safe to rerun on an
    existing database or a freshly rebuilt one.

    Status events are a SYNC, not an append: companies.json is the source
    of truth, so events in the database that the seed no longer lists
    (e.g. a date corrected after re-reading the filing prose) are deleted.
    Plain INSERT OR IGNORE can't do this — the unique key includes
    effective_date, so a corrected date would insert a duplicate event
    next to the stale one instead of replacing it.
    """
    cik = entry["cik"]
    name = entry["name"]
    events = entry.get("status_events", [])

    conn = get_connection(db_path)
    conn.execute("INSERT OR IGNORE INTO companies (cik, entity_name) VALUES (?, ?)", (cik, name))
    if events:
        placeholders = ",".join(["(?, ?)"] * len(events))
        params = [v for e in events for v in (e["effective_date"], e["status"])]
        conn.execute(
            f"DELETE FROM company_status_events WHERE cik = ? "
            f"AND (effective_date, status) NOT IN (VALUES {placeholders})",
            [cik, *params],
        )
    else:
        conn.execute("DELETE FROM company_status_events WHERE cik = ?", (cik,))
    for event in events:
        conn.execute(
            "INSERT OR IGNORE INTO company_status_events (cik, effective_date, status) VALUES (?, ?, ?)",
            (cik, event["effective_date"], event["status"]),
        )
    conn.commit()
    conn.close()

    try:
        facts, conflicts = ingest(db_path, cik, canonical_concepts)
        return {"cik": cik, "name": name, "ok": True, "facts": facts, "conflicts": conflicts}
    except Exception as e:
        return {"cik": cik, "name": name, "ok": False, "error": f"{type(e).__name__}: {e}"}


def import_estimates(db_path: str, path: str = DEFAULT_ESTIMATES_PATH) -> dict[str, int]:
    """Load hand-researched catalyst/buildout estimates from the seed file.

    These exist as a file rather than only in the database because
    catalyst.db is gitignored and documented as regenerable: without this,
    rebuilding the database silently destroys the one artifact in the project
    that a human produced by reading filings.

    UPSERT on (cik, as_of_date), not the delete-then-insert SYNC used for
    status events. An estimate is a dated research record: correcting a typo
    in a row should propagate, and adding a new-dated estimate should append
    beside the old one (invariant 1 — a later estimate never overwrites the
    earlier one it supersedes). Deleting is left as a deliberate manual act
    rather than a side effect of editing this file.
    """
    with open(path) as f:
        seed = json.load(f)

    counts = {}
    conn = get_connection(db_path)
    for table in ESTIMATE_TABLES:
        rows = seed.get(table, [])
        for r in rows:
            conn.execute(
                f"""
                INSERT INTO {table} (cik, as_of_date, p10_date, p50_date, p90_date, basis)
                VALUES (:cik, :as_of_date, :p10_date, :p50_date, :p90_date, :basis)
                ON CONFLICT (cik, as_of_date) DO UPDATE SET
                    p10_date = excluded.p10_date,
                    p50_date = excluded.p50_date,
                    p90_date = excluded.p90_date,
                    basis    = excluded.basis
                """,
                r,
            )
        counts[table] = len(rows)
    conn.commit()
    conn.close()
    return counts


def bulk_import(
    db_path: str, seed_path: str = DEFAULT_SEED_PATH, canonical_concepts: list[str] = None
) -> list[dict]:
    canonical_concepts = canonical_concepts or DEFAULT_CANONICAL_CONCEPTS
    entries = load_seed(seed_path)
    results = []
    for entry in entries:
        result = import_company(db_path, entry, canonical_concepts)
        results.append(result)
        if result["ok"]:
            print(f"{result['name']:40s} OK  facts={result['facts']:<4d} conflicts={result['conflicts']}")
        else:
            print(f"{result['name']:40s} FAILED  {result['error']}")

    failed = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} succeeded")
    if failed:
        print("Failed companies (fix and rerun — safe to rerun, already-applied ones are skipped):")
        for r in failed:
            print(f"  {r['cik']}  {r['name']}  {r['error']}")

    return results


if __name__ == "__main__":
    seed_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SEED_PATH
    init_db("catalyst.db")
    bulk_import("catalyst.db", seed_path)
    counts = import_estimates("catalyst.db")
    print(f"estimates: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
