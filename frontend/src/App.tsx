import { useEffect, useState } from "react";
import { api } from "./api/client";
import { fmtFull, today } from "./lib/format";
import { useAsync } from "./lib/useAsync";
import { Company } from "./views/Company";
import { Ranking } from "./views/Ranking";
import { Transitions } from "./views/Transitions";

type Route = { view: "ranking" } | { view: "transitions" } | { view: "company"; cik: string };

/**
 * The whole app state lives in the hash: `#/company/0001865631?as_of=2023-01-01`.
 *
 * as_of belongs in the URL rather than in React state because a backtest is
 * the thing most worth sending to someone — "here is what the ranking looked
 * like the day before the transition" is a link, not a screenshot plus
 * instructions for reproducing it.
 */
function parseHash(): { route: Route; asOf: string } {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const asOf = new URLSearchParams(qs ?? "").get("as_of") || today();

  let route: Route = { view: "ranking" };
  if (path.startsWith("company/")) {
    route = { view: "company", cik: path.slice("company/".length) };
  } else if (path.startsWith("transitions")) {
    route = { view: "transitions" };
  }
  return { route, asOf };
}

function buildHash(route: Route, asOf: string): string {
  const path =
    route.view === "company"
      ? `company/${route.cik}`
      : route.view === "transitions"
        ? "transitions"
        : "";
  // Today is the default, so leave it out — a link without as_of always means
  // "as of whenever you open this", which is the right default for sharing.
  const qs = asOf === today() ? "" : `?as_of=${asOf}`;
  return `#/${path}${qs}`;
}

export default function App() {
  // The simulated present. Everything on every screen is derived as of this
  // date — it is the single most important control in the app, which is why
  // it sits in the header rather than inside one view, and in the URL rather
  // than in component state.
  const [{ route, asOf }, setState] = useState(parseHash);

  useEffect(() => {
    const onHash = () => setState(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (next: Route, nextAsOf = asOf) => {
    window.location.hash = buildHash(next, nextAsOf);
  };
  const setAsOf = (d: string) => go(route, d || today());
  const openCompany = (cik: string) => go({ view: "company", cik });

  const stats = useAsync(() => api.stats(asOf), [asOf]);
  const isBacktest = asOf !== today();

  return (
    <div className={`app${isBacktest ? " backtest" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="mark">◆</span>
          <span>Catalyst</span>
        </div>

        <nav className="tabs">
          <button
            className={route.view === "ranking" ? "on" : ""}
            onClick={() => go({ view: "ranking" })}
          >
            Ranking
          </button>
          <button
            className={route.view === "transitions" ? "on" : ""}
            onClick={() => go({ view: "transitions" })}
          >
            Transitions
          </button>
        </nav>

        <label className="asof">
          <span>as of</span>
          <input
            type="date"
            value={asOf}
            min="2009-01-01"
            max={today()}
            onChange={(e) => setAsOf(e.target.value)}
          />
          {isBacktest && (
            <button className="reset" onClick={() => setAsOf(today())} title="Back to today">
              reset
            </button>
          )}
        </label>
      </header>

      {isBacktest && (
        <div className="backtest-banner">
          Simulated present: <strong>{asOf}</strong>. Every figure below is restricted to
          what had been filed by that date — later restatements and later-written
          estimates are excluded.
        </div>
      )}

      <main>
        {route.view === "ranking" && <Ranking asOf={asOf} onOpen={openCompany} />}
        {route.view === "transitions" && <Transitions asOf={asOf} onOpen={openCompany} />}
        {route.view === "company" && (
          <Company cik={route.cik} asOf={asOf} onBack={() => go({ view: "ranking" })} />
        )}
      </main>

      <footer className="foot">
        {stats.data ? (
          <span className="dim">
            {fmtFull(stats.data.companies)} companies ({stats.data.active} active,{" "}
            {stats.data.inactive} not) · {fmtFull(stats.data.facts)} facts ·{" "}
            {stats.data.catalyst_estimates} catalyst estimates · filings through{" "}
            {stats.data.latest_knowledge_date ?? "—"}
          </span>
        ) : stats.error ? (
          <span className="error-inline">API unreachable — {stats.error}</span>
        ) : (
          <span className="dim">…</span>
        )}
        <span className="dim">
          Research output with explicit uncertainty. Not a recommendation.
        </span>
      </footer>
    </div>
  );
}
