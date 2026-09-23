"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { TradePlan } from "@/lib/api/types";
import { Panel } from "../ui";

type Props = { plan: TradePlan | null; onSaved: (plan: TradePlan) => void };

export function PlanEditor({ plan, onSaved }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    setValues(plan ? {
      lifecycle_status: plan.lifecycle_status, side: plan.side, setup_type: plan.setup_type,
      thesis: plan.thesis, notes: plan.notes ?? "", hard_invalidation: plan.hard_invalidation,
      thesis_warning: plan.thesis_warning ?? "", max_risk_percent: plan.max_risk_percent,
      max_risk_value: plan.max_risk_value ?? "", correlation_group: plan.correlation_group ?? "",
      entry_probe_plan: JSON.stringify(plan.entry_probe_plan, null, 2),
      add_conditions: JSON.stringify(plan.add_conditions, null, 2),
      target_ladder: JSON.stringify(plan.target_ladder, null, 2),
    } : {});
    setMessage(null);
  }, [plan]);
  const set = (key: string, value: string) => setValues(current => ({ ...current, [key]: value }));
  const save = async () => {
    if (!plan) return;
    try {
      const body = { ...values, thesis_warning: values.thesis_warning || null, max_risk_value: values.max_risk_value || null, correlation_group: values.correlation_group || null, entry_probe_plan: JSON.parse(values.entry_probe_plan), add_conditions: JSON.parse(values.add_conditions), target_ladder: JSON.parse(values.target_ladder) };
      onSaved(await api.patchPlan(plan.id, body)); setMessage("Saved");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not save plan"); }
  };
  if (!plan) return <Panel title="Trade-plan editor" kicker="APPLICATION-SIDE INTENT"><p className="text-xs text-slate-500">Select a plan to edit its persisted intent.</p></Panel>;
  return <Panel title="Trade-plan editor" kicker={plan.lifecycle_status}><div className="grid grid-cols-2 gap-2">{["lifecycle_status", "side", "setup_type", "hard_invalidation", "thesis_warning", "max_risk_percent", "max_risk_value", "correlation_group"].map(key => <input key={key} value={values[key] ?? ""} onChange={event => set(key, event.target.value)} placeholder={key.replaceAll("_", " ")} className="bg-white/5 p-2 text-xs" />)}</div><textarea value={values.thesis ?? ""} onChange={event => set("thesis", event.target.value)} placeholder="thesis" className="mt-2 w-full bg-white/5 p-2 text-xs" />{["entry_probe_plan", "add_conditions", "target_ladder", "notes"].map(key => <textarea key={key} value={values[key] ?? ""} onChange={event => set(key, event.target.value)} placeholder={key.replaceAll("_", " ")} className="mt-2 w-full bg-white/5 p-2 font-mono text-xs" />)}<button onClick={() => void save()} className="mt-2 rounded bg-cyan-400/15 px-3 py-2 text-xs text-cyan-200">Save plan</button>{message && <span className="ml-2 text-xs text-amber-300">{message}</span>}</Panel>;
}
