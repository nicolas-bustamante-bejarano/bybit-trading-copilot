"use client";

import { useCallback, useMemo, useState } from "react";
import { ChevronDown, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api/client";
import { usePolling } from "@/lib/api/use-polling";
import type { ScannerSetup, ScannerSetupState, ScannerStatus, ScannerTransition, ScannerWatchlistInput, ScannerWatchlistItem } from "@/lib/api/types";
import { EmptyState, ErrorState, PageHeader, Panel } from "./ui";
import { StatusBadge } from "./status-badge";

const PLAYBOOKS = [
  ["TREND_PULLBACK", "Trend Pullback"], ["RANGE", "Range Extreme / Deviation-Reclaim"], ["MACRO_BREAKOUT", "Macro Breakout / Reclaim"],
] as const;
const SETUP_TYPES = ["TREND_PULLBACK_LONG", "TREND_PULLBACK_SHORT", "RANGE_LONG", "RANGE_SHORT", "MACRO_BREAKOUT_LONG", "MACRO_BREAKOUT_SHORT"] as const;
const STATUSES: ScannerStatus[] = ["TRIGGER_ARMED", "REACTION_DEVELOPING", "RETEST_PENDING", "BREAKOUT_ACCEPTED", "AT_LOCATION", "ACCEPTANCE_PENDING", "BREAKOUT_ATTEMPT", "APPROACHING_LOCATION", "APPROACHING_BREAKOUT", "WATCH", "IGNORE"];
const PRIORITY = new Map(STATUSES.map((status, index) => [status, index]));
const FAMILY_VALUES = new Set<string>(PLAYBOOKS.map(([value]) => value));
const inputClass = "rounded border border-white/10 bg-black/20 px-2.5 py-2 text-xs text-slate-200 outline-none focus:border-cyan-400/40";

function text(value: unknown): string | null { return typeof value === "string" && value.trim() ? value : null; }
function numberValue(value: unknown): number | null { const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN; return Number.isFinite(parsed) ? parsed : null; }
function stringList(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
function record(value: unknown): Record<string, unknown> | null { return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null; }
function label(value: string): string { return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase()); }
function compactSetup(value: string): string { return label(value).replace("Trend Pullback ", "Trend · ").replace("Macro Breakout ", "Macro · ").replace("Range ", "Range · "); }
function formatNumber(value: unknown, suffix = ""): string { const parsed = numberValue(value); return parsed === null ? "—" : `${parsed.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`; }
function age(value: string | null | undefined): string { if (!value) return "—"; const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000)); if (seconds < 60) return `${seconds}s ago`; if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`; if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`; return `${Math.floor(seconds / 86400)}d ago`; }
function stateList(state: ScannerSetupState, key: "blocking_reasons" | "next_conditions"): string[] { return stringList(state[key]); }
function distance(setup: ScannerSetup): number | null { return numberValue(setup.state.distance_bps ?? record(setup.state.location)?.distance_bps); }
function locationSummary(setup: ScannerSetup): string {
  const structure = record(setup.state.structure); const location = record(setup.state.location);
  return text(structure?.label) ?? text(structure?.type) ?? text(structure?.structure_type) ?? text(location?.label) ?? text(location?.status) ?? text(location?.zone) ?? "—";
}
function statusTone(status: ScannerStatus): string {
  if (status === "TRIGGER_ARMED") return "border-emerald-400/45 bg-emerald-400/10 text-emerald-200";
  if (["REACTION_DEVELOPING", "BREAKOUT_ACCEPTED", "RETEST_PENDING"].includes(status)) return "border-cyan-400/35 bg-cyan-400/10 text-cyan-200";
  if (["AT_LOCATION", "BREAKOUT_ATTEMPT", "ACCEPTANCE_PENDING"].includes(status)) return "border-amber-400/35 bg-amber-400/10 text-amber-200";
  if (status.startsWith("APPROACHING_")) return "border-cyan-400/20 bg-cyan-400/5 text-cyan-300/75";
  return status === "IGNORE" ? "border-white/5 bg-white/[.02] text-slate-600" : "border-white/10 bg-white/[.03] text-slate-400";
}

export function ScannerClient() {
  const statusLoader = useCallback(() => api.getScannerStatus(), []);
  const setupsLoader = useCallback(() => api.getScannerSetups({ limit: 500 }), []);
  const watchlistLoader = useCallback(() => api.getScannerWatchlist(), []);
  const monitor = usePolling(statusLoader, 12000);
  const setups = usePolling(setupsLoader, 15000);
  const watchlist = usePolling(watchlistLoader, 30000);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [symbolFilter, setSymbolFilter] = useState("ALL");
  const [setupFilter, setSetupFilter] = useState("ALL");
  const [statusFilter, setStatusFilter] = useState("ALL");

  const ordered = useMemo(() => [...(setups.data ?? [])].filter((item) => symbolFilter === "ALL" || item.symbol === symbolFilter).filter((item) => setupFilter === "ALL" || item.setup_type === setupFilter).filter((item) => statusFilter === "ALL" || item.status === statusFilter).sort((a, b) => (PRIORITY.get(a.status) ?? 99) - (PRIORITY.get(b.status) ?? 99) || (distance(a) ?? Infinity) - (distance(b) ?? Infinity) || a.symbol.localeCompare(b.symbol) || a.setup_type.localeCompare(b.setup_type)), [setups.data, symbolFilter, setupFilter, statusFilter]);
  const selected = (setups.data ?? []).find((item) => item.id === selectedId) ?? null;
  const symbols = [...new Set((setups.data ?? []).map((item) => item.symbol))].sort();
  const monitorState = !monitor.data?.enabled ? "DISABLED" : monitor.data.last_error ? "DEGRADED / ERRORS" : monitor.data.running ? "RUNNING" : "IDLE";

  return <>
    <PageHeader eyebrow="SETUP DISCOVERY" title="Scanner" detail="Multi-symbol regime, structure, reaction, and breakout monitoring. Execution remains manual." action={<div className="flex items-center gap-3"><StatusBadge value={monitor.data?.last_error ? "WARNING" : monitor.data?.running ? "CONFIRMED" : "INDETERMINATE"}>{monitorState}</StatusBadge><button onClick={() => { void monitor.refresh(); void setups.refresh(); void watchlist.refresh(); }} className="flex items-center gap-2 rounded border border-white/10 px-3 py-2 text-xs text-slate-400"><RefreshCw size={13}/>Refresh</button></div>}/>
    {(monitor.error || setups.error || watchlist.error) && <div className="mb-4"><ErrorState message={[monitor.error, setups.error, watchlist.error].filter(Boolean).join(" · ")}/></div>}
    <MonitorStrip status={monitor.data}/>
    {!monitor.loading && monitor.data && !monitor.data.enabled && <div className="mb-5 rounded border border-amber-400/15 bg-amber-400/5 p-3 text-xs text-amber-200/75">Scanner monitoring is disabled. Persisted observations remain available below.</div>}
    <div className="grid gap-5 xl:grid-cols-[1.05fr_.95fr]">
      <Panel title="Current candidates" kicker={`${ordered.length} VISIBLE`}>
        <div className="mb-3 grid grid-cols-3 gap-2"><Filter value={symbolFilter} onChange={setSymbolFilter} label="Symbol" options={symbols}/><Filter value={setupFilter} onChange={setSetupFilter} label="Setup" options={[...SETUP_TYPES]}/><Filter value={statusFilter} onChange={setStatusFilter} label="Status" options={STATUSES}/></div>
        {setups.data?.length ? ordered.length ? <CandidateList setups={ordered} selectedId={selectedId} onSelect={setSelectedId}/> : <EmptyState title="No candidates match these filters" detail="Adjust the symbol, setup, or status filter."/> : <EmptyState title={watchlist.data?.length ? "Scanner has not persisted a candidate observation yet." : "Add a market to begin scanning."} detail={watchlist.data?.length ? "Observations will appear after a successful scan cycle." : "Use the watchlist configuration below."}/>}
      </Panel>
      <div className="space-y-5"><CandidateDetail setup={selected}/><TransitionHistory setup={selected}/></div>
    </div>
    <div className="mt-5"><WatchlistPanel items={watchlist.data ?? []} onRefresh={watchlist.refresh}/></div>
  </>;
}

function MonitorStrip({ status }: { status: ReturnType<typeof usePolling<import("@/lib/api/types").ScannerMonitorStatus>>["data"] }) {
  const facts = [["LAST SUCCESS", status?.last_success_at ? age(status.last_success_at) : "Never"], ["CYCLE", status?.last_cycle_duration_ms == null ? "—" : `${Math.round(status.last_cycle_duration_ms)} ms`], ["WATCHLIST", status?.watchlist_count ?? "—"], ["SETUPS", status?.setup_count ?? "—"], ["EVALUATED", status?.evaluated_symbols.length ?? "—"], ["FAILED", status?.failed_symbols.length ?? "—"]];
  return <div className="mb-5 grid grid-cols-2 gap-px overflow-hidden rounded border border-white/8 bg-white/8 md:grid-cols-6">{facts.map(([name, value]) => <div key={name} className="bg-[#0d121a] p-3"><div className="mono-label">{name}</div><div className="mt-1 font-mono text-sm text-slate-300">{value}</div></div>)}</div>;
}

function Filter({ label: name, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: readonly string[] }) { return <label><span className="mono-label mb-1 block">{name}</span><select value={value} onChange={(event) => onChange(event.target.value)} className={`${inputClass} w-full`}><option value="ALL">All</option>{options.map((option) => <option key={option} value={option}>{label(option)}</option>)}</select></label>; }

function CandidateList({ setups, selectedId, onSelect }: { setups: ScannerSetup[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return <div className="overflow-hidden rounded border border-white/8"><div className="hidden grid-cols-[.75fr_1.3fr_.45fr_1.1fr_.65fr_.7fr_1fr_.6fr_.7fr] gap-2 border-b border-white/8 bg-black/15 px-3 py-2 font-mono text-[8px] tracking-wider text-slate-600 md:grid"><span>SYMBOL</span><span>SETUP</span><span>SIDE</span><span>STATUS</span><span>DATA</span><span>PRICE</span><span>LOCATION / STRUCTURE</span><span>DISTANCE</span><span>UPDATED</span></div>{setups.map((setup) => <button key={setup.id} onClick={() => onSelect(setup.id)} className={`grid w-full gap-2 border-b border-white/5 p-3 text-left last:border-0 md:grid-cols-[.75fr_1.3fr_.45fr_1.1fr_.65fr_.7fr_1fr_.6fr_.7fr] md:items-center ${selectedId === setup.id ? "bg-cyan-400/[.07]" : "hover:bg-white/[.025]"}`}><div className="font-semibold text-white">{setup.symbol}</div><div className="text-xs text-slate-400">{compactSetup(setup.setup_type)}</div><div className="font-mono text-[10px] text-slate-500">{text(setup.state.side) ?? (setup.setup_type.endsWith("LONG") ? "LONG" : "SHORT")}</div><span className={`w-fit rounded-sm border px-1.5 py-1 font-mono text-[8px] font-semibold ${statusTone(setup.status)}`}>{setup.status}</span><StatusBadge value={text(setup.state.data_status) ?? "INDETERMINATE"}/><div className="font-mono text-xs text-slate-300">{formatNumber(setup.state.price)}</div><div className="truncate text-xs text-slate-500" title={locationSummary(setup)}>{locationSummary(setup)}</div><div className="font-mono text-xs text-slate-400">{formatNumber(distance(setup), " bps")}</div><div className="text-[10px] text-slate-600">{age(setup.last_evaluated_at ?? setup.updated_at)}</div></button>)}</div>;
}

function CandidateDetail({ setup }: { setup: ScannerSetup | null }) {
  if (!setup) return <Panel title="Candidate evidence" kicker="PERSISTED STATE"><EmptyState title="No candidate selected" detail="Select a candidate to inspect its decision evidence."/></Panel>;
  const macro = setup.setup_type.startsWith("MACRO_"); const state = setup.state; const structure = record(state.structure); const blockers = stateList(state, "blocking_reasons"); const next = stateList(state, "next_conditions");
  const context = macro ? `${compactSetup(setup.setup_type)} · ${text(state.side) ?? "—"}` : evidenceSummary(state.context);
  const location = macro ? [text(structure?.label) ?? text(structure?.type) ?? "Structure", `level ${formatNumber(structure?.breakout_level)}`, `distance ${formatNumber(state.distance_bps, " bps")}`].join(" · ") : evidenceSummary(state.location);
  const reaction = macro ? (setup.status === "RETEST_PENDING" || setup.status === "TRIGGER_ARMED" ? "Retest evidence active" : "Not applicable before retest") : evidenceSummary(state.reaction);
  const trigger = setup.status === "TRIGGER_ARMED" ? "Lower-timeframe trigger evaluation armed" : next[0] ? humanReason(next[0]) : label(setup.status);
  return <Panel title={`${setup.symbol} · ${compactSetup(setup.setup_type)}`} kicker={`VERSION ${setup.version}`}><div className="space-y-4"><div className="grid gap-2 sm:grid-cols-5">{[["CONTEXT", context], ["LOCATION", location], ["REACTION", reaction], ["TRIGGER", trigger], ["DATA", text(state.data_status) ?? "Indeterminate"]].map(([name, value]) => <div key={name} className="rounded border border-white/7 bg-black/15 p-2.5"><div className="mono-label">{name}</div><div className="mt-1 text-xs leading-5 text-slate-400">{value}</div></div>)}</div>{macro && <MacroEvidence state={state} structure={structure}/>}<EvidenceList title="BLOCKERS" values={blockers}/><EvidenceList title="NEXT" values={next}/><div className="flex items-center justify-between border-t border-white/7 pt-3 text-[10px] text-slate-600"><span>Evaluated {age(setup.last_evaluated_at)}</span><span className="font-mono text-cyan-300/60">MANUAL EXECUTION ONLY</span></div></div></Panel>;
}

function evidenceSummary(value: unknown): string { const item = record(value); if (!item || !Object.keys(item).length) return "Missing"; const status = text(item.status); const detail = text(item.label) ?? text(item.reason) ?? text(item.zone); return [status ? label(status) : "Evidence recorded", detail].filter(Boolean).join(" · "); }
function humanReason(value: string): string { return label(value); }
function EvidenceList({ title, values }: { title: string; values: string[] }) { return <div><div className="mono-label mb-2">{title}</div>{values.length ? <div className="flex flex-wrap gap-2">{values.map((value) => <span key={value} title={value} className="rounded border border-white/8 bg-white/[.025] px-2 py-1.5 text-xs text-slate-300">{humanReason(value)} <span className="ml-1 font-mono text-[8px] text-slate-600">{value}</span></span>)}</div> : <div className="text-xs text-slate-600">None recorded</div>}</div>; }
function MacroEvidence({ state, structure }: { state: ScannerSetupState; structure: Record<string, unknown> | null }) { const completed = numberValue(state.qualifying_close_count ?? state.accepted_close_count); const required = numberValue(state.required_acceptance_bars ?? state.acceptance_bars); return <div className="grid grid-cols-2 gap-3 rounded border border-cyan-400/10 bg-cyan-400/[.025] p-3 sm:grid-cols-4"><Fact name="BREAKOUT LEVEL" value={formatNumber(structure?.breakout_level)}/><Fact name="DISTANCE" value={formatNumber(state.distance_bps, " bps")}/><Fact name="ACCEPTANCE" value={completed === null || required === null ? "—" : `${completed} / ${required} completed 1H closes`}/><Fact name="ACCEPTED REFERENCE" value={state.accepted_at ? `${formatNumber(state.accepted_breakout_level)} · ${age(state.accepted_at)}` : "—"}/></div>; }
function Fact({ name, value }: { name: string; value: string }) { return <div><div className="mono-label">{name}</div><div className="mt-1 font-mono text-xs text-slate-300">{value}</div></div>; }

function TransitionHistory({ setup }: { setup: ScannerSetup | null }) {
  const loader = useCallback(() => setup ? api.getScannerTransitions({ watched_setup_id: setup.id, limit: 100 }) : Promise.resolve([]), [setup]);
  const transitions = usePolling(loader, 20000);
  if (!setup) return <Panel title="Transition history"><EmptyState title="No transition history selected" detail="Select a candidate to audit its lifecycle."/></Panel>;
  return <Panel title="Transition history" kicker="NEWEST FIRST">{transitions.error && <div className="mb-3"><ErrorState message={transitions.error}/></div>}{transitions.data?.length ? <div className="space-y-2">{transitions.data.map((item) => <TransitionRow key={item.id} transition={item}/>)}</div> : <EmptyState title="No transitions recorded" detail="The candidate has not changed lifecycle state yet."/>}</Panel>;
}
function TransitionRow({ transition }: { transition: ScannerTransition }) { return <details className="group rounded border border-white/7 bg-black/10"><summary className="flex cursor-pointer list-none items-center gap-2 p-3"><ChevronDown size={13} className="text-slate-600 transition group-open:rotate-180"/><span className="text-xs text-slate-500">{new Date(transition.timestamp).toLocaleString()}</span><span className="ml-auto font-mono text-[10px] text-slate-400">{transition.from_status ?? "NEW"} → {transition.to_status}</span><span className="font-mono text-[9px] text-slate-600">v{transition.version}</span></summary><div className="grid gap-2 border-t border-white/7 p-3 sm:grid-cols-2"><Snapshot title="BEFORE" value={transition.state_before}/><Snapshot title="AFTER" value={transition.state_after}/></div></details>; }
function Snapshot({ title, value }: { title: string; value: ScannerSetupState }) { return <div><div className="mono-label mb-1">{title} EVIDENCE</div><pre className="max-h-52 overflow-auto rounded bg-black/25 p-2 font-mono text-[9px] leading-4 text-slate-500">{JSON.stringify(value, null, 2)}</pre></div>; }

function WatchlistPanel({ items, onRefresh }: { items: ScannerWatchlistItem[]; onRefresh: () => Promise<void> }) {
  const [form, setForm] = useState<ScannerWatchlistInput>({ symbol: "", enabled: true, enabled_playbooks: PLAYBOOKS.map(([value]) => value), approach_tolerance_bps: 50, retest_tolerance_bps: 25, acceptance_bars: 2 });
  const [pending, setPending] = useState<string | null>(null); const [error, setError] = useState<string | null>(null);
  async function create() { setPending("create"); setError(null); try { await api.createScannerWatchlistItem({ ...form, symbol: form.symbol.trim().toUpperCase() }); setForm((current) => ({ ...current, symbol: "" })); await onRefresh(); } catch (value) { setError(value instanceof Error ? value.message : "Unable to add symbol"); } finally { setPending(null); } }
  return <Panel title="Watchlist configuration" kicker={`${items.length} MARKETS`}><div className="space-y-4">{error && <ErrorState message={error}/>}<form onSubmit={(event) => { event.preventDefault(); void create(); }} className="grid gap-3 rounded border border-white/7 bg-black/10 p-3 lg:grid-cols-[.8fr_2fr_.65fr_.65fr_.65fr_auto]"><label><span className="mono-label mb-1 block">SYMBOL</span><input required value={form.symbol} onChange={(event) => setForm({ ...form, symbol: event.target.value.toUpperCase() })} placeholder="BTCUSDT" className={`${inputClass} w-full uppercase`}/></label><PlaybookChecks values={form.enabled_playbooks} onChange={(enabled_playbooks) => setForm({ ...form, enabled_playbooks })}/><NumberInput name="APPROACH BPS" value={form.approach_tolerance_bps} min={0} onChange={(approach_tolerance_bps) => setForm({ ...form, approach_tolerance_bps })}/><NumberInput name="RETEST BPS" value={form.retest_tolerance_bps} min={0} onChange={(retest_tolerance_bps) => setForm({ ...form, retest_tolerance_bps })}/><NumberInput name="ACCEPTANCE" value={form.acceptance_bars} min={1} onChange={(acceptance_bars) => setForm({ ...form, acceptance_bars })}/><button disabled={pending === "create"} className="self-end rounded border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-xs text-cyan-200 disabled:opacity-50">{pending === "create" ? "Adding…" : "Add symbol"}</button></form>{items.length ? <div className="space-y-2">{items.map((item) => <WatchlistRow key={`${item.id}:${item.updated_at}`} item={item} onRefresh={onRefresh}/>)}</div> : <EmptyState title="Add a market to begin scanning." detail="Submitting this form creates the first watchlist configuration."/>}</div></Panel>;
}
function WatchlistRow({ item, onRefresh }: { item: ScannerWatchlistItem; onRefresh: () => Promise<void> }) {
  const [draft, setDraft] = useState(item); const [pending, setPending] = useState(false); const [error, setError] = useState<string | null>(null); const exact = draft.enabled_playbooks.filter((value) => !FAMILY_VALUES.has(value));
  async function save() { setPending(true); setError(null); try { await api.patchScannerWatchlistItem(item.symbol, { enabled: draft.enabled, enabled_playbooks: draft.enabled_playbooks, approach_tolerance_bps: draft.approach_tolerance_bps, retest_tolerance_bps: draft.retest_tolerance_bps, acceptance_bars: draft.acceptance_bars }); await onRefresh(); } catch (value) { setError(value instanceof Error ? value.message : "Unable to save configuration"); } finally { setPending(false); } }
  async function remove() { if (!window.confirm("Removing this symbol from the watchlist stops future scanning. Existing setup and transition history is preserved.")) return; setPending(true); setError(null); try { await api.deleteScannerWatchlistItem(item.symbol); await onRefresh(); } catch (value) { setError(value instanceof Error ? value.message : "Unable to delete symbol"); } finally { setPending(false); } }
  return <div className="rounded border border-white/7 bg-black/10 p-3">{error && <div className="mb-3 text-xs text-red-300">{error}</div>}<div className="grid gap-3 lg:grid-cols-[.6fr_2fr_.65fr_.65fr_.65fr_auto]"><label className="flex items-center gap-2 self-center"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}/><span className="font-semibold text-white">{draft.symbol}</span><span className="font-mono text-[8px] text-slate-600">{draft.enabled ? "ENABLED" : "DISABLED"}</span></label><PlaybookChecks values={draft.enabled_playbooks} onChange={(enabled_playbooks) => setDraft({ ...draft, enabled_playbooks: [...enabled_playbooks, ...exact.filter((value) => !enabled_playbooks.includes(value))] })}/><NumberInput name="APPROACH BPS" value={draft.approach_tolerance_bps} min={0} onChange={(approach_tolerance_bps) => setDraft({ ...draft, approach_tolerance_bps })}/><NumberInput name="RETEST BPS" value={draft.retest_tolerance_bps} min={0} onChange={(retest_tolerance_bps) => setDraft({ ...draft, retest_tolerance_bps })}/><NumberInput name="ACCEPTANCE" value={draft.acceptance_bars} min={1} onChange={(acceptance_bars) => setDraft({ ...draft, acceptance_bars })}/><div className="flex items-end gap-2"><button disabled={pending} onClick={() => void save()} className="rounded border border-cyan-400/25 px-3 py-2 text-xs text-cyan-200 disabled:opacity-50">{pending ? "Saving…" : "Save"}</button><button disabled={pending} onClick={() => void remove()} title="Remove from watchlist" className="rounded border border-red-400/15 p-2 text-red-300/70 disabled:opacity-50"><Trash2 size={14}/></button></div></div>{exact.length > 0 && <div className="mt-2 border-t border-white/5 pt-2 font-mono text-[9px] text-slate-600">ADVANCED EXACT SETUPS · {exact.join(" · ")}</div>}</div>;
}
function PlaybookChecks({ values, onChange }: { values: string[]; onChange: (values: string[]) => void }) { return <fieldset><legend className="mono-label mb-1">PLAYBOOKS</legend><div className="flex flex-wrap gap-x-3 gap-y-1">{PLAYBOOKS.map(([value, name]) => <label key={value} className="flex items-center gap-1.5 text-[10px] text-slate-400"><input type="checkbox" checked={values.includes(value)} onChange={(event) => onChange(event.target.checked ? [...values, value] : values.filter((item) => item !== value))}/>{name}</label>)}</div></fieldset>; }
function NumberInput({ name, value, min, onChange }: { name: string; value: number; min: number; onChange: (value: number) => void }) { return <label><span className="mono-label mb-1 block">{name}</span><input type="number" min={min} required value={value} onChange={(event) => onChange(Number(event.target.value))} className={`${inputClass} w-full`}/></label>; }
