import { useCallback, useEffect, useRef, useState } from "react";

/** Load data now and again every `ms`. Errors keep the last good data so a blip does not blank the screen. */
export function usePolling<T>(load: () => Promise<T>, ms: number, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    reload();
    const id = setInterval(reload, ms);
    return () => clearInterval(id);
  }, [reload, ms, enabled]);

  return { data, error, reload };
}
