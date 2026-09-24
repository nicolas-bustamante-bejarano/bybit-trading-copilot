"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, ColorType, createChart, createSeriesMarkers, LineSeries } from "lightweight-charts";
import type { IChartApi, IPriceLine, ISeriesApi, ISeriesMarkersPluginApi, MouseEventParams, Time } from "lightweight-charts";
import { api } from "@/lib/api/client";
import { usePolling } from "@/lib/api/use-polling";
import type { ChartStructure, FibDefinition, RangeDefinition, ScannerSetup, TriggerCurrent } from "@/lib/api/types";
import { buildStructureDraft, draftInstruction, requiredPickCount, type ChartPick, type StructureDraftKind } from "@/lib/scanner-authoring";
import { extractScannerOverlays, hasStructuralBlocker, type ScannerTimeframe } from "@/lib/scanner-visuals";
import { EmptyState, Panel } from "./ui";

const TIMEFRAMES: { value: ScannerTimeframe; label: string }[] = [{ value: "4h", label: "4H" }, { value: "1h", label: "1H" }, { value: "15m", label: "15m" }, { value: "5m", label: "5m" }];

export function ScannerEvidenceChart({ setup, trigger, timeframe, onTimeframeChange, onEvidenceRefresh }: { setup: ScannerSetup | null; trigger: TriggerCurrent | null; timeframe: ScannerTimeframe; onTimeframeChange: (timeframe: ScannerTimeframe) => void; onEvidenceRefresh: () => Promise<void> }) {
  const symbol = setup?.symbol ?? null; const setupId = setup?.id ?? null;
  const planId = setup?.trade_plan_id ?? null;
  const chartLoader = useCallback(() => symbol ? api.getChart(symbol, timeframe) : Promise.resolve(null), [symbol, timeframe]);
  const transitionLoader = useCallback(() => setupId ? api.getScannerTransitions({ watched_setup_id: setupId, limit: 100 }) : Promise.resolve([]), [setupId]);
  const chart = usePolling(chartLoader, 15000);
  const transitions = usePolling(transitionLoader, 15000);
  const structures = usePolling(useCallback(() => symbol ? api.getChartStructures(symbol) : Promise.resolve([]), [symbol]), 15000);
  const definitions = usePolling(useCallback(async () => {
    if (!planId) return { fib: null, range: null };
    const [fibs, ranges] = await Promise.all([api.getFibs(planId), api.getRanges(planId)]);
    return { fib: fibs[0] ?? null, range: ranges[0] ?? null };
  }, [planId]), 15000);
  const overlays = useMemo(() => setup ? extractScannerOverlays(setup, trigger, transitions.data ?? []) : null, [setup, trigger, transitions.data]);
  const [draftKind, setDraftKind] = useState<StructureDraftKind | null>(null);
  const [draftSetupId, setDraftSetupId] = useState<string | null>(null);
  const [picks, setPicks] = useState<ChartPick[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const sourceId = typeof setup?.state.structure?.structure_id === "string" ? setup.state.structure.structure_id : null;
  const selectedMacroStructure = (structures.data ?? []).find((row) => row.id === sourceId) ?? null;
  const activeDraftKind = draftSetupId === setupId ? draftKind : null;
  const activePicks = draftSetupId === setupId ? picks : [];
  const activeMessage = draftSetupId === setupId ? message : null;

  const start = (kind: StructureDraftKind, initial: ChartPick[] = [], id: string | null = null) => { setDraftSetupId(setupId); setDraftKind(kind); setPicks(initial); setEditingId(id); setMessage(null); };
  const cancel = () => { setDraftSetupId(null); setDraftKind(null); setPicks([]); setEditingId(null); setMessage(null); };
  const pick = (point: ChartPick) => { if (!activeDraftKind) return; setPicks((current) => current.length >= requiredPickCount(activeDraftKind) ? [point] : [...current, point]); };
  const refreshCanonical = async () => { await Promise.all([structures.refresh(), definitions.refresh(), onEvidenceRefresh()]); };
  const save = async () => {
    if (!setup || !activeDraftKind || draftSetupId !== setup.id) return;
    setPending(true); setMessage(null);
    try {
      const payload = buildStructureDraft(setup, activeDraftKind, activePicks, timeframe);
      if (payload.target === "CHART_STRUCTURE") {
        if (editingId) {
          const patch = { ...payload.body };
          delete patch.symbol;
          delete patch.structure_type;
          await api.patchChartStructure(editingId, patch);
        } else await api.createChartStructure(payload.body);
      } else if (payload.target === "RANGE") await api.putRange(payload.planId, payload.body);
      else await api.putFib(payload.planId, payload.body);
      let reevaluationError: string | null = null;
      try { await api.reevaluateScannerSymbol(setup.symbol); }
      catch (error) { reevaluationError = error instanceof Error ? error.message : "reevaluation unavailable"; }
      await refreshCanonical();
      setDraftKind(null); setPicks([]); setEditingId(null);
      setMessage(reevaluationError ? `Structure saved. Scanner reevaluation failed: ${reevaluationError}` : "Structure saved and canonical evidence refreshed.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Structure could not be saved"); }
    finally { setPending(false); }
  };
  const remove = async () => {
    if (!setup || !window.confirm("Delete this canonical structure? Scanner evidence will be re-evaluated.")) return;
    setPending(true); setMessage(null);
    try {
      if (setup.setup_type.startsWith("MACRO_BREAKOUT")) {
        if (!selectedMacroStructure) throw new Error("No selected canonical macro structure to delete.");
        await api.deleteChartStructure(selectedMacroStructure.id);
      } else if (setup.setup_type.startsWith("RANGE")) {
        if (!setup.trade_plan_id || !definitions.data?.range) throw new Error("No canonical range definition to delete.");
        await api.deleteRange(setup.trade_plan_id);
      } else {
        if (!setup.trade_plan_id || !definitions.data?.fib) throw new Error("No canonical Fib definition to delete.");
        await api.deleteFib(setup.trade_plan_id);
      }
      let reevaluationError: string | null = null;
      try { await api.reevaluateScannerSymbol(setup.symbol); }
      catch (error) { reevaluationError = error instanceof Error ? error.message : "reevaluation unavailable"; }
      await refreshCanonical();
      setDraftKind(null); setPicks([]); setEditingId(null);
      setMessage(reevaluationError ? `Structure deleted. Scanner reevaluation failed: ${reevaluationError}` : "Structure deleted and canonical evidence refreshed.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Structure could not be deleted"); }
    finally { setPending(false); }
  };

  return <Panel title={setup ? `${setup.symbol} · setup evidence` : "Setup evidence chart"} kicker="CANONICAL PERSISTED EVIDENCE">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <div className="flex gap-1">{TIMEFRAMES.map((item) => <button key={item.value} onClick={() => onTimeframeChange(item.value)} className={`rounded px-2.5 py-1.5 font-mono text-[10px] ${timeframe === item.value ? "bg-cyan-400/15 text-cyan-200" : "bg-white/[.03] text-slate-500"}`}>{item.label}</button>)}</div>
      <div className="font-mono text-[9px] text-slate-600">EVIDENCE ONLY · NOT EXECUTION PERMISSION</div>
    </div>
    {setup && <StructureAuthoring
      setup={setup} draftKind={activeDraftKind} picks={activePicks} pending={pending}
      message={activeMessage} selectedMacroStructure={selectedMacroStructure}
      fib={definitions.data?.fib ?? null} range={definitions.data?.range ?? null}
      onStart={start} onCancel={cancel} onSave={() => void save()} onDelete={() => void remove()}
    />}
    {!setup ? <EmptyState title="No candidate selected" detail="Select a candidate to load its canonical structure evidence."/> : chart.error ? <div className="rounded border border-red-400/15 bg-red-400/5 p-3 text-xs text-red-200">Chart unavailable: {chart.error}</div> : <><ScannerChartCanvas chart={chart.data} overlays={overlays} showEma={setup.setup_type.startsWith("TREND_PULLBACK")} sideLong={setup.setup_type.endsWith("LONG")} draftKind={activeDraftKind} picks={activePicks} onPick={activeDraftKind ? pick : null}/>{overlays?.missing && <div className="mt-3 rounded border border-amber-400/15 bg-amber-400/5 p-3 text-xs text-amber-200">No actionable structure defined. No inferred level is drawn.</div>}</>}
  </Panel>;
}

function ScannerChartCanvas({ chart, overlays, showEma, sideLong, draftKind, picks, onPick }: { chart: Awaited<ReturnType<typeof api.getChart>> | null; overlays: ReturnType<typeof extractScannerOverlays> | null; showEma: boolean; sideLong: boolean; draftKind: StructureDraftKind | null; picks: ChartPick[]; onPick: ((pick: ChartPick) => void) | null }) {
  const root = useRef<HTMLDivElement>(null);
  const view = useRef<IChartApi | null>(null);
  const bars = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markerPlugin = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const fitted = useRef("");

  useEffect(() => {
    if (!root.current) return;
    const chartView = createChart(root.current, { height: 520, width: root.current.clientWidth, layout: { background: { type: ColorType.Solid, color: "#0b0f15" }, textColor: "#94a3b8" }, grid: { vertLines: { color: "rgba(255,255,255,.025)" }, horzLines: { color: "rgba(255,255,255,.035)" } } });
    view.current = chartView;
    bars.current = chartView.addSeries(CandlestickSeries, { upColor: "#34d399", downColor: "#fb7185", borderVisible: false, wickUpColor: "#34d399", wickDownColor: "#fb7185" });
    markerPlugin.current = createSeriesMarkers(bars.current, []);
    const resize = () => { if (root.current) chartView.applyOptions({ width: root.current.clientWidth }); };
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chartView.remove(); };
  }, []);

  useEffect(() => {
    const barSeries = bars.current; const chartView = view.current;
    if (!chart || !barSeries || !chartView) return;
    barSeries.setData(chart.candles.map((candle) => ({ time: candle.time as never, open: candle.open, high: candle.high, low: candle.low, close: candle.close })));
    const key = `${chart.symbol}/${chart.timeframe}`; if (fitted.current !== key) { chartView.timeScale().fitContent(); fitted.current = key; }
  }, [chart]);

  useEffect(() => {
    const chartView = view.current; const barSeries = bars.current;
    if (!chartView || !barSeries || !onPick) return;
    const handler = (parameter: MouseEventParams<Time>) => {
      if (!parameter.point || parameter.time === undefined) return;
      const price = barSeries.coordinateToPrice(parameter.point.y);
      const time = typeof parameter.time === "number" ? Math.floor(parameter.time) : Date.parse(String(parameter.time)) / 1000;
      if (price !== null && Number.isFinite(price) && price > 0 && Number.isInteger(time) && time > 0) onPick({ time, price });
    };
    chartView.subscribeClick(handler);
    return () => chartView.unsubscribeClick(handler);
  }, [onPick]);

  useEffect(() => {
    const chartView = view.current; const barSeries = bars.current;
    if (!chartView || !barSeries || !chart) return;
    const priceLines: IPriceLine[] = [];
    const series: ISeriesApi<"Line">[] = [];
    overlays?.lines.forEach((item) => priceLines.push(barSeries.createPriceLine({ price: item.price, title: item.label, color: item.color, lineWidth: 1, lineStyle: 2 })));
    overlays?.zones.forEach((item) => {
      priceLines.push(barSeries.createPriceLine({ price: item.lower, title: `${item.label} LOW`, color: item.color.replace(".12", ".65"), lineWidth: 1, lineStyle: 3 }));
      priceLines.push(barSeries.createPriceLine({ price: item.upper, title: `${item.label} HIGH`, color: item.color.replace(".12", ".65"), lineWidth: 1, lineStyle: 3 }));
    });
    if (overlays?.trendline) {
      const trendline = chartView.addSeries(LineSeries, { color: overlays.trendline.color, lineWidth: 2, title: overlays.trendline.label });
      trendline.setData(overlays.trendline.points.map((point) => ({ time: point.time as never, value: point.value })));
      series.push(trendline);
    }
    if (showEma) {
      [["EMA 12", "#22d3ee", "ema12"], ["EMA 21", "#f59e0b", "ema21"]].forEach(([title, color, key]) => {
        const ema = chartView.addSeries(LineSeries, { color, lineWidth: 1, title });
        ema.setData(chart.candles.map((candle) => ({ time: candle.time as never, value: candle[key as "ema12" | "ema21"] })));
        series.push(ema);
      });
    }
    if (draftKind && picks.length) {
      if (draftKind === "MACRO_TRENDLINE" && picks.length === 2) {
        const draftLine = chartView.addSeries(LineSeries, { color: "#22d3ee", lineWidth: 2, title: "DRAFT TRENDLINE" });
        draftLine.setData(picks.map((point) => ({ time: point.time as never, value: point.price })));
        series.push(draftLine);
      } else if (draftKind === "RANGE") {
        picks.forEach((point, index) => priceLines.push(barSeries.createPriceLine({ price: point.price, title: index === 0 ? "DRAFT RANGE LOW" : "DRAFT RANGE HIGH", color: "#22d3ee", lineWidth: 2, lineStyle: 2 })));
      } else if (draftKind === "FIB") {
        if (picks.length === 2 && picks[1].price > picks[0].price) {
          const span = picks[1].price - picks[0].price;
          [0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1].forEach((ratio) => {
            const value = sideLong ? picks[1].price - span * ratio : picks[0].price + span * ratio;
            priceLines.push(barSeries.createPriceLine({ price: value, title: `DRAFT FIB ${ratio}`, color: "#22d3ee", lineWidth: 1, lineStyle: 2 }));
          });
        } else priceLines.push(barSeries.createPriceLine({ price: picks[0].price, title: "DRAFT SWING LOW", color: "#22d3ee", lineWidth: 2, lineStyle: 2 }));
      } else priceLines.push(barSeries.createPriceLine({ price: picks[0].price, title: "DRAFT LEVEL", color: "#22d3ee", lineWidth: 2, lineStyle: 2 }));
    }
    markerPlugin.current?.setMarkers([...(overlays?.markers ?? [])].sort((left, right) => left.time - right.time).map((item) => ({ ...item, time: item.time as never })));
    return () => { priceLines.forEach((item) => barSeries.removePriceLine(item)); series.forEach((item) => chartView.removeSeries(item)); markerPlugin.current?.setMarkers([]); };
  }, [chart, overlays, showEma, sideLong, draftKind, picks]);

  return <div ref={root} className="min-h-[520px]"/>;
}

function StructureAuthoring({ setup, draftKind, picks, pending, message, selectedMacroStructure, fib, range, onStart, onCancel, onSave, onDelete }: { setup: ScannerSetup; draftKind: StructureDraftKind | null; picks: ChartPick[]; pending: boolean; message: string | null; selectedMacroStructure: ChartStructure | null; fib: FibDefinition | null; range: RangeDefinition | null; onStart: (kind: StructureDraftKind, picks?: ChartPick[], id?: string | null) => void; onCancel: () => void; onSave: () => void; onDelete: () => void }) {
  const macro = setup.setup_type.startsWith("MACRO_BREAKOUT"); const rangeSetup = setup.setup_type.startsWith("RANGE");
  const existing = macro ? selectedMacroStructure : rangeSetup ? range : fib;
  const edit = () => {
    const now = Math.floor(Date.now() / 1000);
    const seconds = (value: number) => value > 10_000_000_000 ? Math.floor(value / 1000) : value;
    if (macro && selectedMacroStructure) {
      if (selectedMacroStructure.structure_type === "TRENDLINE" && selectedMacroStructure.anchor_one_time && selectedMacroStructure.anchor_one_price && selectedMacroStructure.anchor_two_time && selectedMacroStructure.anchor_two_price) onStart("MACRO_TRENDLINE", [{ time: seconds(selectedMacroStructure.anchor_one_time), price: Number(selectedMacroStructure.anchor_one_price) }, { time: seconds(selectedMacroStructure.anchor_two_time), price: Number(selectedMacroStructure.anchor_two_price) }], selectedMacroStructure.id);
      else if (selectedMacroStructure.lower_price) onStart(setup.setup_type.endsWith("LONG") ? "MACRO_RESISTANCE" : "MACRO_SUPPORT", [{ time: now, price: Number(selectedMacroStructure.lower_price) }], selectedMacroStructure.id);
    } else if (rangeSetup && range) onStart("RANGE", [{ time: now, price: Number(range.range_low) }, { time: now + 1, price: Number(range.range_high) }]);
    else if (fib) onStart("FIB", [{ time: now, price: Number(fib.swing_low) }, { time: now + 1, price: Number(fib.swing_high) }]);
  };
  return <div className="mb-3 rounded border border-cyan-400/15 bg-cyan-400/[.025] p-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><div><div className="mono-label">STRUCTURE AUTHORING</div><div className="mt-1 text-xs text-slate-500">{hasStructuralBlocker(setup) ? "Canonical structure is required before this setup is active." : "Edit the canonical structure used by Scanner and Workspace."}</div></div><div className="flex gap-2">{existing && !draftKind && <><button onClick={edit} className="rounded border border-white/10 px-3 py-1.5 text-xs text-slate-300">Edit</button><button onClick={onDelete} disabled={pending} className="rounded border border-red-400/20 px-3 py-1.5 text-xs text-red-300">Delete</button></>}{!draftKind && <button disabled={!macro && !setup.trade_plan_id} onClick={() => onStart(macro ? (setup.setup_type.endsWith("LONG") ? "MACRO_RESISTANCE" : "MACRO_SUPPORT") : rangeSetup ? "RANGE" : "FIB")} className="rounded bg-cyan-400/15 px-3 py-1.5 text-xs text-cyan-200 disabled:cursor-not-allowed disabled:opacity-40">Define Structure</button>}</div></div>
    {!draftKind && !setup.trade_plan_id && !macro && <div className="mt-2 text-xs text-amber-300">A compatible active trade plan is required before Range or Fib structure can be persisted.</div>}
    {draftKind && <div className="mt-3 border-t border-white/7 pt-3"><div className="mb-2 flex flex-wrap gap-2">{macro && <><button onClick={() => onStart("MACRO_RESISTANCE")} className="text-xs text-slate-300">Resistance</button><button onClick={() => onStart("MACRO_SUPPORT")} className="text-xs text-slate-300">Support</button><button onClick={() => onStart("MACRO_TRENDLINE")} className="text-xs text-slate-300">Trendline</button></>}</div><div className="text-xs text-cyan-100/75">{draftInstruction(draftKind, picks)}</div><div className="mt-2 flex gap-2"><button onClick={onSave} disabled={pending || picks.length !== requiredPickCount(draftKind)} className="rounded bg-cyan-400/15 px-3 py-1.5 text-xs text-cyan-200 disabled:opacity-40">{pending ? "Saving…" : "Save"}</button><button onClick={onCancel} disabled={pending} className="px-3 py-1.5 text-xs text-slate-500">Cancel</button></div></div>}
    {message && <div className="mt-2 text-xs text-red-300">{message}</div>}
  </div>;
}
