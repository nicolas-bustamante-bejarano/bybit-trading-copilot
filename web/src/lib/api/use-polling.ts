"use client";
import { useCallback, useEffect, useState } from "react";

export function usePolling<T>(loader: () => Promise<T>, interval = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try { setData(await loader()); setError(null); }
    catch (value) { setError(value instanceof Error ? value.message : "Request failed"); }
    finally { setLoading(false); }
  }, [loader]);
  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, interval);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh, interval]);
  return { data, error, loading, refresh };
}
