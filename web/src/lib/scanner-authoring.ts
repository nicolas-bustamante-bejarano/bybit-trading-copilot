import type { ScannerSetup } from "./api/types";
import type { ScannerTimeframe } from "./scanner-visuals";

export type StructureDraftKind = "MACRO_RESISTANCE" | "MACRO_SUPPORT" | "MACRO_TRENDLINE" | "RANGE" | "FIB";
export type ChartPick = { time: number; price: number };
export type StructureDraftPayload =
  | { target: "CHART_STRUCTURE"; body: Record<string, unknown> }
  | { target: "RANGE"; planId: string; body: Record<string, unknown> }
  | { target: "FIB"; planId: string; body: Record<string, unknown> };

const validPick = (pick: ChartPick | undefined): pick is ChartPick => Boolean(pick && Number.isFinite(pick.price) && pick.price > 0 && Number.isInteger(pick.time) && pick.time > 0);

export function requiredPickCount(kind: StructureDraftKind): number {
  return kind === "MACRO_RESISTANCE" || kind === "MACRO_SUPPORT" ? 1 : 2;
}

export function buildStructureDraft(setup: ScannerSetup, kind: StructureDraftKind, picks: ChartPick[], timeframe: ScannerTimeframe): StructureDraftPayload {
  const required = requiredPickCount(kind);
  if (picks.length !== required || !picks.every(validPick)) throw new Error(`Select ${required} valid chart point${required === 1 ? "" : "s"}.`);
  const macro = setup.setup_type.startsWith("MACRO_BREAKOUT");
  const range = setup.setup_type.startsWith("RANGE");
  const trend = setup.setup_type.startsWith("TREND_PULLBACK");
  if (kind.startsWith("MACRO_") && !macro) throw new Error("Macro structures are only compatible with Macro Breakout setups.");
  if (kind === "RANGE" && !range) throw new Error("Range bounds are only compatible with Range setups.");
  if (kind === "FIB" && !trend) throw new Error("Fib anchors are only compatible with Trend Pullback setups.");

  if (kind === "MACRO_RESISTANCE" || kind === "MACRO_SUPPORT") {
    const price = picks[0].price;
    return { target: "CHART_STRUCTURE", body: { symbol: setup.symbol, timeframe, structure_type: "HORIZONTAL_ZONE", label: kind === "MACRO_RESISTANCE" ? "Scanner resistance" : "Scanner support", lower_price: price, upper_price: price, active: true } };
  }
  if (kind === "MACRO_TRENDLINE") {
    if (picks[0].time === picks[1].time) throw new Error("Trendline anchors must have distinct timestamps.");
    return { target: "CHART_STRUCTURE", body: { symbol: setup.symbol, timeframe, structure_type: "TRENDLINE", label: "Scanner trendline", anchor_one_time: picks[0].time * 1000, anchor_one_price: picks[0].price, anchor_two_time: picks[1].time * 1000, anchor_two_price: picks[1].price, active: true } };
  }
  if (!setup.trade_plan_id) throw new Error("A compatible active trade plan is required before this structure can be defined.");
  const low = picks[0].price; const high = picks[1].price;
  if (high <= low) throw new Error(kind === "RANGE" ? "Range high must be greater than range low." : "Swing high must be greater than swing low.");
  if (kind === "RANGE") return { target: "RANGE", planId: setup.trade_plan_id, body: { range_low: low, range_high: high } };
  return { target: "FIB", planId: setup.trade_plan_id, body: { direction: setup.setup_type.endsWith("LONG") ? "LONG" : "SHORT", swing_low: low, swing_high: high } };
}

export function draftInstruction(kind: StructureDraftKind, picks: ChartPick[]): string {
  if (kind === "MACRO_RESISTANCE" || kind === "MACRO_SUPPORT") return picks.length ? "Level selected. Review the preview, then Save." : "Click the chart once to select the exact level.";
  if (kind === "MACRO_TRENDLINE") return picks.length === 0 ? "Click trendline anchor 1." : picks.length === 1 ? "Click trendline anchor 2." : "Review the two-point preview, then Save.";
  if (kind === "RANGE") return picks.length === 0 ? "Click range low." : picks.length === 1 ? "Click range high." : "Review the range boundaries, then Save.";
  return picks.length === 0 ? "Click swing low." : picks.length === 1 ? "Click swing high." : "Review the Fib anchors, then Save.";
}
