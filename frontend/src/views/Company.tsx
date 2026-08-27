import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type EstimateRange, type SeriesPoint } from "../api/client";
import { IntervalBar, IntervalLabel, NoEstimate } from "../components/IntervalBar";
import { fmtFull, fmtValue } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function Company({
  cik,
  asOf,
  onBack,
}: {
  cik: string;
  asOf: string;
  onBack: () => void;
}) {
  const { data, error, loading } = useAsync(() => api.company(cik, asOf), [cik, asOf]);

  if (error) {
    return (
      <section>
        <button className="link" onClick={onBack}>
          ← ranking
        </button>
        <div className="error">{error}</div>
      </section>
    );
  }
  if (!data || loading) return <div className="loading">loading {cik}…</div>;

  const estimates = [
    ...data.catalyst_estimates.map((e) => ({ kind: "catalyst" as const, e })),
    ...data.buildout_estimates.map((e) => ({ kind: "buildout" as const, e })),
  ];
  const domainEnd =
    estimates.length > 0
      ? estimates
          .map((x) => x.e.p90_date)
          .sort()
          .at(-1)!
      : asOf;

  return (
    <section className="detail">
      <button className="link back" onClick={onBack}>
        ← ranking
      </button>

      <header className="view-head">
        <div>
          <h2>{data.name}</h2>
          <p className="lede">
            CIK {data.cik} ·{" "}
            <span className={`status status-${data.status}`}>{data.status}</span>
            {data.status_date && <span className="dim"> since {data.status_date}</span>}
            <span className="dim"> · known as of {data.as_of}</span>
          </p>
        </div>
      </header>

      <div className="panels">
        <Panel title="Coverage">
          <dl className="kv">
            <dt>facts</dt>
            <dd>{fmtFull(data.fact_count)}</dd>
            <dt>observed</dt>
            <dd>
              {data.first_period_end ?? "—"} → {data.latest_period_end ?? "—"}
            </dd>
            <dt>last filing</dt>
            <dd>{data.latest_knowledge_date ?? "—"}</dd>
            <dt>units</dt>
            <dd>{data.units.join(", ") || "—"}</dd>
            <dt>concepts</dt>
            <dd>{data.concepts.join(", ") || "—"}</dd>
          </dl>
          {data.conflict_count > 0 && (
            <p className="caveat">
              {data.conflict_count} alias disagreement
              {data.conflict_count === 1 ? "" : "s"} quarantined out of the facts below.
              Quarantine is by design — conflicting tags are excluded rather than
              averaged — but it means the series may have real gaps.
            </p>
          )}
        </Panel>

        <Panel title="Status history">
          {data.status_events.length === 0 ? (
            <p className="dim">No status events — active survivor.</p>
          ) : (
            <ul className="events">
              {data.status_events.map((e, i) => (
                <li key={i}>
                  <span className="mono">{e.effective_date}</span>{" "}
                  <span className={`status status-${e.status}`}>{e.status}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="Estimates">
        {estimates.length === 0 ? (
          <p>
            <NoEstimate what="catalyst or buildout estimate" /> — nobody has read this
            company's filings and written a defensible range yet. That absence is
            deliberate; it is never filled in with a heuristic.
          </p>
        ) : (
          <div className="estimates">
            {estimates.map(({ kind, e }, i) => (
              <EstimateCard key={i} kind={kind} e={e} asOf={asOf} domainEnd={domainEnd} />
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Revenue by quarter">
        {data.series.length === 0 ? (
          <p className="dim">No revenue facts as of {data.as_of}.</p>
        ) : (
          <>
            <RevenueChart series={data.series} />
            <p className="caveat">
              Values are as-reported, in {data.units.join("/") || "the filer's unit"} —
              not currency-normalized. Dashed lines mark the first quarter at each
              stage; a stage reached more than once (a streak that breaks and
              re-forms) is marked only on first arrival — see the table below for
              every quarter's assignment.
            </p>
            <SeriesTable series={data.series} />
          </>
        )}
      </Panel>

      {data.transitions_2_to_3.length > 0 && (
        <Panel title="Stage 2 → 3 transitions">
          <ul className="events">
            {data.transitions_2_to_3.map((t, i) => (
              <li key={i}>
                <span className="mono">{t.period_end}</span> — first revenue to
                inflection
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </section>
  );
}

function EstimateCard({
  kind,
  e,
  asOf,
  domainEnd,
}: {
  kind: "catalyst" | "buildout";
  e: EstimateRange;
  asOf: string;
  domainEnd: string;
}) {
  return (
    <article className="estimate">
      <div className="estimate-head">
        <span className="kind">{kind}</span>
        <span className="dim">written {e.as_of_date}</span>
      </div>
      <IntervalBar
        p10={e.p10_date}
        p50={e.p50_date}
        p90={e.p90_date}
        domainStart={asOf}
        domainEnd={domainEnd}
        origin={asOf}
        height={26}
      />
      <IntervalLabel p10={e.p10_date} p50={e.p50_date} p90={e.p90_date} from={asOf} />
      {e.basis && <p className="basis">{e.basis}</p>}
    </article>
  );
}

function RevenueChart({ series }: { series: SeriesPoint[] }) {
  const rows = series.map((s) => ({
    period_end: s.period_end,
    value: s.value,
    stage: s.stage,
    streak: s.streak,
    basis: s.basis,
  }));

  // Mark the FIRST quarter at each distinct stage label, not every change.
  // Marking every change is unreadable in practice: a company whose streak
  // repeatedly breaks (NextNav) oscillates 2-5 / 3-5 for years and produces a
  // dozen overlapping lines. First-arrival keeps the milestone visible while
  // the oscillation itself stays legible in the per-quarter table below.
  const seen = new Set<string>();
  const changes = rows.filter((r, i) => {
    if (seen.has(r.stage)) return false;
    seen.add(r.stage);
    return i > 0; // the opening stage is the baseline, not an arrival
  });

  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={rows} margin={{ top: 20, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="period_end"
            tick={{ fontSize: 11, fill: "var(--dim)" }}
            stroke="var(--line)"
            minTickGap={24}
          />
          <YAxis
            tickFormatter={fmtValue}
            tick={{ fontSize: 11, fill: "var(--dim)" }}
            stroke="var(--line)"
            width={56}
          />
          <Tooltip
            contentStyle={{
              background: "var(--panel)",
              border: "1px solid var(--line)",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--fg)" }}
            formatter={(v: number, _n, p) => [
              `${fmtFull(v)}  (stage ${p.payload.stage}, streak ${p.payload.streak})`,
              p.payload.basis,
            ]}
          />
          {changes.map((c) => (
            <ReferenceLine
              key={c.period_end}
              x={c.period_end}
              stroke="var(--accent)"
              strokeDasharray="3 3"
              label={{ value: c.stage, position: "top", fill: "var(--accent)", fontSize: 10 }}
            />
          ))}
          <Line
            type="monotone"
            dataKey="value"
            stroke="var(--accent)"
            strokeWidth={2}
            dot={{ r: 2 }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function SeriesTable({ series }: { series: SeriesPoint[] }) {
  return (
    <details className="series-details">
      <summary>{series.length} classified quarters</summary>
      <table className="grid compact">
        <thead>
          <tr>
            <th>Period end</th>
            <th className="num">Value</th>
            <th>Stage</th>
            <th className="num">Streak</th>
            <th>Corroborated</th>
            <th>Basis</th>
          </tr>
        </thead>
        <tbody>
          {[...series].reverse().map((s) => (
            <tr key={s.period_end}>
              <td className="mono">{s.period_end}</td>
              <td className="num">{fmtFull(s.value)}</td>
              <td>
                <span className="stage">{s.stage}</span>
              </td>
              <td className="num">{s.streak}</td>
              <td>
                {s.corroborated ? (
                  <span className="ok" title="A second alias tag agreed within tolerance.">
                    ✓
                  </span>
                ) : (
                  <span className="dim" title="Single tag only — no corroborating alias.">
                    —
                  </span>
                )}
              </td>
              <td className="mono small">{s.basis}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <h3>{title}</h3>
      {children}
    </section>
  );
}
