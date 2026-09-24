"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useRef } from "react";
import { CandlestickSeries, ColorType, createChart, createSeriesMarkers, LineSeries } from "lightweight-charts";
import type { Chart, ChartStructure, StateChange, TradePlan } from "@/lib/api/types";
import type { Fib, Range } from "./fib-range-editor";

const markerEvents = new Set(["REACTION_DEVELOPING", "REACTION_CONFIRMED", "THESIS_WARNING", "RISK_BREACH", "ADD_ALLOWED", "ADD_LOCKED", "REDUCE", "EXIT", "INVALIDATE"]);

export function markersForChart(changes: StateChange[], symbol: string) {
  return changes.flatMap((change) => {
    const timestamp = Date.parse(change.timestamp);
    if (change.symbol !== symbol || !markerEvents.has(change.event_type) || !Number.isFinite(timestamp)) return [];
    return [{ time: Math.floor(timestamp / 1000) as never, position: "belowBar" as const, color: "#f59e0b", shape: "circle" as const, text: change.event_type }];
  });
}

export function PriceChart({ chart, plan, structures, changes, fib, range }: { chart: Chart | null; plan: TradePlan | null; structures: ChartStructure[]; changes: StateChange[]; fib: Fib | null; range: Range | null }) {
  const root = useRef<HTMLDivElement>(null);
  const view = useRef<any>(null);
  const bars = useRef<any>(null);
  const markerPlugin = useRef<any>(null);
  const fittedChart = useRef("");

  useEffect(() => {
    if (!root.current) return;
    view.current = createChart(root.current, { height: 520, width: root.current.clientWidth, layout: { background: { type: ColorType.Solid, color: "#0b0f15" }, textColor: "#94a3b8" } });
    bars.current = view.current.addSeries(CandlestickSeries, { upColor: "#34d399", downColor: "#fb7185" });
    markerPlugin.current = createSeriesMarkers(bars.current, []);
    return () => view.current?.remove();
  }, []);

  useEffect(() => {
    if (!chart || !bars.current) return;
    bars.current.setData(chart.candles.map((candle) => ({ time: candle.time as never, open: candle.open, high: candle.high, low: candle.low, close: candle.close })));
    const chartKey = `${chart.symbol}/${chart.timeframe}`;
    if (fittedChart.current !== chartKey) { view.current.timeScale().fitContent(); fittedChart.current = chartKey; }
  }, [chart]);

  useEffect(() => {
    markerPlugin.current?.setMarkers(markersForChart(changes, chart?.symbol ?? ""));
  }, [changes, chart?.symbol]);

  useEffect(() => {
    if (!bars.current || !view.current) return;
    const lines: any[] = [];
    const series: any[] = [];
    const addLine = (value: unknown, label: string) => {
      if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return;
      lines.push(bars.current.createPriceLine({ price: Number(value), color: "#f59e0b", lineWidth: 1, lineStyle: 2, title: label }));
    };
    if (plan) {
      addLine((plan.entry_probe_plan as Record<string, unknown>).entry, "ENTRY");
      addLine(plan.thesis_warning, "WARNING");
      addLine(plan.hard_invalidation, "INVALIDATION");
      [...plan.target_ladder]
        .sort((left, right) => Number(left.ordering) - Number(right.ordering))
        .forEach((target, index) => addLine(target.price, `TP${index + 1}`));
    }
    Object.entries(fib?.levels ?? {}).forEach(([ratio, price]) => addLine(price, `Fib ${ratio}`));
    if (range) {
      addLine(range.range_low, "RANGE LOW");
      addLine((Number(range.range_low) + Number(range.range_high)) / 2, "RANGE MID");
      addLine(range.range_high, "RANGE HIGH");
    }
    structures.filter((structure) => structure.active).forEach((structure) => {
      if (structure.structure_type === "HORIZONTAL_ZONE") {
        addLine(structure.lower_price, `${structure.label ?? "ZONE"} LOW`);
        addLine(structure.upper_price, `${structure.label ?? "ZONE"} HIGH`);
      } else if (structure.anchor_one_time && structure.anchor_one_price && structure.anchor_two_time && structure.anchor_two_price) {
        const trendline = view.current.addSeries(LineSeries, { color: "#f59e0b", title: structure.label ?? "TRENDLINE" });
        const chartTime = (value: number) => value > 10_000_000_000 ? Math.floor(value / 1000) : value;
        trendline.setData([{ time: chartTime(structure.anchor_one_time) as never, value: Number(structure.anchor_one_price) }, { time: chartTime(structure.anchor_two_time) as never, value: Number(structure.anchor_two_price) }]);
        series.push(trendline);
      }
    });
    return () => { lines.forEach((line) => bars.current?.removePriceLine(line)); series.forEach((item) => view.current?.removeSeries(item)); };
  }, [plan, structures, fib, range]);

  return <div ref={root} />;
}
