# Catalyst — session handoff

Restart prompt: paste this file's contents (or point Claude at it) to resume.
Read CLAUDE.md first for the project's non-negotiable invariants — this file
covers what's been built and what's mid-flight, not the design philosophy.

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
  native-quarterly-tag preferred over YTD-diff when both exist
- `stages.py` — streak-based stage classifier (0-1 undifferentiated
  pre-revenue since we can't distinguish without asset data; 2 first
  revenue; 3 inflection at streak>=2; 4 scaling at streak>=4; 5 mature via
  buildout_estimates calendar override, or streak-break-with-grace-period
  fallback when no estimate exists). Materiality gate for false-positive
  noise was tried and **reverted** — erased real signal along with noise;
  don't re-attempt without more data to calibrate against.
- `discovery.py` — SIC-code-based EDGAR discovery (browse-edgar atom feed
  — note its `<entry title>` is broken, contains literal `ARRAY(0x...)`,
  a bug in SEC's own script; use `<cik>` element instead, and it inherits
  the feed's default xmlns so query it as `a:cik` not bare `cik`).
  `status_signal()` checks 8-K item 1.03 (bankruptcy), Form 25/25-NSE
  (delisting), 15-12B/15-12G/15F-12B (deregistration, the F-variant is for
  foreign issuers).
- `ranking.py` — ranks active, not-yet-inflected (stage<=2) companies by
  revenue momentum. time_to_catalyst/materiality explicitly show "no
  estimate on file" rather than guessing (both source tables empty).
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
  event next to the stale one.

## Verification standard (established, don't relax without asking)

Every non-survivor status event needs a real, filing-backed date before
going in companies.json. The gold standard demonstrated repeatedly: fetch
the actual 8-K/submission text via `sec_client.get_text()`, find "Item 1.03"
or similar, read the prose, confirm the real date. An item code and a
filing date alone are NOT sufficient — see the completed audit below.

Full manual verification was an explicit user decision over a
tiered/auto-import system (rejected the tiered option to preserve rigor).

## DONE — bankruptcy date audit (completed 2026-08-24)

**What triggered this**: verifying Baudax Bio, discovered the recorded
date (8-K *filing* date, 2024-02-28) differs from the actual Chapter 11
*petition* date in the prose (2024-02-22) — a 6-day gap.

**Method that finally worked** (after `audit_dates.py`'s
window-from-anchor regex failed on 26/30): extract every sentence in the
whole 8-K containing both a full date and a bankruptcy keyword, then read
the candidate sentences per company. The window approach failed because
some filings (e.g. Sorrento) open Item 1.03 with pages of background
before the petition sentence. Scratch scripts lived in the session tmp
dir; `audit_dates.py` in the project root is the (known-limited) regex
version.

**Outcome — all 30 bankrupt-status events are now prose-verified.**
16 corrections applied to companies.json and synced to the DB:

- Athenex 2023-05-22 -> 2023-05-14 (8 days off — worst case; the 8-K
  bundled the petition with later Nasdaq/delisting items)
- Humanigen 2024: 2024-01-08 -> 2024-01-03; Humanigen/KaloBios 2015:
  2015-12-30 -> 2015-12-29
- Molecular Templates 2025-04-24 -> 2025-04-20
- Athersys 2024-01-08 -> 2024-01-05 (**note: the previous handoff's
  claim that Athersys was live-verified as "January 7/8" was wrong** —
  the 8-K says "On January 5, 2024 (the 'Petition Date')")
- Clovis 2022-12-13 -> 2022-12-11 (Sunday petition)
- Akorn -> 2020-05-20, Endo -> 2022-08-16, Acorda -> 2024-04-01,
  PhaseBio -> 2022-10-23 (Sunday), Lannett -> 2023-05-02,
  Teligent -> 2021-10-14, Synergy -> 2018-12-12, Zosano -> 2022-06-01,
  ContraFect -> 2023-12-04 (**Chapter 7, not 11** — only Ch.7 in the
  universe), Tricida -> 2023-01-11

Confirmed correct as recorded (14): Virgin Orbit, Sorrento, PLx Pharma,
Achaogen, Insys, VIVUS, Infinity, Orexigen, Melinta, Eiger, Gamida Cell
(Chapter 15 recognition petition filed April 22, 2024 — confirmed in
prose this session), Gritstone, Codiak, Allena.

**Verified after reimport**: facts=3662, conflicts=91, status_events=63,
companies=38 — identical before and after; DB status events exactly match
companies.json for all 38 companies.

## Universe state (as of 2026-08-25, see companies.json for ground truth)

848 companies. Original 38 (~9 space, ~29 biotech, all non-survivor
dates prose-verified above), plus 47 space-sector survivors (2026-08-24,
SIC 3760/3663/3812), plus 763 biotech survivors (2026-08-25, SIC
2834/2836) — both survivor batches are the FULL currently-listed roster
for their SIC codes, added wholesale at user's direction (completeness
over curation; the classifier + left-censoring do the filtering).
841/848 ingested; 7 companies 404 on companyfacts (no XBRL on SEC's
side — shells/foreign issuers, left in universe with zero facts): SIPP
International, GelStat, Grey Matters Health, Beroni, InnoCan Pharma,
Nok Therapeutics, Starton Holdings. DB after full ingest: 66,476 facts,
2,306 concept_conflicts (biotech brought many alias conflicts —
quarantined by design, not investigated per-company), 63 status events.
Biotech bankruptcy candidates from `biotech_candidates.txt`: VERIFIED
2026-08-25. Of 45 bankruptcy-flagged candidates not already in the
universe, 42 were prose-verified (every bankrupt date read from Item
1.03 8-K text, two extraction passes plus targeted follow-ups) and
added as non-survivors; 3 were rejected as false positives:
- PETRO USA (0000745543): the "Item 1.03" is a filer error — the 8-K is
  a reverse-stock-split notice, no bankruptcy ever occurred.
- Mymetics (0000927761): 2006 8-K flagged 1.03 in EDGAR metadata but
  contains no bankruptcy content at all.
- Avadel (0001012477): SUBSIDIARY-only bankruptcy (Avadel Specialty
  Pharmaceuticals/Noctiva, Feb 2019); the parent kept operating its
  Phase III program. No company-level bankrupt event. Avadel's Form 25
  of 2026-02-12 is likely an acquisition — needs separate verification
  if we ever record it.
Notable finds: Mallinckrodt (as "Keenova Therapeutics", 0001567892) has
TWO prose-verified Chapter 11s (2020-10-12 and 2023-08-28) — second
Humanigen-style double bankruptcy, and like Humanigen its 2022
re-emergence has no "active" event recorded. 23andMe hides under
"Chrome Holding Co." (0001804591), petition 2025-03-23. Baudax Bio
(2024-02-22) — the company that triggered the date audit — is now in.
Novelion's event is a BC voluntary liquidation (2020-01-09 court order)
recorded as bankrupt because the filer reported it under Item 1.03;
LadRx's is a California ABC (2025-07-28), same reasoning.
The remaining ~455 rows of biotech_candidates.txt are delisted/
deregistered/M&A-only candidates — NOT verified, separate pass if ever
needed.

Ingest robustness fix found by the biotech batch: facts with NULL
period_start under a duration concept (ASC 606 transition-date
disclosures tagged as revenue — Sinovac, Alnylam, Capricor) crashed
quarters.py; now skipped with a comment. 4 such facts in the DB.

## Left-censoring rule in stages.py (added 2026-08-24)

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
The escape hatch for those is a hand-entered catalyst estimate (below).

## Catalyst estimates seeded (2026-08-25) — and deliberate non-estimates

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

ranking.py includes estimate-backed companies regardless of stage range
(a verified estimate resolves the left-censoring ambiguity), sorted
nearest-p50 first, then momentum-only entries by streak.

## Where the ranking stands (2026-08-25, post-biotech)

101 entries: 2 estimate-backed (NextNav p50 2029-12-31, Voyager/Starlab
p50 2032-06-30), then 72 stage-2 and 27 stage-0-1 companies by streak.
Notable early-commercialization biotechs surfaced: Actinium ($35M/qtr,
streak=1), Evolus ($84M/qtr), Organogenesis ($44M/qtr), Pelthos,
Invivyd, IDEAYA. Space rows unchanged: Satellogic, Momentus. Excluded
with reasons recorded above: Firefly, York, Helio, Intuitive Machines
(no-estimate decisions), Redwire (genuine mature via streak fallback).

Staleness rule (added 2026-08-25): a row whose latest observed quarter
ended more than STALE_DAYS (= 6 quarters, ~18 months — smallest cutoff
that doesn't punish an annual-only 20-F filer) before the reference date
is DEMOTED below all fresh rows and labeled "STALE(x.xy old)" — never
dropped, because the company is still listed and filing; often it's a
pre-revenue biotech that stopped tagging a zero revenue line. Stale rows
sort among themselves most-recently-observed first. rank() also gained
an as_of_date param, threaded through active_companies and classify, so
staleness stays point-in-time-correct in backtests. Result on current
data: 66 fresh rows, 35 stale.

## Planning sequence agreed on (partially done)

1. ~~Ranking generation~~ — done; now estimate-aware, see above
2. ~~Bulk-import tooling~~ — done, sync semantics (see above)
3. ~~Survivor-side discovery~~ — done for space SIC codes
   (`discover_survivors.py`); biotech not yet run, needs filter decision
4. API layer (FastAPI) — not started. User wants shareable/multi-user
   eventually, not just a local tool, so this is the right shape when we
   get there.
5. Front end — not started, blocked on 4.

## Cross-sector validation (done)

Biotech tested via Intra-Cellular Therapies with ZERO code changes needed
beyond IFRS taxonomy support (SatixFy surfaced that gap, fixed generally).
__Confirms the core pipeline (ingest -> quarters -> stages) is sector-agnostic.
Semiconductors and AI infra (user's other interests) not yet tried but__
should work the same way per this evidence.

## Known latent issues (not urgent, noted for later)

- `ranking.py`'s point-in-time `active_companies(as_of_date=...)` works
  correctly but Humanigen's real 2016-2023 operating period is invisible
  to it because we never recorded a re-emergence "active" event — the
  schema supports it, the data doesn't have it.
- Gamida Cell reports under both ifrs-full AND us-gaap (first dual-taxonomy
  company in the universe) — `partition_group` hasn't been stress-tested
  against a real same-period cross-taxonomy disagreement yet.
- Endo International and Teligent both have real, unresolved-by-design
  alias conflicts (concept_conflicts) from genuine corporate complexity
  (Endo's 2014 Irish inversion; Teligent's 2017-2019 restatement/SEC
  investigation) — correctly left quarantined, not bugs to fix.
