"""Survivor-side discovery: the full currently-listed roster for a set of
SIC codes, as a reviewable list — the systematic version of the ad hoc
AST/Rocket Lab/Planet/BlackSky selection.

Prints signals for human review; adds nothing to companies.json itself.
Survivors carry no status events, so verification is lighter than the
non-survivor side — the review is about whether the entry is a real
operating company in the sector (vs. a blank-check shell or a stale
listing), not about filing-backed dates.
"""
import json
import re
import sys

from discovery import candidate_survivors
from sec_client import get

# Blank-check shells cluster in these SIC codes pre-merger. Flag for
# review only, never auto-exclude: AST/Rocket Lab/Planet all went public
# via SPAC and their post-merger entries are what we want.
SPAC_NAME_RE = re.compile(r"acquisition\s+(corp|company|holdings)|blank\s+check", re.IGNORECASE)

ANNUAL_FORMS = {"10-K", "20-F", "40-F"}


def survivor_signal(cik: str) -> dict:
    """Review signals for a currently-listed candidate: is this a real,
    actively-reporting operating company we can ingest?"""
    data = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = data["filings"]["recent"]
    forms = recent["form"]
    dates = recent["filingDate"]

    last_annual = None
    for form, date in zip(forms, dates):
        if form in ANNUAL_FORMS:
            last_annual = f"{form} {date}"
            break

    name = data.get("name") or ""
    return {
        "cik": cik,
        "name": name,
        "sic": data.get("sic"),
        "sicDescription": data.get("sicDescription"),
        "tickers": data.get("tickers", []),
        "exchanges": data.get("exchanges", []),
        "last_filing_date": dates[0] if dates else None,
        "last_annual": last_annual,
        "spac_flag": bool(SPAC_NAME_RE.search(name)),
    }


if __name__ == "__main__":
    # Same space-sector SIC codes as the non-survivor pass; 4700/4899
    # stay excluded as too broad. Pass alternate codes as CLI args.
    sic_codes = sys.argv[1:] or ["3760", "3663", "3812"]

    with open("companies.json") as f:
        known = {entry["cik"] for entry in json.load(f)}

    candidates = candidate_survivors(sic_codes, known)
    print(f"{len(candidates)} currently-listed CIKs under SIC {'/'.join(sic_codes)}, not already in universe")
    print()

    for cik in candidates:
        s = survivor_signal(cik)
        exch = ",".join(e for e in s["exchanges"] if e) or "?"
        tick = ",".join(s["tickers"]) or "?"
        flags = []
        if s["spac_flag"]:
            flags.append("SPAC?")
        if not s["last_annual"]:
            flags.append("no annual report on file")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        print(
            f"{cik}  {s['name']!r:50s} SIC {s['sic']}  {tick:8s} {exch:12s} "
            f"last_filing={s['last_filing_date']}  annual={s['last_annual']}{flag_str}"
        )
