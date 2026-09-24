"use client";

/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { TradePlan } from "@/lib/api/types";
import { Panel } from "../ui";

export type Fib = { direction: string; swing_low: string | number; swing_high: string | number; levels: Record<string, number> };
export type Range = { range_low: string | number; range_high: string | number };

export function FibRangeEditor({ plan, onChange }: { plan: TradePlan | null; onChange: (fib: Fib | null, range: Range | null) => void }) {
  const [fib, setFib] = useState<Fib | null>(null);
  const [range, setRange] = useState<Range | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    setFib(null); setRange(null); setMessage(""); onChange(null, null);
    if (!plan) {
      return () => { cancelled = true; };
    }
    Promise.all([api.getFibs(plan.id), api.getRanges(plan.id)]).then(([fibs, ranges]) => {
      if (cancelled) return;
      const nextFib = (fibs[0] as Fib | undefined) ?? null;
      const nextRange = (ranges[0] as Range | undefined) ?? null;
      setFib(nextFib); setRange(nextRange); setMessage(""); onChange(nextFib, nextRange);
    }).catch(() => {
      if (cancelled) return;
      setFib(null); setRange(null); onChange(null, null); setMessage("Definitions could not load");
    });
    return () => { cancelled = true; };
  // The parent supplies an inline state bridge; fetching follows the selected plan only.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan]);

  const saveFib = async () => {
    if (!plan || !fib) return;
    try {
      const next = await api.putFib(plan.id, { direction: fib.direction, swing_low: fib.swing_low, swing_high: fib.swing_high }) as Fib;
      setFib(next); onChange(next, range); setMessage("Fib saved");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Fib could not save"); }
  };
  const saveRange = async () => {
    if (!plan || !range) return;
    try {
      const next = await api.putRange(plan.id, { range_low: range.range_low, range_high: range.range_high }) as Range;
      setRange(next); onChange(fib, next); setMessage("Range saved");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Range could not save"); }
  };
  const setFibValue = (key: string, value: string) => setFib((current) => ({ ...(current ?? { direction: "LONG", swing_low: "", swing_high: "", levels: {} }), [key]: value }));
  const setRangeValue = (key: string, value: string) => setRange((current) => ({ ...(current ?? { range_low: "", range_high: "" }), [key]: value }));

  return <Panel title="Fib / range" kicker="PERSISTED STRUCTURE">
    {plan ? <>
      <div className="grid grid-cols-3 gap-2">
        <select value={fib?.direction ?? "LONG"} onChange={(event) => setFibValue("direction", event.target.value)} className="bg-white/5 p-2 text-xs"><option>LONG</option><option>SHORT</option></select>
        <input value={fib?.swing_low ?? ""} onChange={(event) => setFibValue("swing_low", event.target.value)} placeholder="swing low" className="bg-white/5 p-2 text-xs" />
        <input value={fib?.swing_high ?? ""} onChange={(event) => setFibValue("swing_high", event.target.value)} placeholder="swing high" className="bg-white/5 p-2 text-xs" />
      </div>
      <button onClick={() => void saveFib()} className="mt-2 text-xs text-cyan-300">Save Fib</button>
      {!fib && <span className="ml-2 text-xs text-slate-500">No Fib definition</span>}
      <div className="mt-4 grid grid-cols-2 gap-2">
        <input value={range?.range_low ?? ""} onChange={(event) => setRangeValue("range_low", event.target.value)} placeholder="range low" className="bg-white/5 p-2 text-xs" />
        <input value={range?.range_high ?? ""} onChange={(event) => setRangeValue("range_high", event.target.value)} placeholder="range high" className="bg-white/5 p-2 text-xs" />
      </div>
      <button onClick={() => void saveRange()} className="mt-2 text-xs text-cyan-300">Save Range</button>
      {!range && <span className="ml-2 text-xs text-slate-500">No Range definition</span>}
      {message && <div className="mt-2 text-xs text-amber-300">{message}</div>}
    </> : <p className="text-xs text-slate-500">Select a plan.</p>}
  </Panel>;
}
