from discovery import candidate_non_survivors, status_signal

SIC_CODES = ["2834", "2836"]  # Pharmaceutical Preparations; Biological Products
known = {
    "0001567514",  # Intra-Cellular Therapies
    "0001682852",  # Moderna
    "0000873303",  # Sarepta Therapeutics
    "0001744659",  # Akero Therapeutics
    "0001745999",  # Beam Therapeutics
}

candidates = candidate_non_survivors(SIC_CODES, known)
print(f"{len(candidates)} candidate CIKs not in current listings, not already known")
print()

for cik in candidates:
    s = status_signal(cik)
    if s["last_filing_date"] and s["last_filing_date"] < "2019-01-01":
        continue
    flags = []
    if s["bankruptcy_8k_date"]:
        flags.append(f"bankruptcy_8k={s['bankruptcy_8k_date']}")
    if s["delisted_date"]:
        flags.append(f"delisted={s['delisted_date']}")
    if s["deregistered_date"]:
        flags.append(f"deregistered={s['deregistered_date']}")
    flag_str = ", ".join(flags) if flags else "no clear signal"
    print(f"{cik}  {s['name']!r:45s} SIC {s['sic']} last_filing={s['last_filing_date']}  [{flag_str}]")
