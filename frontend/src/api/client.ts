/**
 * Typed fetch wrapper over the Catalyst API.
 *
 * Every response type here is derived from `schema.d.ts`, which is generated
 * from api.py's own OpenAPI output (`npm run gen:api`). Nothing in this file
 * hand-describes a response shape, so a changed pydantic model surfaces as a
 * compile error rather than an `undefined` in a table cell.
 */
import type { paths } from "./schema";

/**
 * Same-origin in production (FastAPI serves the built app at /app, so the API
 * lives one level up at /). In dev the Vite server is a separate origin, hence
 * the explicit default — the API's CORS is permissive so no proxy is needed.
 */
const BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

type GetOp<P extends keyof paths> = paths[P] extends { get: infer G } ? G : never;

type GetResponse<P extends keyof paths> = GetOp<P> extends {
  responses: { 200: { content: { "application/json": infer R } } };
}
  ? R
  : never;

type GetQuery<P extends keyof paths> = GetOp<P> extends {
  parameters: { query?: infer Q };
}
  ? Q
  : never;

export class ApiError extends Error {
  // Written out longhand rather than as a constructor parameter property:
  // the Vite template sets `erasableSyntaxOnly`, so TS-only syntax that
  // emits runtime code is a compile error.
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * `detail` is FastAPI's error field. It is either a string (our own
 * HTTPExceptions: "unknown CIK 9999999999") or a list of validation objects
 * (422s raised by pydantic before our code runs). Both are worth showing
 * verbatim — a silent "something went wrong" would hide exactly the schema
 * drift this typed client exists to catch.
 */
function detailOf(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((v) =>
          v && typeof v === "object" && "msg" in v
            ? `${(v as { loc?: unknown[] }).loc?.join(".") ?? ""}: ${(v as { msg: string }).msg}`
            : JSON.stringify(v),
        )
        .join("; ");
    }
  }
  return `request failed (${status})`;
}

async function get<P extends keyof paths>(
  path: P,
  opts: { query?: GetQuery<P>; params?: Record<string, string> } = {},
): Promise<GetResponse<P>> {
  let url: string = path as string;
  for (const [k, v] of Object.entries(opts.params ?? {})) {
    url = url.replace(`{${k}}`, encodeURIComponent(v));
  }

  const search = new URLSearchParams();
  for (const [k, v] of Object.entries((opts.query ?? {}) as Record<string, unknown>)) {
    // Omit undefined/null rather than sending "undefined": an absent `as_of`
    // means "today", and the server is the one that must resolve it so the
    // date in the cache key matches the date the classifier sees.
    if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
  }
  const qs = search.toString();

  const res = await fetch(`${BASE}${url}${qs ? `?${qs}` : ""}`);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailOf(body, res.status));
  return body as GetResponse<P>;
}

export type Health = GetResponse<"/health">;
export type UniverseStats = GetResponse<"/universe/stats">;
export type RankingResponse = GetResponse<"/ranking">;
export type RankEntry = RankingResponse["entries"][number];
export type EstimateRange = NonNullable<RankEntry["catalyst"]>;
export type CompanyListResponse = GetResponse<"/companies">;
export type CompanySummary = CompanyListResponse["companies"][number];
export type CompanyDetail = GetResponse<"/companies/{cik}">;
export type SeriesPoint = CompanyDetail["series"][number];
export type QuarterPoint = GetResponse<"/companies/{cik}/quarters">[number];
export type Transition = GetResponse<"/transitions">[number];

export const api = {
  health: () => get("/health"),
  stats: (as_of?: string) => get("/universe/stats", { query: { as_of } }),
  ranking: (query: GetQuery<"/ranking">) => get("/ranking", { query }),
  transitions: (query: GetQuery<"/transitions">) => get("/transitions", { query }),
  companies: (query: GetQuery<"/companies">) => get("/companies", { query }),
  company: (cik: string, as_of?: string) =>
    get("/companies/{cik}", { params: { cik }, query: { as_of } }),
  quarters: (cik: string, as_of?: string) =>
    get("/companies/{cik}/quarters", { params: { cik }, query: { as_of } }),
};
