import { API_URL } from "@/lib/api";

// Public transparency page. These live numbers are also where your resume metrics come from.
export const revalidate = 300; // refresh at most every five minutes

type Stats = {
  window_days: number;
  overall: Record<string, number | null>;
  routes: { route: string; n: number; avg_cost: number | null }[];
};

const LABELS: Record<string, string> = {
  small: "Fast model",
  large: "Reasoning model",
  cached: "Answered from cache",
  refused: "Outside Ontario tenancy law",
  budget: "Paused by daily limit",
};

function pct(v: number | null | undefined) {
  return v == null ? "No data yet" : `${(v * 100).toFixed(1)}%`;
}

export default async function StatsPage() {
  let stats: Stats | null = null;
  try {
    const res = await fetch(`${API_URL}/api/stats?days=30`, { next: { revalidate: 300 } });
    if (res.ok) stats = await res.json();
  } catch {
    stats = null;
  }

  if (!stats) {
    return <p className="error">Performance data is unavailable right now. Refresh in a few minutes.</p>;
  }
  const o = stats.overall;
  const rows: [string, string][] = [
    ["Questions answered", String(o.queries ?? 0)],
    ["People who asked", String(o.unique_users ?? 0)],
    ["Answered from cache", pct(o.cache_hit_rate)],
    ["Answers with valid citations", pct(o.citation_valid_rate)],
    ["Rated helpful", pct(o.helpful_rate)],
    ["Typical response time", o.p50_latency_ms == null ? "No data yet" : `${(o.p50_latency_ms / 1000).toFixed(1)} s`],
    ["Slowest 5% of responses", o.p95_latency_ms == null ? "No data yet" : `${(o.p95_latency_ms / 1000).toFixed(1)} s`],
    ["Average cost per question", o.avg_cost_usd == null ? "No data yet" : `$${Number(o.avg_cost_usd).toFixed(4)}`],
  ];

  return (
    <>
      <section className="intro">
        <h1>How TenantWise performs</h1>
        <p className="lede">Live numbers from the last {stats.window_days} days, published so anyone can check them.</p>
      </section>
      <dl className="stats">
        {rows.map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
        ))}
      </dl>
      <h2 className="section-title">Where questions went</h2>
      <table className="routes">
        <thead><tr><th>Path</th><th>Questions</th><th>Average cost</th></tr></thead>
        <tbody>
          {stats.routes.map((r) => (
            <tr key={r.route}>
              <td>{LABELS[r.route] ?? r.route}</td>
              <td>{r.n}</td>
              <td>{r.avg_cost == null ? "$0" : `$${Number(r.avg_cost).toFixed(4)}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
