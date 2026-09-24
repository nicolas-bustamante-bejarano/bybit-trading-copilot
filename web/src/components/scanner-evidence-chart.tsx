"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import { CandlestickSeries, ColorType, createChart, createSeriesMarkers, LineSeries } from "lightweight-charts";
import type { IChartApi, IPriceLine, ISeriesApi, ISeriesMarkersPluginApi, Time } from "lightweight-charts";
import { api } from "@/lib/api/client";
import { usePolling } from "@/lib/api/use-polling";
import type { ScannerSetup, TriggerCurrent } from "@/lib/api/types";
import { extractScannerOverlays, type ScannerTimeframe } from "@/lib/scanner-visuals";
import { EmptyState, Panel } from "./ui";

const TIMEFRAMES: { value: ScannerTimeframe; label: string }[] = [{ value: "4h", label: "4H" }, { value: "1h", label: "1H" }, { value: "15m", label: "15m" }, { value: "5m", label: "5m" }];

export function ScannerEvidenceChart({ setup, trigger, timeframe, onTimeframeChange }: { setup: ScannerSetup | null; trigger: TriggerCurrent | null; timeframe: ScannerTimeframe; onTimeframeChange: (timeframe: ScannerTimeframe) => void }) {
  const symbol = setup?.symbol ?? null; const setupId = setup?.id ?? null;
  const chartLoader = useCallback(() => symbol ? api.getChart(symbol, timeframe) : Promise.resolve(null), [symbol, timeframe]);
  const transitionLoader = useCallback(() => setupId ? api.getScannerTransitions({ watched_setup_id: setupId, limit: 100 }) : Promise.resolve([]), [setupId]);
  const chart = usePolling(chartLoader, 15000);
  const transitions = usePolling(transitionLoader, 15000);
  const overlays = useMemo(() => setup ? extractScannerOverlays(setup, trigger, transitions.data ?? []) : null, [setup, trigger, transitions.data]);

  return <Panel title={setup ? `${setup.symbol} · setup evidence` : "Setup evidence chart"} kicker="CANONICAL PERSISTED EVIDENCE">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <div className="flex gap-1">{TIMEFRAMES.map((item) => <button key={item.value} onClick={() => onTimeframeChange(item.value)} className={`rounded px-2.5 py-1.5 font-mono text-[10px] ${timeframe === item.value ? "bg-cyan-400/15 text-cyan-200" : "bg-white/[.03] text-slate-500"}`}>{item.label}</button>)}</div>
      <div className="font-mono text-[9px] text-slate-600">EVIDENCE ONLY · NOT EXECUTION PERMISSION</div>
    </div>
    {!setup ? <EmptyState title="No candidate selected" detail="Select a candidate to load its canonical structure evidence."/> : chart.error ? <div className="rounded border border-red-400/15 bg-red-400/5 p-3 text-xs text-red-200">Chart unavailable: {chart.error}</div> : <><ScannerChartCanvas chart={chart.data} overlays={overlays} showEma={setup.setup_type.startsWith("TREND_PULLBACK")}/>{overlays?.missing && <div className="mt-3 rounded border border-amber-400/15 bg-amber-400/5 p-3 text-xs text-amber-200">No actionable structure defined. No inferred level is drawn.</div>}</>}
  </Panel>;
}

function ScannerChartCanvas({ chart, overlays, showEma }: { chart: Awaited<ReturnType<typeof api.getChart>> | null; overlays: ReturnType<typeof extractScannerOverlays> | null; showEma: boolean }) {
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
    markerPlugin.current?.setMarkers([...(overlays?.markers ?? [])].sort((left, right) => left.time - right.time).map((item) => ({ ...item, time: item.time as never })));
    return () => { priceLines.forEach((item) => barSeries.removePriceLine(item)); series.forEach((item) => chartView.removeSeries(item)); markerPlugin.current?.setMarkers([]); };
  }, [chart, overlays, showEma]);

  return <div ref={root} className="min-h-[520px]"/>;
}
