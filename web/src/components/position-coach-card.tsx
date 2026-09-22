import { AlertTriangle, ArrowDownRight, ArrowUpRight, Check, CircleDashed, ShieldAlert, X } from "lucide-react";
import type { Coach, Position } from "@/lib/api/types";
import { label, money, number } from "@/lib/format";
import { StatusBadge } from "./status-badge";
import { Panel } from "./ui";

function EvidenceRow({ name, value, status }: { name: string; value: string | null | undefined; status: string }) {
  return <div className="flex items-center justify-between gap-3 border-b border-white/6 py-2 last:border-0"><div><div className="text-xs text-slate-500">{name}</div><div className="mt-0.5 text-xs text-slate-300">{label(value)}</div></div><StatusBadge value={status}/></div>;
}
export function PositionCoachCard({ position, coach, expanded=false }: { position: Position; coach: Coach; expanded?: boolean }) {
  const pnl = position.unrealized_pnl ?? Number(coach.position.unrealized_pnl_usdt);
  const SideIcon = position.side === "long" ? ArrowUpRight : ArrowDownRight;
  return <Panel className="overflow-hidden">
    <div className="-m-4 mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-white/8 bg-white/[.018] px-4 py-3">
      <div className="flex items-center gap-3"><div className={`grid size-9 place-items-center rounded ${position.side === "long" ? "bg-emerald-400/10 text-emerald-300" : "bg-rose-400/10 text-rose-300"}`}><SideIcon size={18}/></div><div><div className="text-base font-semibold text-white">{position.symbol}</div><div className="font-mono text-[9px] tracking-[.16em] text-slate-500">{position.side.toUpperCase()} · PERPETUAL</div></div></div>
      <div className="flex items-center gap-2"><StatusBadge value={coach.risk.policy_status}/><StatusBadge value={coach.execution.state}/></div>
    </div>
    <div className="grid grid-cols-2 gap-x-5 gap-y-3 md:grid-cols-4">
      <Datum label="ENTRY" value={money(position.average_entry)}/><Datum label="MARK" value={money(position.mark_price)}/><Datum label="QUANTITY" value={number(position.quantity, 5)}/><Datum label="UNREALIZED PNL" value={money(pnl)} tone={pnl >= 0 ? "text-emerald-300" : "text-rose-300"}/>
    </div>
    <div className="my-4 h-px bg-white/8"/>
    <div className="grid gap-5 xl:grid-cols-[1.1fr_.9fr]">
      <div><div className="mb-2 font-mono text-[9px] tracking-[.16em] text-slate-500">PLAN & MARKET EVIDENCE</div>
        <div className="grid gap-x-5 md:grid-cols-2"><EvidenceRow name="Plan" value={coach.plan.setup_type ?? "No active trade plan"} status={coach.plan.status}/><EvidenceRow name="4H regime" value={coach.market_context.regime_4h} status={coach.market_context.status}/><EvidenceRow name="Location" value={coach.market_context.location} status={coach.market_context.location_status}/><EvidenceRow name="Reaction" value={coach.market_context.reaction ?? "Waiting for live reaction data"} status={coach.market_context.reaction_status}/><EvidenceRow name="1H context" value={coach.market_context.context_1h} status={coach.market_context.status}/><EvidenceRow name="Risk policy" value={`${money(coach.risk.known_group_risk_usdt)} known`} status={coach.risk.policy_status}/></div>
      </div>
      <div className={`rounded border p-4 ${coach.execution.add_allowed ? "border-cyan-400/25 bg-cyan-400/5" : "border-white/8 bg-black/15"}`}><div className="flex items-center justify-between"><span className="font-mono text-[10px] tracking-[.16em] text-slate-500">ADD ALLOWED</span><span className={`flex items-center gap-1.5 text-sm font-semibold ${coach.execution.add_allowed ? "text-cyan-200" : "text-slate-400"}`}>{coach.execution.add_allowed ? <Check size={15}/> : <X size={15}/>} {coach.execution.add_allowed ? "YES" : "NO"}</span></div>
        <div className="mt-3 space-y-2">{coach.execution.blocking_reasons.length ? coach.execution.blocking_reasons.map(reason=><Reason key={reason} text={reason}/>) : <Reason text="No active execution blockers" ok/>}</div></div>
    </div>
    {(expanded || coach.execution.next_conditions.length > 0) && <div className="mt-4 grid gap-4 md:grid-cols-2"><ListBlock icon={<ShieldAlert size={14}/>} title="EVIDENCE MISSING" items={coach.execution.evidence_missing}/><ListBlock icon={<CircleDashed size={14}/>} title="NEXT CONDITIONS" items={coach.execution.next_conditions}/></div>}
    {expanded && <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4"><Datum label="EMA 12" value={number(coach.market_context.ema_12)}/><Datum label="EMA 21" value={number(coach.market_context.ema_21)}/><Datum label="STOCH RSI K" value={number(coach.market_context.stoch_rsi.k)}/><Datum label="INVALIDATION" value={money(coach.execution.invalidation)}/></div>}
  </Panel>;
}
function Datum({ label: text, value, tone="text-slate-200" }: { label: string; value: string; tone?: string }) { return <div><div className="font-mono text-[9px] tracking-[.14em] text-slate-600">{text}</div><div className={`mt-1 font-mono text-sm tabular-nums ${tone}`}>{value}</div></div> }
function Reason({ text, ok=false }: { text: string; ok?: boolean }) { return <div className="flex items-start gap-2 text-xs text-slate-400">{ok ? <Check className="mt-0.5 shrink-0 text-emerald-400" size={13}/> : <AlertTriangle className="mt-0.5 shrink-0 text-amber-400" size={13}/>}<span>{text}</span></div> }
function ListBlock({ icon, title, items }: { icon: React.ReactNode; title: string; items: string[] }) { return <div className="rounded border border-white/8 bg-black/10 p-3"><div className="mb-2 flex items-center gap-2 font-mono text-[9px] tracking-[.14em] text-slate-500">{icon}{title}</div>{items.length ? <ul className="space-y-1.5 text-xs text-slate-400">{items.map(x=><li key={x} className="flex gap-2"><span className="text-slate-700">—</span>{x}</li>)}</ul> : <div className="text-xs text-slate-600">None reported</div>}</div> }
