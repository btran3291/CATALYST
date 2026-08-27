import { useState } from "react";
import { api, type RankEntry } from "../api/client";
import { IntervalBar, IntervalLabel, NoEstimate } from "../components/IntervalBar";
import { fmtDuration, fmtValue } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const PAGE = 50;

export function Ranking({
  asOf,
  onOpen,
}: {
  asOf: string;
  onOpen: (cik: string) => void;
}) {
  const [includeStale, setIncludeStale] = useState(true);
  const [estimatesOnly, setEstimatesOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  const { data, error, loading } = useAsync(
    () =>
      api.ranking({
        as_of: asOf,
        include_stale: includeStale,
        estimates_only: estimatesOnly,
        limit: PAGE,
        offset,
      }),
    [asOf, includeStale, estimatesOnly, offset],
  );

  // One domain for every bar in the table, so widths are comparable row to
  // row. It starts at the as_of date (the origin line) so a bar's distance
  // from the left edge reads as "how far out from now".
  const withEstimates = (data?.entries ?? []).filter((e) => e.catalyst);
  const domainStart = asOf;
  const domainEnd =
    withEstimates.length > 0
      ? withEstimates.map((e) => e.catalyst!.p90_date).sort().at(-1)!
      : asOf;

  return (
    <section>
      <header className="view-head">
        <div>
          <h2>Ranking</h2>
          <p className="lede">
            Active companies we know haven't inflected (stage_max &le; 2), by revenue
            momentum — plus any company with a hand-verified catalyst estimate,
            whatever its stage. Stale rows are demoted and labelled, never dropped.
          </p>
        </div>
        <div className="controls">
          <label>
            <input
              type="checkbox"
              checked={includeStale}
              onChange={(e) => {
                setIncludeStale(e.target.checked);
                setOffset(0);
              }}
            />
            include stale
          </label>
          <label>
            <input
              type="checkbox"
              checked={estimatesOnly}
              onChange={(e) => {
                setEstimatesOnly(e.target.checked);
                setOffset(0);
              }}
            />
            estimates only
          </label>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {loading && !data && <div className="loading">ranking {asOf}…</div>}

      {data && (
        <>
          <div className="summary">
            <strong>{data.total}</strong> entries
            <span className="dim">
              {" "}· {data.fresh} fresh, {data.stale} stale · as of {data.as_of}
            </span>
          </div>

          <table className="grid">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Company</th>
                <th>Stage</th>
                <th className="num">Streak</th>
                <th className="num">Latest qtr rev</th>
                <th>Last observed</th>
                <th className="wide">Time to catalyst</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((e, i) => (
                <Row
                  key={e.cik}
                  entry={e}
                  rank={offset + i + 1}
                  asOf={asOf}
                  domainStart={domainStart}
                  domainEnd={domainEnd}
                  onOpen={onOpen}
                />
              ))}
              {data.entries.length === 0 && (
                <tr>
                  <td colSpan={7} className="empty">
                    No entries match these filters as of {data.as_of}.
                    {estimatesOnly &&
                      " No catalyst estimate had been written by that date."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <nav className="pager">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              ← prev
            </button>
            <span className="dim">
              {data.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE, data.total)} of{" "}
              {data.total}
            </span>
            <button
              disabled={offset + PAGE >= data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              next →
            </button>
          </nav>
        </>
      )}
    </section>
  );
}

function Row({
  entry,
  rank,
  asOf,
  domainStart,
  domainEnd,
  onOpen,
}: {
  entry: RankEntry;
  rank: number;
  asOf: string;
  domainStart: string;
  domainEnd: string;
  onOpen: (cik: string) => void;
}) {
  return (
    <tr className={entry.stale ? "stale" : undefined}>
      <td className="num dim">{rank}</td>
      <td>
        <button className="link" onClick={() => onOpen(entry.cik)}>
          {entry.name}
        </button>
      </td>
      <td>
        <span className="stage">{entry.stage}</span>
      </td>
      <td className="num">{entry.streak}</td>
      <td className="num">{fmtValue(entry.latest_quarterly_revenue)}</td>
      <td>
        {entry.as_of}
        {entry.stale && (
          <span className="badge" title="Latest observed quarter is older than ~18 months. Still listed and filing — demoted, not dropped.">
            stale {fmtDuration(entry.age_days)}
          </span>
        )}
      </td>
      <td className="wide">
        {entry.catalyst ? (
          <div className="catalyst-cell">
            <IntervalBar
              p10={entry.catalyst.p10_date}
              p50={entry.catalyst.p50_date}
              p90={entry.catalyst.p90_date}
              domainStart={domainStart}
              domainEnd={domainEnd}
              origin={asOf}
            />
            <IntervalLabel
              p10={entry.catalyst.p10_date}
              p50={entry.catalyst.p50_date}
              p90={entry.catalyst.p90_date}
              from={asOf}
            />
          </div>
        ) : (
          <NoEstimate what="estimate" />
        )}
      </td>
    </tr>
  );
}
