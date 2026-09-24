import type { ScannerSetup, ScannerTransition, TriggerCurrent } from "./api/types";

export type ScannerTimeframe = "4h" | "1h" | "15m" | "5m";
export type OverlayLine = { price: number; label: string; color: string };
export type OverlayZone = { lower: number; upper: number; label: string; color: string };
export type OverlayTrendline = { label: string; color: string; points: { time: number; value: number }[] };
export type OverlayMarker = { time: number; position: "aboveBar" | "belowBar"; color: string; shape: "arrowUp" | "arrowDown" | "circle"; text: string };
export type ScannerOverlays = { lines: OverlayLine[]; zones: OverlayZone[]; trendline: OverlayTrendline | null; markers: OverlayMarker[]; missing: boolean };

const numberValue = (value: unknown): number | null => {
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
};
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const timestamp = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value > 10_000_000_000 ? Math.floor(value / 1000) : Math.floor(value);
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
};
const line = (price: unknown, label: string, color: string): OverlayLine | null => {
  const parsed = numberValue(price); return parsed === null ? null : { price: parsed, label, color };
};
const zone = (value: unknown, label: string, color: string): OverlayZone | null => {
  const item = record(value); const lower = numberValue(item?.lower); const upper = numberValue(item?.upper);
  return lower === null || upper === null ? null : { lower, upper, label, color };
};

export function defaultScannerTimeframe(setupType: ScannerSetup["setup_type"]): ScannerTimeframe {
  return setupType.startsWith("MACRO_BREAKOUT") ? "4h" : "1h";
}

export function candidateCounts(setups: ScannerSetup[]) {
  return {
    tracked: setups.length,
    active: setups.filter((setup) => setup.status !== "IGNORE").length,
    armed: setups.filter((setup) => setup.status === "TRIGGER_ARMED").length,
  };
}

export function filterScannerCandidates(setups: ScannerSetup[], showIgnored: boolean): ScannerSetup[] {
  return showIgnored ? setups : setups.filter((setup) => setup.status !== "IGNORE");
}

export function exactCurrentTrigger(setup: ScannerSetup, current: TriggerCurrent | null): TriggerCurrent | null {
  if (setup.status !== "TRIGGER_ARMED" || current?.watched_setup_id !== setup.id || current.scanner_status !== "TRIGGER_ARMED") return null;
  return current;
}

export function extractScannerOverlays(setup: ScannerSetup, current: TriggerCurrent | null, transitions: ScannerTransition[] = []): ScannerOverlays {
  const structure = record(setup.state.structure);
  const lines: OverlayLine[] = [];
  const zones: OverlayZone[] = [];
  const markers: OverlayMarker[] = [];
  let trendline: OverlayTrendline | null = null;

  if (setup.setup_type.startsWith("MACRO_BREAKOUT") && structure) {
    [line(structure.breakout_level, "BREAKOUT", "#f59e0b"), line(setup.state.accepted_breakout_level, "ACCEPTED BREAKOUT", "#22d3ee")].forEach((item) => { if (item) lines.push(item); });
    [zone(structure.approach_zone, "APPROACH TOLERANCE", "rgba(245,158,11,.12)"), zone(structure.retest_zone, "RETEST TOLERANCE", "rgba(34,211,238,.12)")].forEach((item) => { if (item) zones.push(item); });
    const t1 = timestamp(structure.anchor_one_time); const t2 = timestamp(structure.anchor_two_time);
    const p1 = numberValue(structure.anchor_one_price); const p2 = numberValue(structure.anchor_two_price);
    if (structure.type === "TRENDLINE" && t1 !== null && t2 !== null && p1 !== null && p2 !== null) trendline = { label: typeof structure.label === "string" ? structure.label : "MACRO TRENDLINE", color: "#f59e0b", points: [{ time: t1, value: p1 }, { time: t2, value: p2 }] };
    [line(structure.lower_price, "STRUCTURE LOW", "#a78bfa"), line(structure.upper_price, "STRUCTURE HIGH", "#a78bfa")].forEach((item) => { if (item) lines.push(item); });
  } else if (setup.setup_type.startsWith("RANGE") && structure) {
    [line(structure.range_low, "RANGE LOW", "#a78bfa"), line(structure.range_high, "RANGE HIGH", "#a78bfa"), line(structure.actionable_level, "ACTIONABLE", "#f59e0b")].forEach((item) => { if (item) lines.push(item); });
    const approach = zone(structure.approach_zone, "APPROACH TOLERANCE", "rgba(245,158,11,.12)"); if (approach) zones.push(approach);
  } else if (setup.setup_type.startsWith("TREND_PULLBACK") && structure) {
    const levels = record(structure.levels) ?? {};
    ["0.236", "0.382", "0.500", "0.618", "0.786", "0.886", "1.000"].forEach((ratio) => { const item = line(levels[ratio], `FIB ${Number(ratio)}`, "#a78bfa"); if (item) lines.push(item); });
    const actionable = zone(structure.actionable_zone, "ACTIONABLE FIB ZONE", "rgba(34,211,238,.12)"); if (actionable) zones.push(actionable);
    const approach = zone(structure.approach_zone, "APPROACH TOLERANCE", "rgba(245,158,11,.12)"); if (approach) zones.push(approach);
  }

  transitions.forEach((transition) => {
    if (!["BREAKOUT_ATTEMPT", "ACCEPTANCE_PENDING", "BREAKOUT_ACCEPTED", "RETEST_PENDING", "TRIGGER_ARMED"].includes(transition.to_status)) return;
    const time = timestamp(transition.timestamp); if (time === null) return;
    markers.push({ time, position: setup.setup_type.endsWith("LONG") ? "belowBar" : "aboveBar", color: "#22d3ee", shape: "circle", text: transition.to_status.replaceAll("_", " ") });
  });
  const acceptedAt = timestamp(setup.state.accepted_at);
  if (acceptedAt !== null && !markers.some((marker) => marker.time === acceptedAt && marker.text === "BREAKOUT ACCEPTED")) markers.push({ time: acceptedAt, position: setup.setup_type.endsWith("LONG") ? "belowBar" : "aboveBar", color: "#34d399", shape: "circle", text: "BREAKOUT ACCEPTED" });

  const exact = exactCurrentTrigger(setup, current); const result = exact?.attempt?.result;
  if (result) {
    const reference = line(result.reference_level, `LTF ${result.state}`, result.state === "FAILED" ? "#fb7185" : "#34d399"); if (reference) lines.push(reference);
    const anchorTime = timestamp(result.anchor_bar_end_ms); if (anchorTime !== null) markers.push({ time: anchorTime, position: setup.setup_type.endsWith("LONG") ? "belowBar" : "aboveBar", color: result.state === "FAILED" ? "#fb7185" : "#f59e0b", shape: "circle", text: result.pattern === "DEVIATION_RECLAIM" ? "RECLAIM" : "RETEST" });
    const confirmationTime = timestamp(result.confirmation_bar_end_ms); if (confirmationTime !== null) markers.push({ time: confirmationTime, position: setup.setup_type.endsWith("LONG") ? "belowBar" : "aboveBar", color: result.state === "FAILED" ? "#fb7185" : "#34d399", shape: setup.setup_type.endsWith("LONG") ? "arrowUp" : "arrowDown", text: result.state === "FAILED" ? "TRIGGER FAILED" : "LTF CONFIRMATION" });
    const failureTime = timestamp(result.evaluated_at); if (result.state === "FAILED" && confirmationTime === null && failureTime !== null) markers.push({ time: failureTime, position: setup.setup_type.endsWith("LONG") ? "belowBar" : "aboveBar", color: "#fb7185", shape: "circle", text: "TRIGGER FAILED" });
  }
  return { lines, zones, trendline, markers, missing: lines.length === 0 && zones.length === 0 && trendline === null };
}
