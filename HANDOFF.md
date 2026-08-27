# Catalyst — session handoff

Restart prompt: paste this file's contents (or point Claude at it) to resume.
Read CLAUDE.md first for the project's non-negotiable invariants — this file
covers what's been built and what's mid-flight, not the design philosophy.

Last updated 2026-08-26.

## Current state in one paragraph

890-company point-in-time universe (space + biotech, survivors and
non-survivors), 69,591 revenue facts ingested from SEC XBRL, every
non-survivor status date prose-verified from filings. The ranking
produces 101 entries — 2 with real filing-backed time-to-catalyst
estimates, the rest ranked by revenue momentum, with stale rows demoted
and labeled. Committed and pushed to GitHub. Next unbuilt piece is the
FastAPI layer.

## Git / GitHub

- Local repo: `/Users/briantran/PycharmProjects/ValuationProj` (its own
  repo — the pre-existing repo at `/Users/briantran` is unrelated, a
  stray home-directory repo with only a README; don't commit there).
- Remote: `git@github.com:btran3291/CATALYST.git`, branch `main`.
- **Two-GitHub-account gotcha**: this machine's SSH key authenticates as
  **btran329**, but the repo is owned by **btran3291**. Pushes work only
  because btran329 was added as a collaborator. `gh` CLI is installed but
  NOT authenticated — plain `git push` over SSH is the working path.
- `.gitignore` excludes `catalyst.db` (regenerable via `bulk_import.py`),
  `.venv/`, `__pycache__/`, `.idea/`, `.claude/`.

## Architecture (all files in project root)

- `schema.sql` / `db.py` — sqlite schema + connection helpers
- `sec_client.py` — rate-limited (10 req/sec), timeout+retry-protected SEC HTTP client
- `concepts.py` — canonical concept -> taxonomy-qualified alias tags (us-gaap + ifrs-full).
  Only "revenue" defined so far. **Lesson learned twice**: never alias a
  category-breakdown tag (e.g. RevenueFromSaleOfGoods) as a synonym of the
  total — it's a component, not an alternate total. Caused real bugs both
  in IFRS and legacy us-gaap tags; both fixed.
- `ingest_sec.py` — fetches SEC companyfacts, groups alias values per
  (period, filing), `partition_group()` resolves conflicts by finding the
  largest mutually-agreeing clique (not all-or-nothing quarantine)
- `quarters.py` — derives discrete-quarter values from YTD facts,
  native-quarterly-tag preferred over YTD-diff when both exist. Skips
  facts with NULL period_start (ASC 606 transition-date disclosures
  tagged as revenue — Sinovac, Alnylam, Capricor; 4 such facts) which
  used to crash it.
- `stages.py` — streak-based stage classifier (0-1 undifferentiated
  pre-revenue since we can't distinguish without asset data; 2 first
  revenue; 3 inflection at streak>=2; 4 scaling at streak>=4; 5 mature via
  buildout_estimates calendar override, or streak-break-with-grace-period
  fallback when no estimate exists), plus the left-censoring rule below.
  Materiality gate for false-positive noise was tried and **reverted** —
  erased real signal along with noise; don't re-attempt without more data
  to calibrate against.
- `discovery.py` — SIC-code-based EDGAR discovery (browse-edgar atom feed
  — note its `<entry title>` is broken, contains literal `ARRAY(0x...)`,
  a bug in SEC's own script; use `<cik>` element instead, and it inherits
  the feed's default xmlns so query it as `a:cik` not bare `cik`).
  `candidate_non_survivors()` / `candidate_survivors()` are mirror images
  (not in / in company_tickers.json). `status_signal()` checks 8-K item
  1.03 (bankruptcy), Form 25/25-NSE (delisting), 15-12B/15-12G/15F-12B
  (deregistration, the F-variant is for foreign issuers).
- `discover_survivors.py` — prints the full currently-listed roster for
  given SIC codes with review signals (ticker, exchange, last annual
  report, SPAC-name flag). Defaults to space codes; pass codes as argv.
- `discover_biotech.py` — the non-survivor biotech pass that produced
  `biotech_candidates.txt`.
- `ranking.py` — ranks active companies we KNOW haven't inflected
  (stage_max <= 2) by revenue momentum, PLUS any company with a real
  catalyst estimate on file regardless of stage range. Sort order:
  estimate-backed by nearest p50, then momentum by streak; stale rows
  demoted below everything (see staleness rule). time_to_catalyst /
  materiality read from catalyst_estimates / buildout_estimates and show
  "no estimate on file" rather than guessing.
  `active_companies()` checks the LATEST status event, not mere existence
  of one — needed because Humanigen (formerly KaloBios) went bankrupt
  twice, 2015 and 2024, with a real operating period between.
- `companies.json` — **the durable source of truth for the universe**.
  Array of `{cik, name, status_events: [{effective_date, status}]}`.
  Empty status_events = active/survivor.
- `bulk_import.py` — idempotent importer for companies.json. Status
  events are a per-company SYNC to the seed (delete rows the seed no
  longer lists, then insert), so date corrections in companies.json
  propagate on rerun. The old INSERT-OR-IGNORE-only gap is fixed —
  plain IGNORE couldn't apply corrections because effective_date is part
  of the unique key, so a corrected date would have inserted a duplicate
  event next to the stale one. Full rerun over 890 companies takes
  several minutes at the SEC rate limit; run it in the background.
- `audit_dates.py` — the (known-limited) regex bankruptcy-date auditor.
  Superseded in practice by the sentence-extraction method below, but
  kept for reference.

## Verification standard (established, don't relax without asking)

Every non-survivor status event needs a real, filing-backed date before
going in companies.json. The gold standard: fetch the actual
8-K/submission text via `sec_client.get_text()`, find "Item 1.03" or
similar, read the prose, confirm the real date. An item code and a filing
date alone are NOT sufficient — that assumption produced 16 wrong dates
(see audit below), and EDGAR's own 1.03 metadata produced 3 outright
false positives.

Full manual verification was an explicit user decision over a
tiered/auto-import system (rejected the tiered option to preserve rigor).

**Method that works** (after `audit_dates.py`'s window-from-anchor regex
failed on 26/30): extract every sentence in the whole 8-K containing both
a full date and a bankruptcy keyword, then read the candidates per
company. The window approach failed because some filings (e.g. Sorrento)
open Item 1.03 with pages of background before the petition sentence.
Scratch scripts lived in session tmp dirs, not the repo.

## Universe state (as of 2026-08-26, see companies.json for ground truth)

890 companies, 144 status events, 69,591 facts, 2,306 concept_conflicts
(biotech brought many alias conflicts — quarantined by design, not
investigated per-company). DB status events verified to match
companies.json exactly (zero mismatches).

Composition:
- 38 original (~9 space, ~29 biotech)
- 47 space survivors (2026-08-24, SIC 3760/3663/3812)
- 763 biotech survivors (2026-08-25, SIC 2834/2836)
- 42 verified biotech non-survivors (2026-08-25)

Both survivor batches are the FULL currently-listed roster for their SIC
codes, added wholesale at user's direction (completeness over curation;
the classifier + left-censoring do the filtering). That includes mature
mega-caps and some non-sector SIC noise — harmless, they classify out.

7 companies 404 on companyfacts (no XBRL on SEC's side —
shells/foreign issuers, left in universe with zero facts): SIPP
International, GelStat, Grey Matters Health, Beroni, InnoCan Pharma,
Nok Therapeutics, Starton Holdings.

## Bankruptcy date audit — DONE (2026-08-24)

**Trigger**: Baudax Bio's recorded date (8-K *filing* date, 2024-02-28)
differed from the actual Chapter 11 *petition* date in the prose
(2024-02-22) — a 6-day gap.

All 30 then-existing bankrupt events prose-verified; 16 corrections:

- Athenex 2023-05-22 -> 2023-05-14 (8 days off — worst case; the 8-K
  bundled the petition with later Nasdaq/delisting items)
- Humanigen 2024: 2024-01-08 -> 2024-01-03; Humanigen/KaloBios 2015:
  2015-12-30 -> 2015-12-29
- Molecular Templates 2025-04-24 -> 2025-04-20
- Athersys 2024-01-08 -> 2024-01-05 (**an earlier handoff's claim that
  Athersys was live-verified as "January 7/8" was wrong** — the 8-K says
  "On January 5, 2024 (the 'Petition Date')")
- Clovis 2022-12-13 -> 2022-12-11 (Sunday petition)
- Akorn -> 2020-05-20, Endo -> 2022-08-16, Acorda -> 2024-04-01,
  PhaseBio -> 2022-10-23 (Sunday), Lannett -> 2023-05-02,
  Teligent -> 2021-10-14, Synergy -> 2018-12-12, Zosano -> 2022-06-01,
  ContraFect -> 2023-12-04 (**Chapter 7, not 11**), Tricida -> 2023-01-11

Confirmed correct as recorded (14): Virgin Orbit, Sorrento, PLx Pharma,
Achaogen, Insys, VIVUS, Infinity, Orexigen, Melinta, Eiger, Gamida Cell
(Chapter 15 recognition of an Israeli proceeding — the date is when the
US recognition petition was filed), Gritstone, Codiak, Allena.

## Biotech bankruptcy candidates — DONE (2026-08-25)

Of 45 bankruptcy-flagged candidates in `biotech_candidates.txt` not
already in the universe, 42 prose-verified and added; **3 rejected as
false positives** (the reason the manual standard exists):

- PETRO USA (0000745543): the "Item 1.03" is a filer error — the 8-K is a
  reverse-stock-split notice, no bankruptcy ever occurred.
- Mymetics (0000927761): 2006 8-K flagged 1.03 in EDGAR metadata but
  contains no bankruptcy content at all.
- Avadel (0001012477): SUBSIDIARY-only bankruptcy (Avadel Specialty
  Pharmaceuticals/Noctiva, Feb 2019); the parent kept operating its
  Phase III program and stayed listed. No company-level bankrupt event.
  Avadel's Form 25 of 2026-02-12 is likely an acquisition — needs
  separate verification if we ever record it.

Notable: Mallinckrodt (filed as "Keenova Therapeutics", 0001567892) has
TWO prose-verified Chapter 11s (2020-10-12, 2023-08-28) — second
Humanigen-style double bankruptcy. 23andMe hides under "Chrome Holding
Co." (0001804591), petition 2025-03-23. Baudax Bio (2024-02-22) — the
company that triggered the date audit — is now in the universe itself.
Novelion's event is a BC voluntary liquidation (2020-01-09 court order)
recorded as bankrupt because the filer reported it under Item 1.03;
LadRx's is a California ABC (2025-07-28), same reasoning.

The remaining ~455 rows of biotech_candidates.txt are delisted/
deregistered/M&A-only candidates — NOT verified, separate pass if needed.

## Left-censoring rule in stages.py (2026-08-24)

The survivor roster exposed a classifier gap: mature seasonal companies
(Motorola, Garmin — streak resets every year, SCALING_STREAK never
reached) and annual-only 20-F filers read as "stage 2 first revenue"
forever. Fix: if a company's EARLIEST observed quarter already has
revenue, the series is left-censored — we never saw the start — so
stage_max=5 (basis gains ",left_censored") while streaks still set
stage_min. Pure observability logic, no magnitude thresholds (the
reverted-materiality-gate lesson). ranking.py correspondingly filters on
stage_max <= 2 ("we KNOW it hasn't inflected"), not stage_min.
Consequence to know: near-zero-start companies get censored too —
NextNav's first observed quarter was $569K (stage 3-5), Helio's $2M.
The escape hatch for those is a hand-entered catalyst estimate.

## Staleness rule in ranking.py (2026-08-25)

A row whose latest observed quarter ended more than STALE_DAYS
(= 6 quarters, ~18 months — smallest cutoff that doesn't punish an
annual-only 20-F filer) before the reference date is DEMOTED below all
fresh rows and labeled "STALE(x.xy old)" — never dropped, because the
company is still listed and filing; often it's a pre-revenue biotech that
stopped tagging a zero revenue line. Stale rows sort among themselves
most-recently-observed first. `rank()` gained an `as_of_date` param,
threaded through `active_companies` and `classify`, so staleness stays
point-in-time-correct in backtests (invariant 1).

## Catalyst estimates on file — and deliberate non-estimates

Two catalyst_estimates rows, both as_of 2026-08-25, full reasoning in
each row's basis column:

- NextNav (0001865631): p10=2028-06-30 / p50=2029-12-31 / p90=2033-12-31.
  Q2 2026 10-Q (accn 0001554855-26-001789): FCC Lower 900 MHz Petition
  for Rulemaking filed 2024-04-16, still pending with NO NPRM, only
  referenced in FCC's 2025-03-27 PNT Notice of Inquiry; company's own
  accounting won't recognize the contingent consideration. Conditional
  on grant — outright denial is a live risk.
- Voyager (0001788060): p10=2030-12-31 / p50=2032-06-30 / p90=2036-12-31.
  Starlab segment (pre-revenue, $56M capex in H1'26). 10-K (accn
  0001628280-26-016543): launch anticipated 2029 on Starship, revenue in
  first full year of operation; $2.8-3.3B cost vs $34.3M grant money
  remaining; CDR incomplete; NASA Phase II CLD not yet competed.

DELIBERATELY no estimate (checked filings 2026-08-25, don't re-litigate
without new filings):
- Firefly: no Eclipse first-launch date stated anywhere in 10-Q or the
  June 2026 424B4; and it's already observably scaling (streak=4, $1.5B
  backlog) — its inflection isn't ahead, it's happening.
- York Space: commercialized years pre-IPO (Tranche 0 delivered 2022,
  $543M backlog) — no discrete "approaching commercialization" catalyst
  exists; left-censored exclusion is simply correct.
- Helio (= Heliospace): niche instruments/services micro-cap growing
  contract-by-contract; no dated buildout of its own.
- Intuitive Machines: NSN lunar data-relay constellation is a real
  buildout (sole awardee, construction-in-progress growing) but NO
  service date stated in the 10-K, and company-level inflection already
  happened (~$90M/qtr diversified, Lanteris/ex-Maxar acquired 2026-01).
  Revisit if a filing ever dates the relay service start.

buildout_estimates (the maturity-override table) is still EMPTY — the
"materiality" column in the ranking reads from it, so it shows "no
estimate on file" for everything.

## Where the ranking stands (2026-08-26)

101 entries: 66 fresh, 35 stale. Top: NextNav (stage 3-5, p50
2029-12-31), Voyager (stage 2-5, p50 2032-06-30), then momentum-only —
Satellogic, Actinium ($35M/qtr), BioLineRx, Evolus ($84M/qtr),
Organogenesis ($44M/qtr), IDEAYA, Invivyd, Pelthos, Momentus, and the
stage-0-1 pre-revenue cohort (Rocket Pharma, Annovis, TuHURA, Oruka…).
Excluded with reasons recorded above: Firefly, York, Helio, Intuitive
Machines (no-estimate decisions), Redwire (genuine mature via streak
fallback, not censoring).

The 42 verified non-survivors correctly never appear — `active_companies`
excludes them. Their value is survivorship-correct backtests
(invariant 2), not today's ranking.

## Planning sequence

1. ~~Ranking generation~~ — done; estimate-aware, staleness-aware
2. ~~Bulk-import tooling~~ — done, sync semantics
3. ~~Survivor-side discovery~~ — done, space AND biotech
4. **API layer (FastAPI) — NOT started, this is next.** User wants
   shareable/multi-user eventually, not just a local tool.
5. Front end — not started, blocked on 4.

## Cross-sector validation (done)

Biotech tested via Intra-Cellular Therapies with ZERO code changes needed
beyond IFRS taxonomy support (SatixFy surfaced that gap, fixed generally).
Confirms the core pipeline (ingest -> quarters -> stages) is
sector-agnostic. The 763-company biotech batch reconfirmed this at scale
— the only fix needed was the NULL period_start guard. Semiconductors and
AI infra (user's other interests) not yet tried but should work the same.

## Known latent issues (not urgent, noted for later)

- Re-emergence events are never recorded, so `active_companies` treats a
  reorganized company as permanently inactive. Affects Humanigen
  (operated 2016-2023 between bankruptcies) and now Mallinckrodt
  (emerged 2022, refiled 2023). The schema supports an 'active' event;
  the data doesn't have one.
- Gamida Cell reports under both ifrs-full AND us-gaap (first
  dual-taxonomy company in the universe) — `partition_group` hasn't been
  stress-tested against a real same-period cross-taxonomy disagreement.
- Endo International and Teligent both have real, unresolved-by-design
  alias conflicts (concept_conflicts) from genuine corporate complexity
  (Endo's 2014 Irish inversion; Teligent's 2017-2019 restatement/SEC
  investigation) — correctly left quarantined, not bugs to fix.
- 2,306 concept_conflicts total across the universe, never audited in
  bulk. Some are certainly the same category-breakdown-tag class of bug
  fixed twice in concepts.py; worth a sampling pass someday.
- `discover_survivors.py`'s SPAC-name regex flags but never excludes —
  correct (AST/Rocket Lab/Planet all came via SPAC), just don't mistake
  the flag for a filter.
