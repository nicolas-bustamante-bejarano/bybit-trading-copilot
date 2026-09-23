"use client";

/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { Coach, Sizing, TradePlan } from "@/lib/api/types";
import { usePolling } from "@/lib/api/use-polling";
import { FibRangeEditor, type Fib, type Range } from "./workspace/fib-range-editor";
import { FlowPanel } from "./workspace/flow-panel";
import { PlanEditor } from "./workspace/plan-editor";
import { PriceChart } from "./workspace/price-chart";
import { StructureEditor } from "./workspace/structure-editor";
import { PageHeader, Panel } from "./ui";

const symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "BONKUSDT"];
const frames = ["5m", "15m", "1h", "4h", "1D", "3D"];

export function WorkspaceClient() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [frame, setFrame] = useState("1h");
  const [plans, setPlans] = useState<TradePlan[]>([]);
  const [plan, setPlan] = useState<TradePlan | null>(null);
  const [coach, setCoach] = useState<Coach | null>(null);
  const [sizing, setSizing] = useState<Sizing | null>(null);
  const [fib, setFib] = useState<Fib | null>(null);
  const [range, setRange] = useState<Range | null>(null);

  const loadChart = useCallback(() => api.getChart(symbol, frame), [symbol, frame]);
  const loadStructures = useCallback(() => api.getChartStructures(symbol), [symbol]);
  const loadChanges = useCallback(() => api.getStateChanges(50), []);
  const loadCoach = useCallback(() => api.getPositionCoach(symbol), [symbol]);
  const { data: chart } = usePolling(loadChart, 5000);
  const { data: structures, refresh: refreshStructures } = usePolling(loadStructures, 15000);
  const { data: changes } = usePolling(loadChanges, 15000);
  const { data: polledCoach } = usePolling(loadCoach, 15000);

  const refreshPlans = async () => setPlans(await api.getTradePlans());
  useEffect(() => {
    void refreshPlans();
    setPlan(null);
    setSizing(null);
    setFib(null);
    setRange(null);
  }, [symbol]);
  useEffect(() => setCoach(polledCoach), [polledCoach]);

  const relevant = plans.filter((item) => item.symbol === symbol);
  const active = relevant.filter((item) => item.lifecycle_status === "ACTIVE");
  const permission = !coach ? "UNAVAILABLE" : coach.execution.state === "ADD" && coach.execution.add_allowed ? "ADD ALLOWED" : coach.execution.state === "HOLD" ? "HOLD · ADD LOCKED" : coach.execution.state;
  const size = async () => {
    if (!plan) return;
    setSizing(await api.getSizing({
      symbol,
      side: plan.side,
      entry: (plan.entry_probe_plan as Record<string, string>).entry,
      hard_invalidation: plan.hard_invalidation,
      max_risk_percent: plan.max_risk_percent,
      correlation_group: plan.correlation_group,
      trade_plan_id: plan.id,
    }));
  };

  return <>
    <PageHeader eyebrow="LIVE TRADING WORKSPACE" title="Chart, plan, and execution discipline" detail="Read-only analysis. Every exchange action remains manual on Bybit." />
    <div className="mb-4 flex flex-wrap gap-2">
      {symbols.map((item) => <button key={item} onClick={() => setSymbol(item)} className={item === symbol ? "rounded bg-cyan-400/15 px-3 py-2 text-xs text-cyan-200" : "rounded bg-white/5 px-3 py-2 text-xs"}>{item}</button>)}
      {frames.map((item) => <button key={item} onClick={() => setFrame(item)} className="px-2 text-xs text-slate-400">{item}</button>)}
    </div>
    <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_390px]">
      <div className="space-y-4">
        <Panel title={`${symbol} · ${frame}`} kicker="TEMPORARY 5s MARKET REFRESH">
          <PriceChart chart={chart} plan={plan} structures={structures ?? []} changes={changes ?? []} fib={fib} range={range} />
        </Panel>
        <FlowPanel coach={coach} />
        <StructureEditor symbol={symbol} timeframe={frame} rows={structures ?? []} refresh={refreshStructures} />
      </div>
      <aside className="space-y-4">
        <Panel title="Plan selection" kicker={active.length > 1 ? "AMBIGUOUS" : "PERSISTED INTENT"}>
          {active.length > 1 && <p className="mb-2 text-xs text-rose-300">Multiple active plans: sizing remains safely blocked.</p>}
          <select value={plan?.id ?? ""} onChange={(event) => { setPlan(relevant.find((item) => item.id === event.target.value) ?? null); setFib(null); setRange(null); }} className="w-full bg-white/5 p-2 text-xs">
            <option value="">Select plan</option>
            {relevant.map((item) => <option key={item.id} value={item.id}>{item.lifecycle_status} · {item.setup_type}</option>)}
          </select>
        </Panel>
        <PlanEditor plan={plan} onSaved={(saved) => { setPlan(saved); void refreshPlans(); }} />
        <FibRangeEditor plan={plan} onChange={(nextFib, nextRange) => { setFib(nextFib); setRange(nextRange); }} />
        <Panel title="Evidence / permission" kicker="SERVER-OWNED">
          {coach ? <div className="space-y-2 text-xs">
            <div>CONTEXT: 4H {coach.market_context.regime_4h ?? "—"} · 1H {coach.market_context.context_1h ?? "—"}</div>
            <div>Data: {coach.market_context.status} · Playbook: {coach.market_context.playbook_state ?? "UNAVAILABLE"}</div>
            <div>LOCATION: {coach.market_context.location} · {coach.execution.condition_evidence.includes("LOCATION_VALID") ? "YES" : "NO"}</div>
            <div>REACTION: {coach.market_context.reaction ?? "MISSING"} · {coach.market_context.reaction_status}</div>
            <div>TRIGGER: unavailable</div>
            <div>RISK: {coach.risk.policy_status} · {coach.risk.correlation_group} · ${coach.risk.known_group_risk_usdt}</div>
            <div>PERMISSION: {permission}</div>
            {coach.execution.next_conditions.map((item) => <div key={item}>Next: {item}</div>)}
            {coach.execution.blocking_reasons.map((item) => <div key={item} className="text-amber-300">Block: {item}</div>)}
            {coach.execution.warnings.map((item) => <div key={item} className="text-rose-300">Warning: {item}</div>)}
          </div> : <p className="text-xs text-slate-500">No active position coach is available.</p>}
        </Panel>
        <Panel title="Sizing" kicker="BACKEND CALCULATION">
          {plan && <button onClick={() => void size()} className="text-xs text-cyan-300">Refresh sizing</button>}
          {sizing && <div className="mt-2 space-y-1 text-xs">
            Risk ${sizing.risk_budget_usdt} · used ${sizing.group_risk_used_usdt} · remaining ${sizing.group_risk_remaining_usdt}<br />
            Max qty {sizing.maximum_quantity} · notional ${sizing.maximum_notional}<br />
            Minimum leverage for margin fit: {sizing.minimum_leverage_for_margin_fit ?? "—"}<br />
            Estimated margin: {sizing.estimated_margin_usdt ?? "—"} · LIQUIDATION DATA {sizing.liquidation_status}
            {sizing.stages.map((stage) => <div key={stage.state} className="mt-2 border-t border-white/10 pt-2">
              {stage.state} · {stage.status} · allocation {stage.allocation ?? "—"}<br />
              risk ${stage.stage_risk_usdt ?? "—"} · qty {stage.quantity ?? "—"} · notional ${stage.notional ?? "—"}<br />
              {stage.reasons.join("; ")}
            </div>)}
          </div>}
        </Panel>
      </aside>
    </div>
  </>;
}
