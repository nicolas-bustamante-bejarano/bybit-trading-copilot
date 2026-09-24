import type { ChartStructure, FibDefinition, RangeDefinition, ScannerSetup } from "./api/types";
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

export function addChartPick(kind: StructureDraftKind, picks: ChartPick[], point: ChartPick): ChartPick[] {
  return picks.length >= requiredPickCount(kind) ? picks : [...picks, point];
}

export function undoChartPick(picks: ChartPick[]): ChartPick[] {
  return picks.slice(0, -1);
}

export function hydrateStructureDraft(
  setup: ScannerSetup,
  structure: ChartStructure | null,
  range: RangeDefinition | null,
  fib: FibDefinition | null,
  fallbackTime = Math.floor(Date.now() / 1000),
): { kind: StructureDraftKind; picks: ChartPick[]; id: string | null } | null {
  const seconds = (value: number) => value > 10_000_000_000 ? Math.floor(value / 1000) : value;
  if (setup.setup_type.startsWith("MACRO_BREAKOUT") && structure) {
    if (structure.structure_type === "TRENDLINE" && structure.anchor_one_time && structure.anchor_one_price && structure.anchor_two_time && structure.anchor_two_price) {
      return { kind: "MACRO_TRENDLINE", picks: [{ time: seconds(structure.anchor_one_time), price: Number(structure.anchor_one_price) }, { time: seconds(structure.anchor_two_time), price: Number(structure.anchor_two_price) }], id: structure.id };
    }
    if (structure.lower_price) return { kind: setup.setup_type.endsWith("LONG") ? "MACRO_RESISTANCE" : "MACRO_SUPPORT", picks: [{ time: fallbackTime, price: Number(structure.lower_price) }], id: structure.id };
  }
  if (setup.setup_type.startsWith("RANGE") && range) return { kind: "RANGE", picks: [{ time: fallbackTime, price: Number(range.range_low) }, { time: fallbackTime + 1, price: Number(range.range_high) }], id: null };
  if (setup.setup_type.startsWith("TREND_PULLBACK") && fib) return { kind: "FIB", picks: [{ time: fallbackTime, price: Number(fib.swing_low) }, { time: fallbackTime + 1, price: Number(fib.swing_high) }], id: null };
  return null;
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
  if (kind === "MACRO_RESISTANCE" || kind === "MACRO_SUPPORT") return picks.length ? "Level selected. Review the preview, then Save." : `Select ${kind === "MACRO_RESISTANCE" ? "resistance" : "support"} with one chart click.`;
  if (kind === "MACRO_TRENDLINE") return picks.length === 0 ? "Select anchor 1 on the chart." : picks.length === 1 ? "Select anchor 2 on the chart." : "Both anchors selected. Use Undo to revise or Save.";
  if (kind === "RANGE") return picks.length === 0 ? "Select range low on the chart." : picks.length === 1 ? "Select range high on the chart." : "Both range bounds selected. Use Undo to revise or Save.";
  return picks.length === 0 ? "Select swing low on the chart." : picks.length === 1 ? "Select swing high on the chart." : "Both Fib anchors selected. Use Undo to revise or Save.";
}

export function isStructureDraftValid(setup: ScannerSetup, kind: StructureDraftKind, picks: ChartPick[], timeframe: ScannerTimeframe): boolean {
  try { buildStructureDraft(setup, kind, picks, timeframe); return true; }
  catch { return false; }
}
