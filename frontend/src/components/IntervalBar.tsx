import { daysBetween, fmtDuration } from "../lib/format";

/**
 * A dated milestone drawn as a range, never a point (invariant 3).
 *
 * The reason this is a drawn interval rather than a date in a cell: a p50
 * printed alone reads as a prediction, and these p50s are the midpoint of
 * distributions eight years wide. Showing p10 and p90 as the visually
 * dominant element — with p50 as a tick inside it, not a label beside it —
 * makes the uncertainty the thing you see first.
 *
 * The domain is shared across every row in a table so bars are comparable:
 * a wide bar really is more uncertain than a narrow one next to it.
 */
export function IntervalBar({
  p10,
  p50,
  p90,
  domainStart,
  domainEnd,
  origin,
  height = 22,
}: {
  p10: string;
  p50: string;
  p90: string;
  domainStart: string;
  domainEnd: string;
  /** "Now" for this view (the as_of date) — drawn as a reference line. */
  origin?: string;
  height?: number;
}) {
  const span = Math.max(1, daysBetween(domainStart, domainEnd));
  const pct = (d: string) =>
    Math.max(0, Math.min(100, (daysBetween(domainStart, d) / span) * 100));

  const x10 = pct(p10);
  const x50 = pct(p50);
  const x90 = pct(p90);
  const originPct = origin ? pct(origin) : null;
  const width = Math.max(0.6, x90 - x10);

  return (
    <div
      className="interval"
      style={{ height }}
      title={`p10 ${p10} · p50 ${p50} · p90 ${p90}`}
    >
      <div className="interval-track" />
      {originPct !== null && (
        <div className="interval-origin" style={{ left: `${originPct}%` }} />
      )}
      <div className="interval-bar" style={{ left: `${x10}%`, width: `${width}%` }} />
      <div className="interval-p50" style={{ left: `${x50}%` }} />
    </div>
  );
}

/** The textual companion to the bar. Always shows all three dates. */
export function IntervalLabel({
  p10,
  p50,
  p90,
  from,
}: {
  p10: string;
  p50: string;
  p90: string;
  /** If given, annotate how far out the p50 is from this date. */
  from?: string;
}) {
  return (
    <span className="interval-label">
      <span className="dim">{p10}</span>
      <span className="sep"> – </span>
      <span className="dim">{p90}</span>
      <span className="p50-note">
        {" "}p50 {p50}
        {from ? ` (${fmtDuration(daysBetween(from, p50))})` : ""}
      </span>
    </span>
  );
}

/**
 * The absence of an estimate is a real, reportable answer here — it means
 * nobody has read the filings and written a defensible range yet. It is
 * never filled in with a heuristic, so it gets its own explicit rendering
 * rather than an empty cell that could read as "zero" or "soon".
 */
export function NoEstimate({ what = "estimate" }: { what?: string }) {
  return <span className="no-estimate">no {what} on file</span>;
}
