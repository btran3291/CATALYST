# Catalyst — session handoff

Restart prompt: paste this file's contents (or point Claude at it) to resume.
Read CLAUDE.md first for the project's non-negotiable invariants — this file
covers what's been built and what's mid-flight, not the design philosophy.

Last updated 2026-09-01 (buildout estimates: Voyager, Satellogic, AST).

## Current state in one paragraph

890-company point-in-time universe (space + biotech, survivors and
non-survivors), 69,591 revenue facts ingested from SEC XBRL, every
non-survivor status date prose-verified from filings. The ranking
produces 101 entries — 2 with real filing-backed time-to-catalyst
estimates, the rest ranked by revenue momentum, with stale rows demoted
and labeled. The FastAPI read-only layer (`api.py`) and the React front end
(`frontend/`) are both built and verified in a browser against the live
DB. Every planned piece now exists end to end.

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
- `estimates.json` — **the durable source of truth for hand-researched
  estimates**, the counterpart to companies.json. `{catalyst_estimates: [],
  buildout_estimates: []}`, each row `{cik, as_of_date, p10/p50/p90_date,
  basis}`. Created 2026-08-28 after noticing that estimates existed ONLY in
  catalyst.db — which is gitignored and documented as regenerable — so any
  rebuild silently destroyed the one artifact a human produced by reading
  filings. `bulk_import.import_estimates()` UPSERTs on (cik, as_of_date);
  deliberately not the delete-sync used for status events, because an
  estimate is a dated research record and a later one must append beside
  the one it supersedes rather than overwrite it (invariant 1). Deleting an
  estimate is a manual act, never a side effect of editing the seed.
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
- `api.py` — FastAPI read-only HTTP layer. Every connection it opens is
  sqlite `mode=ro` and every pipeline call passes `persist=False`, so
  nothing reachable over HTTP can write to the point-in-time store.
  `as_of` is a query param on every derived-value endpoint, resolved to a
  concrete date once at the request boundary (so the cache key and the
  classifier see the same date), then threaded into `knowledge_date <=`,
  `effective_date <=`, and `as_of_date <=` filters. Rankings and the
  universe-wide transition sweep are `lru_cache`d on (as_of, db mtime+size)
  — rebuilding catalyst.db invalidates every cached result. Endpoints:
  `/health`, `/universe/stats`, `/ranking`, `/transitions`, `/companies`,
  `/companies/{cik}`, `/companies/{cik}/quarters`, `/companies/{cik}/stages`.
  Run with `uvicorn api:app --reload`; `/docs` is live and `/openapi.json`
  is what the front end gets generated against. `/transitions` deliberately
  sweeps ALL companies including non-survivors (invariant 2).
  Note: `fastapi.testclient` needs `httpx2` installed, which the venv lacks
  — smoke-test by running uvicorn on a port and curling it instead.
- `frontend/` — React + Vite + TypeScript UI, three views (ranking,
  company detail, transitions). Types are GENERATED from api.py's own
  OpenAPI output, never hand-written: `npm run gen:api` runs
  `dump_openapi.py` then `openapi-typescript`. **Rerun it after any change
  to an api.py response model** — that regeneration is the whole reason
  this is TypeScript rather than a static page, and skipping it silently
  gives back the untyped situation.
  - `src/api/client.ts` — one generic typed `get()`; every response type is
    derived from the generated schema.
  - `src/components/IntervalBar.tsx` — p10/p50/p90 drawn as a bar with the
    p50 as an interior tick (invariant 3). The domain is shared across all
    rows in a table so bar widths are comparable. `NoEstimate` renders the
    absence explicitly — never an empty cell, which could read as "zero".
  - `as_of` lives in the URL hash (`#/company/0001865631?as_of=2023-01-01`),
    not component state, so a backtest view is a shareable link. Omitted
    from the hash when it equals today.
  - Built with `npm run build`; `api.py` mounts `frontend/dist` at `/app`
    and `/` redirects there, so `uvicorn api:app` still serves everything
    from one process and one origin. The mount is guarded on the directory
    existing — a checkout that never ran `npm run build` gets a working API
    and `/` falls back to `/docs`.
  - **TypeScript is pinned to ~5.9 on purpose.** The Vite template pulls TS
    6, which `openapi-typescript` (7.13) refuses as a peer. Don't bump it
    without checking that generator.
  - The template sets `erasableSyntaxOnly`, so TS-only syntax that emits
    runtime code (constructor parameter properties, enums) is a build error.
- `dump_openapi.py` — writes `frontend/openapi.json` from the `app` object
  without starting a server.
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

890 companies, 144 status events, 69,591 facts, 2,440 concept_conflicts
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

buildout_estimates (the maturity-override table) has THREE rows as of
2026-09-01:

- Voyager (0001788060): p10=2031-12-31 / p50=2034-06-30 / p90=2039-12-31,
  Starlab full operational capability. 10-K accn 0001628280-26-016543 +
  10-Q accn 0001628280-26-052292. Company says $2.8-3.3B program, launch
  anticipated 2029, revenue in the first full year of operation. The
  independent build-rate check is what set the range (the CLAUDE.md
  archetype): construction in progress $37.3M (2024-12-31) -> $143.9M
  (2025-12-31) -> $193.5M (2026-06-30), i.e. ~$100M/yr against a $2.8B+
  program, so ~6-7% of the low-end cost is in the ground and a 2029 launch
  is unreachable at the observed rate. The Nanoracks $217.5M milestone
  funding is now FULLY DRAWN ("All milestone payments have now been earned
  as of June 30, 2026") and NASA Phase II CLD is still uncompeted, so the
  required funding step-change is unsecured. Every percentile is later
  than Voyager's own catalyst estimate by construction — revenue precedes
  full buildout.
  Verified: p50 is in the future, so the calendar override does NOT fire
  and Voyager's stage is unchanged at 2-5; the row is invisible at
  as_of=2026-08-27.

- Satellogic (0001874315): p10=2027-06-30 / p50=2028-06-30 /
  p90=2030-12-31, Merlin constellation fully operational. 10-K accn
  0001874315-26-000013 + 10-Q accn 0001874315-26-000032. Company says
  Merlin.01 launches Q4 2026 and Merlin is "fully operational in the first
  half of 2027", reaffirmed verbatim in the 10-Q filed three weeks before
  the estimate, and Merlin "is fully funded by existing customer
  contracts".
  **First use of third-party asset verification in this project** (the
  CLAUDE.md rule, previously unexercised): CelesTrak GP catalog queried
  2026-08-28 returns 19 NUSAT-* objects with fresh epochs, exactly matching
  the 10-K's "19 NewSat satellites in orbit". Highest is NUSAT-54 with gaps
  at 27-32, 34, 36, 43, 46 — so 19 of ~30 launched, attrition is real, and
  the company count is NOT inflated. Watch out: SNUSAT-2 is an unrelated
  Korean cubesat that matches a naive NAME=NUSAT query and must be excluded.
  Build rate CONFIRMS the schedule rather than contradicting it (the
  opposite of Voyager): satellites under construction $14.8M -> $24.9M in
  H1 2026 (+68%), capex $11.2M vs $2.7M year over year (4.2x). That is why
  p10 sits at the company's own date instead of well past it.
  Countervailing, and the reason p50 is a year out: the Q4 2026 first
  launch had not flown, "fully operational" needs multiple launches plus
  months-long commissioning, and Satellogic SELLS satellites out of the
  operational fleet ($12M sovereign-defense sale 2026-04-30, $8.3M cash in
  H1; "Satellites and other equipment" fell $32.1M -> $28.4M).
  **CONSEQUENCE TO DIARISE**: Satellogic is NOT left-censored (stage 2-2),
  so unlike Voyager this row WILL flip it to stage 5 "mature" the moment
  2028-06-30 passes — verified on a scratch DB by backdating the p50, which
  produced stage 5-5 past_buildout_estimate. Revisit before mid-2028
  rather than letting the calendar decide unattended.

- AST SpaceMobile (0001780312): p10=2028-12-31 / p50=2030-06-30 /
  p90=2034-12-31, Continuous SpaceMobile Service at ~90 BlueBird
  satellites. 10-K accn 0001780312-26-000006 + 10-Q accn
  0001193125-26-342550. The company defines the complete state itself:
  ~45-60 BB satellites for key-market coverage, ~90 for "Continuous
  SpaceMobile Service in all targeted geographical markets" (= "close to
  100% reliable persistent service"), with more beyond 90 for enhancement,
  so 90 is a floor.
  **THE FIRST ROW THAT ACTUALLY CHANGED AN ANSWER.** AST read stage 5-5
  "mature" via the streak-break fallback (max_streak=4) — plainly wrong for
  a company mid-buildout whose revenue went $1.9M/qtr (2024) to $31.5M/qtr
  (2026). The calendar override replaces that: AST is now stage 2-5, and
  the whole tail of the series re-read (2025-06-30 onward was 5-5, now
  2-5/3-5). It will not read mature again until 2030-06-30 — revisit first.
  **CelesTrak naming trap**: a NAME=BLUEBIRD query returns ZERO and would
  read as "no constellation". The catalog names them SPACEMOBILE-nnn.
  Verified 2026-09-01: 12 operational (SPACEMOBILE-001..006, 008..013, all
  53.0 deg), plus BLUEWALKER-3 (the prototype, catalogued separately) and 5
  "SPACEMOBILE-00n DEB" debris objects from Block 1 array deployments. The
  missing 007 is real — the 10-Q reports BB7 was left in too low an orbit
  by the New Glenn 3 upper stage on 2026-04-19 and de-orbited ($21.6M
  insurance received).
  CADENCE vs PLAN is what set the range: the 10-K planned "45 to 60 Block 2
  BB satellites by the end of 2026"; with four months left there are 7 —
  a 6-8x miss on a one-year plan, NOT revised in the August 10-Q. Recent
  cadence is far better than the annual average (6 satellites in July and
  August 2026, ~3/month) but has held for two months, not two years.
  Build rate and funding are NOT the constraint: CIP $1,122.0M -> $1,724.1M
  in H1 2026 (+$602M), cash capex $859.2M vs $430.6M year over year, cash
  and restricted cash $2.7B. Satellites are being built faster than flown;
  launch capacity binds. But ~$1.7B/yr capex against $2.7B cash means more
  financing is needed before 90 are up.
  78 satellites remain: ~26 months at 3/month, ~39 at 2/month.
  KNOWN LIMITATION: this fixed the maturity error but AST is still ABSENT
  from the ranking, because left-censoring keeps stage_max=5 and the
  ranking filters stage_max <= 2. The escape hatch is the usual one — a
  hand-entered catalyst estimate. Not written; nobody has done that
  filing work yet.

WHEN buildout_estimates APPLIES AT ALL (rule made explicit 2026-09-01
after the Momentus pass, so the next one doesn't have to rediscover it):
only for a company ACCUMULATING AN ASSET BASE TOWARD A DEFINED COMPLETE
STATE. The tell is a growing construction-in-progress (or equivalent) line
plus a stated target for what "complete" means. Voyager (CIP $193.5M,
Starlab) and Satellogic (satellites under construction $24.9M, Merlin)
qualify. NextNav (the buildout is a partner's capex) and Momentus (sells
missions; vehicles are consumed, not accumulated) do not — and for those
the honest output is no row, not a softer range. A fabricated p50 is worse
than an absent one here because it eventually fires the stage-5 calendar
override.

DELIBERATELY no BUILDOUT estimate for Momentus (checked 2026-09-01;
10-K accn 0001628280-26-022291, 10-Q accn 0001628280-26-055624 — don't
re-litigate without a change of business model, not merely new filings).
Momentus sells MISSIONS, not capacity: build one Orbital Service Vehicle,
fly it, it deorbits. There is no accumulating asset base to complete.
- Total property, machinery and equipment NET = $464K at 2026-06-30, down
  from $953K; GROSS fell $5,251K -> $1,441K as leasehold improvements and
  machinery were disposed. For scale, Voyager's CIP is $193.5M and
  Satellogic's satellites under construction $24.9M — three orders of
  magnitude apart.
- No construction-in-progress line item exists at all.
- Zero matches in the whole 10-K for any target fleet size / constellation
  count. The forward statement is "We plan to EVENTUALLY operate a family
  of progressively larger and more capable OSVs" — no date, no count.
- CelesTrak 2026-09-01 confirms the consumption model: only VIGORIDE-3, -5,
  -7 tracked (plus MOMENTUS-X1), not an accumulating fleet.
- The only dated forward item is a MISSION, not a buildout: "The Company
  plans to launch its Vigoride 8 mission in 2027 carrying two payloads for
  NASA under contracts it has been awarded."
Momentus is stage 2-2 and NOT left-censored, so an invented p50 would
eventually mark a company with $25K of quarterly revenue as mature.
Context if revisited: cash $107.6M at 2026-06-30 (much improved; no
substantial-doubt conclusion in the latest 10-Q, the 10-K's going-concern
mention is a risk-factor bullet), revenue $0-3.2M and erratic across six
years with no trend. A CATALYST estimate anchored on the contracted
Vigoride 8 / NASA 2027 mission was offered and declined as too thin —
one contracted mission is weak evidence for a distribution.

DELIBERATELY no BUILDOUT estimate for NextNav (checked 2026-08-28,
10-K accn 0001554855-26-000328 — don't re-litigate without new filings).
NextNav has no company-owned asset base under construction:
- "we anticipate the capital expenditures associated with the network
  deployment will be associated with our future 5G partnerships"
- "Our plans to deploy NextGen with one or more network partners could
  result in minimal capital expenditures by us"
- "We anticipate that the radio network infrastructure will be deployed
  by, and the broadband services operated by those operators"
Its OWN network is already built ("Our TerraPoiNT network is deployed,
operated, and maintained by us"; Pinnacle nationwide via AT&T
co-location; FCC accepted build-out showings for 78 of 154 LMS licenses
2023-04-17/18), and the financials agree — network under construction
$582K at 2025-12-31, DOWN from $1,664K, with gross PNT network shrinking
too. So the future buildout is a partner's capex program gated on a
rulemaking with no NPRM.
Recording it as already-complete was considered and rejected by the user
after the consequence was probed on a scratch DB: a past-dated p50 fires
the calendar override and makes NextNav stage 5-5
"past_buildout_estimate(2023-04-18)" — i.e. MATURE — while its own
catalyst estimate says material revenue is still ~3 years out. The two
would contradict each other.

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
4. ~~API layer (FastAPI)~~ — done, read-only and point-in-time-correct
5. ~~Front end~~ — done, React/Vite/TS generated against `/openapi.json`

Nothing on the original sequence is left. Candidates for what's next, none
started: more `buildout_estimates` rows (Voyager, Satellogic and AST so
far, so
"materiality" still reads "no estimate on file" for everything else); the
2,440-row concept_conflicts
sampling pass; extending the universe beyond space + biotech (semis, AI
infra); real multi-user deployment (auth, hosting) if it's going to be
shared beyond a local process.

## Point-in-time leak in ranking.py — FIXED (2026-08-27)

Found while smoke-testing the API: `ranking._latest_estimate()` had no
`as_of_date` filter, so a backtest at any date returned estimates entered
later. A 2023-01-01 ranking was showing NextNav's 2026-08-25 catalyst
range — and worse, promoting NextNav into the estimate-backed sort tier
and past the `stage_max <= 2` filter on the strength of an estimate that
did not exist yet. Straight invariant 1 violation.

Fixed by mirroring `stages._get_buildout_p50()`, which already did this
correctly: `AND as_of_date <= ?` when a date is given, unfiltered when it
is None. Verified at the boundary — `estimates_only` returns 2 rows at
2026-08-26 and 2026-08-25, 0 rows at 2026-08-24 (the estimates' own
as_of_date is 2026-08-25). The 2023 ranking dropped 79 -> 78 entries;
the entry that vanished is NextNav, which is correct.

`ranking.py` also now emits the full `catalyst_estimate` /
`buildout_estimate` dicts alongside the CLI's single-p50 strings, so the
API can render p10/p50/p90 as a range and never present the p50 as a date
we believe (invariant 3).

Lesson worth generalizing: any new table joined into a query needs its own
point-in-time predicate. Two of the three estimate lookups had one; the
third was written later and didn't.

## Front end decisions worth not re-litigating (2026-08-27)

Stack was an explicit user choice of React + Vite + TS over a
no-build-step static page, after a written pro/con. The deciding argument
was compile-time safety against api.py's young response models; the
accepted cost is the npm toolchain (which bit immediately — see the TS 6
pin above).

Display rules that exist for invariant reasons, not taste:
- A bare p50 is never rendered anywhere. Every estimate shows p10 and p90
  as the dominant visual element with p50 as a tick inside the range.
- "no estimate on file" is rendered explicitly wherever an estimate is
  absent, because a blank cell reads as zero or as "soon".
- Stale rows are dimmed and badged with their age, never dropped.
- Revenue carries no currency symbol — values are as-reported and NOT
  normalized (USD/EUR/CHF/CAD/GBP all appear), so a "$" would be a
  fabrication. The company page names the actual unit.
- Counts that hit a page cap are labelled "200+ … capped", never reported
  as if they were totals.
- The revenue chart marks only the FIRST quarter at each distinct stage.
  Marking every stage change was tried and is unreadable — NextNav
  oscillates 2-5 / 3-5 for years and produced a dozen overlapping labels.
  Per-quarter assignments live in the expandable table below the chart.

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
- 2,440 concept_conflicts total across the universe, never audited in
  bulk. Some are certainly the same category-breakdown-tag class of bug
  fixed twice in concepts.py; worth a sampling pass someday.
- `discover_survivors.py`'s SPAC-name regex flags but never excludes —
  correct (AST/Rocket Lab/Planet all came via SPAC), just don't mistake
  the flag for a filter.
