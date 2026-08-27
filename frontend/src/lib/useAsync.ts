import { useEffect, useState } from "react";

/**
 * Fetch-on-deps with out-of-order protection.
 *
 * The guard matters specifically because `as_of` is editable: typing a date
 * fires a request per keystroke, and a slow ranking for 2023-01-0 must never
 * paint over a fast one for 2023-01-01. A stale response is dropped, not
 * rendered.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    fn().then(
      (d) => {
        if (!live) return;
        setData(d);
        setLoading(false);
      },
      (e: unknown) => {
        if (!live) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading };
}
