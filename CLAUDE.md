# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Catalyst

## What this is
A research tool that identifies pre-revenue and early-revenue public
companies approaching commercialization, estimates when revenue starts,
and ranks them by time-to-catalyst and materiality.

Archetype: a satellite operator with a constellation under construction.
Known asset count + build rate + launch cadence → estimated service date
→ revenue projection.

Not a trading system. Output is a ranked research list with explicit
uncertainty, never a recommendation or a point-estimate price target.

## Core domain model
Revenue stages, classified from financials (not text):
0 pre-product | 1 building | 2 first revenue | 3 inflection |
4 scaling | 5 mature

Stage 2→3 is the signal of interest. Detected by diffing stage
assignments across quarters, so every value must be stored as a
time series, never a snapshot.

## Non-negotiable invariants
1. POINT-IN-TIME. Every stored value carries the filing date it was
   first reported. Backtests may only use data available as of the
   simulated date. Restated figures are a separate record, never an
   overwrite. Violating this silently invalidates all results.
2. SURVIVORSHIP. Delisted, bankrupt, and acquired companies stay in
   the universe. Never build the universe from a current-listings
   snapshot.
3. NO POINT ESTIMATES for dates or prices. Buildout timelines are
   distributions. Report ranges with probabilities.
4. RATE LIMITING lives in the HTTP client, not at call sites.
   SEC: max 10 req/sec, User-Agent header with real contact info
   required or requests are blocked.

## Data sources
- SEC XBRL frames API — cross-sectional, one concept across all filers
- SEC XBRL companyconcept API — per-company time series
- CelesTrak / space-track.org — independent orbital object counts
- FCC filings — spectrum grants, coverage milestones

Prefer third-party verification over company-reported figures wherever
a registry or regulator counts the same asset.

## Stack
Python. sqlite for storage, parquet for bulk. pandas. requests.
Rules-based classification, not ML — there are too few true stage
transitions to train on.

## Working style
- Explain the approach and the reasoning before writing code. I want to
  understand why each step exists, not just receive files.
- Lead explanations with the observable effect, then the mechanism
  causing it.
- Be direct. Tell me when an approach is wrong rather than building it.
- Build narrow and validate before expanding scope.
