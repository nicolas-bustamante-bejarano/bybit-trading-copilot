import type { StateChange } from "@/lib/api/types";
import { EmptyState, Panel } from "./ui";

const importanceTone = {
  INFO: "bg-cyan-300",
  WARNING: "bg-amber-300",
  CRITICAL: "bg-red-400",
};

function detail(event: StateChange) {
  return event.changes.slice(0, 3).map(change => {
    const name = change.field.replaceAll("_", " ");
    return `${name}: ${String(change.from ?? "none")} → ${String(change.to ?? "none")}`;
  }).join(" · ");
}

export function StateChangeFeed({ events }: { events: StateChange[] }) {
  return <Panel title="Recent state changes" kicker="MEANINGFUL TRANSITIONS">
    {events.length ? <div className="space-y-1">{events.map(event => <div key={event.id} className="grid grid-cols-[58px_88px_1fr] gap-3 border-b border-white/7 py-3 last:border-0">
      <time className="font-mono text-[10px] text-slate-600">{new Date(event.timestamp).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}</time>
      <div className="flex items-start gap-2"><span className={`mt-1 size-1.5 rounded-full ${importanceTone[event.importance]}`}/><span className="font-mono text-[10px] text-slate-400">{event.symbol}</span></div>
      <div><div className="text-sm text-slate-200">{event.summary}</div><div className="mt-1 text-xs text-slate-600">{detail(event)}</div></div>
    </div>)}</div> : <EmptyState title="No meaningful state changes recorded yet." detail="The monitor establishes a silent baseline before recording future transitions."/>}
  </Panel>;
}
