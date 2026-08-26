import json
import sys

from db import get_connection, init_db
from ingest_sec import ingest

DEFAULT_SEED_PATH = "companies.json"
DEFAULT_CANONICAL_CONCEPTS = ["revenue"]


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
