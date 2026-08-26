import json
import re

from sec_client import get, get_text

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# "On <Date>, ... filed ... petition" — the standard construction in item 1.03 prose.
PETITION_RE = re.compile(
    rf"On\s+({MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})[^.]{{0,400}}?(?:filed|commenc)",
    re.IGNORECASE,
)
MONTH_NUM = {m: i + 1 for i, m in enumerate(
    "January February March April May June July August September October November December".split()
)}


def extract_petition_date(cik: str, accn: str, doc: str) -> str | None:
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc}"
    try:
        text = get_text(url)
    except Exception:
        return None
    clean = re.sub("<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean)
    idx = clean.find("Item 1.03")
    if idx == -1:
        idx = clean.lower().find("bankruptcy or receivership")
    if idx == -1:
        return None
    window = clean[idx: idx + 1500]
    m = PETITION_RE.search(window)
    if not m:
        return None
    month, day, year = m.group(1).capitalize(), int(m.group(2)), int(m.group(3))
    return f"{year:04d}-{MONTH_NUM[month]:02d}-{day:02d}"


with open("companies.json") as f:
    seed = json.load(f)

print(f"{'company':42s} {'recorded':12s} {'prose':12s} verdict")
corrections = {}
for entry in seed:
    cik = entry["cik"]
    recorded = [e["effective_date"] for e in entry["status_events"] if e["status"] == "bankrupt"]
    if not recorded:
        continue
    data = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    r = data["filings"]["recent"]
    name = data.get("name", "")[:40]
    for form, fdate, item, accn, doc in zip(
        r["form"], r["filingDate"], r.get("items", [""] * len(r["form"])), r["accessionNumber"], r["primaryDocument"]
    ):
        if not (form.startswith("8-K") and "1.03" in item.split(",")):
            continue
        if fdate not in recorded:
            continue
        prose = extract_petition_date(cik, accn, doc)
        if prose is None:
            verdict = "could not extract"
        elif prose == fdate:
            verdict = "OK"
        else:
            verdict = f"CORRECT TO {prose}"
            corrections.setdefault(cik, []).append((fdate, prose))
        print(f"{name:42s} {fdate:12s} {str(prose):12s} {verdict}")

print()
print("CORRECTIONS NEEDED:", json.dumps(corrections, indent=2))
