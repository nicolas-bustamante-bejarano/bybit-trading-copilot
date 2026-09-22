import type { ReactNode } from "react";
const tones: Record<string, string> = {
  PASS: "border-emerald-500/35 bg-emerald-500/10 text-emerald-300", ADD: "border-cyan-400/40 bg-cyan-400/10 text-cyan-200",
  CONFIRMED: "border-emerald-500/35 bg-emerald-500/10 text-emerald-300", HOLD: "border-amber-400/40 bg-amber-400/10 text-amber-200",
  REDUCE: "border-orange-400/40 bg-orange-400/10 text-orange-200", EXIT: "border-rose-400/40 bg-rose-400/10 text-rose-200",
  INVALIDATE: "border-red-500/50 bg-red-500/15 text-red-200", BREACH: "border-red-500/50 bg-red-500/15 text-red-200",
  INDETERMINATE: "border-slate-500/40 bg-slate-500/10 text-slate-300", PARTIAL: "border-violet-400/30 bg-violet-400/10 text-violet-200",
  STALE: "border-orange-400/30 bg-orange-400/10 text-orange-200", MISSING: "border-slate-600 bg-slate-800 text-slate-400",
};
export function StatusBadge({ children, value }: { children?: ReactNode; value: string }) {
  return <span className={`inline-flex items-center rounded-sm border px-2 py-1 font-mono text-[10px] font-semibold tracking-[0.14em] ${tones[value] ?? tones.INDETERMINATE}`}>{children ?? value}</span>;
}
