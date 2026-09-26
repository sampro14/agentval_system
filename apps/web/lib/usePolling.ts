"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Calls `fetcher` now and then every `intervalMs`, until `enabled` turns false or the component unmounts.
 * A failed call keeps the last good data and exposes the error, and polling continues so the page recovers
 * on its own when the API comes back.
 */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number, enabled = true) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const fetcherRef = useRef(fetcher);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const next = await fetcherRef.current();
        if (!cancelled) {
          setData(next);
          setError(undefined);
        }
      } catch (e) {
        if (!cancelled) setError(e as Error);
      } finally {
        if (!cancelled) timer = setTimeout(run, intervalMs);
      }
    };
    run();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [intervalMs, enabled, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, reload };
}
