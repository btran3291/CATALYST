import xml.etree.ElementTree as ET

from sec_client import get, get_text

BROWSE_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcompany&SIC={sic}&type=10-K&dateb=&owner=include&count=100&start={start}&output=atom"
)
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# Status-change signals we can recognize from a filing history alone, same
# markers used to hand-verify Virgin Orbit / Astra Space / Terran Orbital:
# Form 25 / 25-NSE = actual removal from exchange listing.
# 15-12B / 15-12G = deregistration (stopped being an SEC reporting company).
# 8-K item 1.03 = bankruptcy/receivership disclosure.
DELISTED_FORMS = {"25", "25-NSE"}
DEREGISTERED_FORMS = {"15-12B", "15-12G"}
BANKRUPTCY_8K_ITEM = "1.03"


def fetch_sic_ciks(sic_code: str) -> list[str]:
    """All CIKs SEC has ever classified under a SIC code, active or not —
    unlike company_tickers.json, this isn't filtered by current listing
    status. Paginates the legacy browse-edgar atom feed.

    Note: the feed's <entry title=...> and <company-info name=...>
    attributes are broken on SEC's end (they contain a stringified Perl/PHP
    array reference, not the company name), so names aren't extracted here
    — fetch per-CIK from submissions.json instead.
    """
    ciks = []
    start = 0
    while True:
        xml_text = get_text(BROWSE_URL.format(sic=sic_code, start=start))
        root = ET.fromstring(xml_text)
        entries = root.findall("a:entry", ATOM_NS)
        if not entries:
            break
        for entry in entries:
            cik_el = entry.find(".//a:cik", ATOM_NS)
            if cik_el is not None and cik_el.text:
                ciks.append(cik_el.text.zfill(10))
        if len(entries) < 100:
            break
        start += 100
    return ciks


def candidate_non_survivors(sic_codes: list[str], known_ciks: set[str]) -> list[str]:
    """CIKs registered under any of sic_codes that are NOT in the current
    tickers file (candidates for hand-verification as delisted/bankrupt/
    acquired) and not already in known_ciks (companies we've already added).
    """
    current = get("https://www.sec.gov/files/company_tickers.json")
    currently_listed = {str(e["cik_str"]).zfill(10) for e in current.values()}

    all_ciks: set[str] = set()
    for sic in sic_codes:
        all_ciks |= set(fetch_sic_ciks(sic))

    return sorted(c for c in all_ciks if c not in currently_listed and c not in known_ciks)


def candidate_survivors(sic_codes: list[str], known_ciks: set[str]) -> list[str]:
    """Mirror of candidate_non_survivors: CIKs registered under any of
    sic_codes that ARE in the current tickers file (currently listed —
    the systematic version of the ad hoc AST/Rocket Lab/Planet roster)
    and not already in known_ciks.
    """
    current = get("https://www.sec.gov/files/company_tickers.json")
    currently_listed = {str(e["cik_str"]).zfill(10) for e in current.values()}

    all_ciks: set[str] = set()
    for sic in sic_codes:
        all_ciks |= set(fetch_sic_ciks(sic))

    return sorted(c for c in all_ciks if c in currently_listed and c not in known_ciks)


def status_signal(cik: str) -> dict:
    """Fetch a candidate's filing history and check for the same
    status-change markers used to hand-verify prior non-survivors. Returns
    a best-effort read, not a confirmed classification — still meant for
    human review, not automatic insertion into company_status_events.
    """
    data = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = data["filings"]["recent"]
    forms = recent["form"]
    dates = recent["filingDate"]
    items = recent.get("items", [""] * len(forms))

    signal = {
        "cik": cik,
        "name": data.get("name"),
        "sic": data.get("sic"),
        "sicDescription": data.get("sicDescription"),
        "last_filing_date": dates[0] if dates else None,
        "delisted_date": None,
        "deregistered_date": None,
        "bankruptcy_8k_date": None,
    }
    for form, date, item in zip(forms, dates, items):
        if form in DELISTED_FORMS and signal["delisted_date"] is None:
            signal["delisted_date"] = date
        if form in DEREGISTERED_FORMS and signal["deregistered_date"] is None:
            signal["deregistered_date"] = date
        if form.startswith("8-K") and BANKRUPTCY_8K_ITEM in item.split(",") and signal["bankruptcy_8k_date"] is None:
            signal["bankruptcy_8k_date"] = date

    return signal


if __name__ == "__main__":
    # Space-specific SIC codes only — 4700/4899 (also seen among our known
    # companies) are too broad/generic to be useful signal without further
    # text filtering, so they're deliberately excluded from this pass.
    SIC_CODES = ["3760", "3663", "3812"]
    known = {
        "0001780312", "0001819994", "0001836833", "0001753539",
        "0001843388", "0001814329", "0001835512",
    }

    candidates = candidate_non_survivors(SIC_CODES, known)
    print(f"{len(candidates)} candidate CIKs not in current listings, not already known")
    print()

    for cik in candidates:
        s = status_signal(cik)
        flags = []
        if s["bankruptcy_8k_date"]:
            flags.append(f"bankruptcy_8k={s['bankruptcy_8k_date']}")
        if s["delisted_date"]:
            flags.append(f"delisted={s['delisted_date']}")
        if s["deregistered_date"]:
            flags.append(f"deregistered={s['deregistered_date']}")
        flag_str = ", ".join(flags) if flags else "no clear signal"
        print(f"{cik}  {s['name']!r:45s} SIC {s['sic']} last_filing={s['last_filing_date']}  [{flag_str}]")
