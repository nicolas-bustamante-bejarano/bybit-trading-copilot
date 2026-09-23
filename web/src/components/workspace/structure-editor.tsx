"use client";

import { useState } from "react";
import { api } from "@/lib/api/client";
import type { ChartStructure } from "@/lib/api/types";
import { Panel } from "../ui";

export function StructureEditor({ symbol, timeframe, rows, refresh }: { symbol: string; timeframe: string; rows: ChartStructure[]; refresh: () => Promise<void> }) {
  const [type, setType] = useState<ChartStructure["structure_type"]>("HORIZONTAL_ZONE");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const set = (key: string, value: string) => setForm(current => ({ ...current, [key]: value }));
  const reset = () => { setEditingId(null); setForm({}); };
  const save = async () => {
    const body: Record<string, unknown> = { symbol, timeframe: form.timeframe || timeframe, structure_type: type, label: form.label || null, active: form.active !== "false" };
    if (type === "HORIZONTAL_ZONE") Object.assign(body, { lower_price: form.lower_price, upper_price: form.upper_price });
    else Object.assign(body, { anchor_one_time: Number(form.anchor_one_time), anchor_one_price: form.anchor_one_price, anchor_two_time: Number(form.anchor_two_time), anchor_two_price: form.anchor_two_price });
    if (editingId) await api.patchChartStructure(editingId, body); else await api.createChartStructure(body);
    reset(); await refresh();
  };
  const edit = (row: ChartStructure) => { setType(row.structure_type); setEditingId(row.id); setForm(Object.fromEntries(Object.entries(row).map(([key, value]) => [key, String(value ?? "")]))) };
  const keys = type === "HORIZONTAL_ZONE" ? ["label", "timeframe", "lower_price", "upper_price"] : ["label", "timeframe", "anchor_one_time", "anchor_one_price", "anchor_two_time", "anchor_two_price"];
  return <Panel title="Manual structures" kicker="AUTHORITATIVE"><div className="flex gap-2 text-xs"><button onClick={() => { reset(); setType("HORIZONTAL_ZONE") }}>Zone</button><button onClick={() => { reset(); setType("TRENDLINE") }}>Trendline</button></div><div className="mt-2 grid grid-cols-2 gap-2">{keys.map(key => <input key={key} value={form[key] ?? ""} onChange={event => set(key, event.target.value)} placeholder={key.replaceAll("_", " ")} className="bg-white/5 p-2 text-xs" />)}</div><button onClick={() => void save()} className="mt-2 text-xs text-cyan-300">{editingId ? "Save changes" : `Create ${type === "HORIZONTAL_ZONE" ? "zone" : "trendline"}`}</button>{editingId && <button onClick={reset} className="ml-3 text-xs text-slate-400">Cancel edit</button>}{rows.map(row => <div key={row.id} className="mt-2 flex justify-between text-xs"><span>{row.label ?? row.structure_type} · {row.active ? "active" : "inactive"}</span><span><button onClick={() => edit(row)} className="text-cyan-300">edit</button><button onClick={() => void api.patchChartStructure(row.id, { active: !row.active }).then(refresh)} className="ml-2 text-cyan-300">toggle</button><button onClick={() => void api.deleteChartStructure(row.id).then(refresh)} className="ml-2 text-rose-300">delete</button></span></div>)}</Panel>
}
