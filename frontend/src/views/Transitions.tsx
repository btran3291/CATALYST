import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../lib/useAsync";

// The endpoint returns a bare list with no total, so a full page is
// indistinguishable from "exactly this many exist". Labelled as capped rather
// than reported as a count — see the summary line below.
const LIMIT = 200;

export function Transitions({
  asOf,
  onOpen,
}: {
  asOf: string;
  onOpen: (cik: string) => void;
}) {
  const [from, setFrom] = useState(2);
  const [to, setTo] = useState(3);
  const [since, setSince] = useState("");

  const { data, error, loading } = useAsync(
    () =>
      api.transitions({
        as_of: asOf,
        from_stage: from,
        to_stage: to,
        since: since || undefined,
        limit: LIMIT,
      }),
    [asOf, from, to, since],
  );

  return (
    <section>
      <header className="view-head">
        <div>
          <h2>Transitions</h2>
          <p className="lede">
            Stage crossings detected by diffing classifications across quarters. 2 → 3
            (first revenue → inflection) is the signal this project exists to find.
            Delisted, bankrupt and acquired companies are <em>included</em> — excluding
            them would delete exactly the observations survivorship bias eats.
          </p>
        </div>
        <div className="controls">
          <label>
            from
            <select value={from} onChange={(e) => setFrom(Number(e.target.value))}>
              {[0, 1, 2, 3, 4, 5].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            to
            <select value={to} onChange={(e) => setTo(Number(e.target.value))}>
              {[0, 1, 2, 3, 4, 5].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            since
            <input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
          </label>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {loading && !data && <div className="loading">sweeping the universe…</div>}

      {data && (
        <>
          <div className="summary">
            <strong>
              {data.length}
              {data.length === LIMIT ? "+" : ""}
            </strong>{" "}
            crossings
            <span className="dim">
              {" "}· stage {from} → {to} · as of {asOf}
              {data.length === LIMIT && " · capped at the first 200, narrow with “since”"}
            </span>
          </div>
          <table className="grid">
            <thead>
              <tr>
                <th>Period end</th>
                <th>Company</th>
                <th className="num">From</th>
                <th className="num">To</th>
              </tr>
            </thead>
            <tbody>
              {data.map((t, i) => (
                <tr key={`${t.cik}-${t.period_end}-${i}`}>
                  <td className="mono">{t.period_end}</td>
                  <td>
                    <button className="link" onClick={() => onOpen(t.cik)}>
                      {t.name}
                    </button>
                  </td>
                  <td className="num">{t.from_stage}</td>
                  <td className="num">{t.to_stage}</td>
                </tr>
              ))}
              {data.length === 0 && (
                <tr>
                  <td colSpan={4} className="empty">
                    No stage {from} → {to} crossings known as of {asOf}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
