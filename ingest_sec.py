from collections import defaultdict

from db import get_connection, init_db
from sec_client import get
from concepts import alias_specs

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

CONFLICT_RELATIVE_TOLERANCE = 0.05  # small gaps (e.g. tax-inclusive vs exclusive) are expected


def values_conflict(values: list[float], rel_tolerance: float = CONFLICT_RELATIVE_TOLERANCE) -> bool:
    lo, hi = min(values), max(values)
    if hi == lo:
        return False
    denom = max(abs(hi), abs(lo), 1.0)
    return (hi - lo) / denom > rel_tolerance


def partition_group(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split a same-(period, filing) group of alias-tag values into
    (clean, quarantined).

    clean is the largest subset of tags that all mutually agree within
    tolerance — a true clique (every pair agrees), not just "each agrees
    with one common item," since that would let a middle-ground value
    falsely bridge two tags that don't actually agree with each other.

    If no subset exceeds half the group — a 2-item disagreement (no
    majority possible), or every tag disagreeing with every other — there's
    no principled way to pick a winner, so everything is quarantined. This
    is the same all-or-nothing behavior as before, but now only for
    genuinely irresolvable cases instead of every disagreement.
    """
    n = len(items)
    values = [item["value"] for item in items]
    agrees = [
        [i == j or not values_conflict([values[i], values[j]]) for j in range(n)]
        for i in range(n)
    ]

    best_cluster: list[int] = []
    for i in range(n):
        candidate = [j for j in range(n) if agrees[i][j]]
        if all(agrees[a][b] for a in candidate for b in candidate):
            if len(candidate) > len(best_cluster):
                best_cluster = candidate

    if len(best_cluster) * 2 <= n:
        return [], items

    clean_idx = set(best_cluster)
    clean = [items[i] for i in range(n) if i in clean_idx]
    quarantined = [items[i] for i in range(n) if i not in clean_idx]
    return clean, quarantined


def ingest(db_path: str, cik: str, canonical_concepts: list[str]) -> tuple[int, int]:
    data = get(COMPANY_FACTS_URL.format(cik=cik))
    taxonomies = data["facts"]  # e.g. {"us-gaap": {...}} or {"ifrs-full": {...}} or both

    conn = get_connection(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO companies (cik, entity_name) VALUES (?, ?)",
        (cik, data["entityName"]),
    )

    facts_inserted = 0
    conflicts_inserted = 0

    for canonical in canonical_concepts:
        # Group every alias tag's points, across taxonomies, by (period_end,
        # source_ref) — i.e. what a single filing reported for a single
        # period, across all synonym tags whichever taxonomy they're in.
        groups = defaultdict(list)
        for taxonomy, tag in alias_specs(canonical):
            taxonomy_facts = taxonomies.get(taxonomy, {})
            if tag not in taxonomy_facts:
                continue
            qualified_tag = f"{taxonomy}:{tag}"
            for unit, points in taxonomy_facts[tag]["units"].items():
                for point in points:
                    key = (point.get("start"), point["end"], point["accn"])
                    groups[key].append(
                        {
                            "concept": qualified_tag,
                            "start": point.get("start"),
                            "value": point["val"],
                            "unit": unit,
                            "filed": point["filed"],
                        }
                    )

        for (period_start, period_end, source_ref), items in groups.items():
            if len(items) == 1:
                clean, quarantined = items, []
            else:
                clean, quarantined = partition_group(items)
            corroborated = 1 if len(clean) > 1 else 0

            for item in clean:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO facts
                        (entity_id, concept, period_start, period_end, value, unit,
                         knowledge_date, source, source_ref, corroborated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'sec_xbrl', ?, ?)
                    """,
                    (
                        cik,
                        item["concept"],
                        item["start"],
                        period_end,
                        item["value"],
                        item["unit"],
                        item["filed"],
                        source_ref,
                        corroborated,
                    ),
                )
                facts_inserted += cur.rowcount

            for item in quarantined:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO concept_conflicts
                        (entity_id, canonical, period_start, period_end, source_ref,
                         concept, value, unit, knowledge_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cik,
                        canonical,
                        period_start,
                        period_end,
                        source_ref,
                        item["concept"],
                        item["value"],
                        item["unit"],
                        item["filed"],
                    ),
                )
                conflicts_inserted += cur.rowcount

    conn.commit()
    conn.close()
    return facts_inserted, conflicts_inserted


if __name__ == "__main__":
    db_path = "catalyst.db"
    cik = "0001780312"  # AST SpaceMobile

    init_db(db_path)
    facts_inserted, conflicts_inserted = ingest(db_path, cik, ["revenue"])
    print(f"facts inserted: {facts_inserted}")
    print(f"conflicting rows quarantined: {conflicts_inserted}")

    conn = get_connection(db_path)
    conflicts = conn.execute(
        """
        SELECT period_end, source_ref, concept, value
        FROM concept_conflicts
        WHERE entity_id = ?
        ORDER BY period_end, concept
        """,
        (cik,),
    ).fetchall()
    for row in conflicts:
        print("  conflict:", row)

    conn.close()
