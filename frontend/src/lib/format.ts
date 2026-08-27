/** Display helpers. All of them fail loudly-ish rather than inventing values. */

/**
 * Revenue is NOT currency-normalized upstream — filers report in USD, EUR,
 * CHF, CAD, GBP and the API passes the raw number through. So no currency
 * symbol is ever rendered here; a "$" on a Swiss filer's number would be a
 * fabrication. The unit is shown separately on the company page, where the
 * actual `units` list is available.
 */
export function fmtValue(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return v.toFixed(0);
}

export function fmtFull(v: number): string {
  return new Intl.NumberFormat("en-US").format(v);
}

export const DAY = 86_400_000;

export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / DAY);
}

/** "1.6y" / "7mo" — used for staleness age and time-to-catalyst distance. */
export function fmtDuration(days: number): string {
  if (Math.abs(days) >= 365) return `${(days / 365).toFixed(1)}y`;
  if (Math.abs(days) >= 30) return `${Math.round(days / 30)}mo`;
  return `${days}d`;
}

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Zero-padded 10-char CIK, matching how they're stored. */
export function padCik(cik: string): string {
  return cik.replace(/\D/g, "").padStart(10, "0");
}
