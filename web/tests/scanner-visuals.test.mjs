import assert from "node:assert/strict";
import test from "node:test";
import { PREVIEW_ACCOUNT, PREVIEW_PORTFOLIO, privateSyncLabel } from "../src/lib/dashboard-preview.ts";
import { addChartPick, buildStructureDraft, hydrateStructureDraft, undoChartPick } from "../src/lib/scanner-authoring.ts";
import { candidateCounts, defaultScannerTimeframe, exactCurrentTrigger, extractScannerOverlays, filterScannerCandidates, hasStructuralBlocker } from "../src/lib/scanner-visuals.ts";

function setup(id, setupType, status, structure = {}) {
  return { id, symbol: "BTCUSDT", setup_type: setupType, status, state: { structure }, version: 1, trade_plan_id: null, last_evaluated_at: null, updated_at: "2026-01-01T00:00:00Z", created_at: "2026-01-01T00:00:00Z" };
}

test("ignored candidates stay persisted but are hidden by default", () => {
  const rows = [setup("active", "RANGE_LONG", "WATCH"), setup("ignored", "RANGE_SHORT", "IGNORE")];
  assert.deepEqual(filterScannerCandidates(rows, false).map((row) => row.id), ["active"]);
  assert.deepEqual(filterScannerCandidates(rows, true).map((row) => row.id), ["active", "ignored"]);
});

test("tracked active and armed counts use all persisted directional setups", () => {
  const rows = [setup("a", "RANGE_LONG", "WATCH"), setup("b", "RANGE_SHORT", "IGNORE"), setup("c", "TREND_PULLBACK_LONG", "TRIGGER_ARMED")];
  assert.deepEqual(candidateCounts(rows), { tracked: 3, active: 2, needsStructure: 0, armed: 1 });
});

test("structural blockers are presented as needs structure and excluded from active", () => {
  const blocked = setup("blocked", "RANGE_LONG", "WATCH"); blocked.state.blocking_reasons = ["STRUCTURE_REQUIRED"];
  assert.equal(hasStructuralBlocker(blocked), true);
  assert.deepEqual(candidateCounts([blocked]), { tracked: 1, active: 0, needsStructure: 1, armed: 0 });
});

test("setup-aware timeframes default macro to 4H and plan structures to 1H", () => {
  assert.equal(defaultScannerTimeframe("MACRO_BREAKOUT_LONG"), "4h");
  assert.equal(defaultScannerTimeframe("TREND_PULLBACK_SHORT"), "1h");
  assert.equal(defaultScannerTimeframe("RANGE_LONG"), "1h");
});

test("macro overlays use exact persisted structure and tolerance evidence", () => {
  const row = setup("macro", "MACRO_BREAKOUT_LONG", "BREAKOUT_ACCEPTED", { type: "HORIZONTAL_ZONE", lower_price: 98, upper_price: 100, breakout_level: 100, approach_zone: { lower: 99, upper: 101 }, retest_zone: { lower: 99.5, upper: 100.5 } });
  row.state.accepted_breakout_level = 100;
  const overlay = extractScannerOverlays(row, null);
  assert.deepEqual(overlay.lines.map((item) => item.label), ["ACCEPTED BREAKOUT · BREAKOUT · STRUCTURE HIGH", "STRUCTURE LOW"]);
  assert.deepEqual(overlay.zones.map((item) => item.label), ["APPROACH TOLERANCE", "RETEST TOLERANCE"]);
});

test("range and trend overlays are extracted without reconstructing levels", () => {
  const range = extractScannerOverlays(setup("range", "RANGE_SHORT", "WATCH", { type: "RANGE", range_low: 90, range_high: 110, actionable_level: 110, approach_zone: { lower: 109, upper: 111 } }), null);
  assert.deepEqual(range.lines.map((item) => item.price), [90, 110]);
  const levels = Object.fromEntries(["0.236", "0.382", "0.500", "0.618", "0.786", "0.886", "1.000"].map((ratio, index) => [ratio, 100 + index]));
  const trend = extractScannerOverlays(setup("trend", "TREND_PULLBACK_LONG", "AT_LOCATION", { type: "FIB_RETRACEMENT", levels, actionable_zone: { lower: 102, upper: 103 }, approach_zone: { lower: 101, upper: 104 } }), null);
  assert.equal(trend.lines.length, 7);
  assert.deepEqual(trend.zones.map((item) => item.label), ["ACTIONABLE FIB ZONE", "APPROACH TOLERANCE"]);
});

test("missing canonical structure draws nothing", () => {
  assert.equal(extractScannerOverlays(setup("missing", "RANGE_LONG", "WATCH"), null).missing, true);
});

test("trigger overlay is accepted only for the exact current armed setup", () => {
  const row = setup("arm-b", "RANGE_LONG", "TRIGGER_ARMED", { type: "RANGE", range_low: 90, range_high: 110, actionable_level: 90 });
  const current = { watched_setup_id: "arm-a", scanner_status: "TRIGGER_ARMED", attempt: { result: { reference_level: 90, state: "CONFIRMED", pattern: "DEVIATION_RECLAIM", anchor_bar_end_ms: null, confirmation_bar_end_ms: null } } };
  assert.equal(exactCurrentTrigger(row, current), null);
  assert.equal(extractScannerOverlays(row, current).lines.some((item) => item.label.startsWith("LTF")), false);
  current.watched_setup_id = "arm-b";
  assert.equal(exactCurrentTrigger(row, current), current);
  assert.equal(extractScannerOverlays(row, current).lines.some((item) => item.label.includes("LTF CONFIRMED")), true);
});

test("near-identical semantic lines and same-bar lifecycle markers normalize deterministically", () => {
  const row = setup("macro", "MACRO_BREAKOUT_LONG", "BREAKOUT_ACCEPTED", { type: "HORIZONTAL_ZONE", lower_price: 100, upper_price: 100.0000001, breakout_level: 100 });
  row.state.accepted_breakout_level = 100.0000002;
  row.state.accepted_at = "2026-01-01T00:00:00Z";
  const transitions = [{ to_status: "BREAKOUT_ACCEPTED", timestamp: "2026-01-01T00:00:00Z" }, { to_status: "RETEST_PENDING", timestamp: "2026-01-01T00:00:00Z" }];
  const overlay = extractScannerOverlays(row, null, transitions);
  assert.equal(overlay.lines.length, 1);
  assert.match(overlay.lines[0].label, /ACCEPTED BREAKOUT/);
  assert.match(overlay.lines[0].label, /STRUCTURE/);
  assert.equal(overlay.markers.length, 1);
  assert.match(overlay.markers[0].text, /BREAKOUT ACCEPTED · RETEST PENDING/);
});

test("completed picks lock, Undo removes only the last point, and edit hydrates persisted geometry", () => {
  const first = { time: 100, price: 90 }; const second = { time: 200, price: 110 }; const third = { time: 300, price: 120 };
  const complete = addChartPick("MACRO_TRENDLINE", addChartPick("MACRO_TRENDLINE", [], first), second);
  assert.equal(addChartPick("MACRO_TRENDLINE", complete, third), complete);
  assert.deepEqual(undoChartPick(complete), [first]);
  const macro = setup("macro", "MACRO_BREAKOUT_LONG", "WATCH");
  const hydrated = hydrateStructureDraft(macro, { id: "line-1", structure_type: "TRENDLINE", anchor_one_time: 100000, anchor_one_price: 90, anchor_two_time: 200000, anchor_two_price: 110 }, null, null, 999);
  const exactStored = [{ time: 100000, price: 90 }, { time: 200000, price: 110 }];
  assert.deepEqual(hydrated, { kind: "MACRO_TRENDLINE", picks: exactStored, id: "line-1" });
});

test("reset response state removes stale canonical overlays", () => {
  const reset = setup("macro", "MACRO_BREAKOUT_LONG", "WATCH");
  reset.state.blocking_reasons = ["STRUCTURE_REQUIRED"];
  const overlay = extractScannerOverlays(reset, null);
  assert.equal(overlay.missing, true);
  assert.deepEqual(overlay.lines, []);
});

test("missing canonical macro source suppresses stale setup and trigger overlays", () => {
  const row = setup("stale", "MACRO_BREAKOUT_LONG", "TRIGGER_ARMED", { structure_id: "deleted-structure", type: "HORIZONTAL_ZONE", lower_price: 100, upper_price: 100, breakout_level: 100, approach_zone: { lower: 99, upper: 101 } });
  row.state.accepted_structure_id = "deleted-structure";
  row.state.accepted_breakout_level = 100;
  const current = { watched_setup_id: "stale", scanner_status: "TRIGGER_ARMED", attempt: { result: { reference_level: 100, state: "CONFIRMED", pattern: "DEVIATION_RECLAIM", anchor_bar_end_ms: 1000, confirmation_bar_end_ms: 1000 } } };
  const overlay = extractScannerOverlays(row, current, [{ to_status: "TRIGGER_ARMED", timestamp: "2026-01-01T00:00:00Z" }], new Set());
  assert.equal(overlay.staleCanonicalReference, true);
  assert.deepEqual(overlay.lines, []);
  assert.deepEqual(overlay.zones, []);
  assert.deepEqual(overlay.markers, []);
});

test("canonical macro source permits persisted evidence rendering", () => {
  const row = setup("current", "MACRO_BREAKOUT_LONG", "BREAKOUT_ACCEPTED", { structure_id: "current-structure", breakout_level: 100 });
  assert.equal(extractScannerOverlays(row, null, [], new Set(["current-structure"])).staleCanonicalReference, false);
});

test("disabled private sync has explicit preview data instead of an API failure", () => {
  assert.equal(privateSyncLabel({ enabled: false, credentials_configured: false, mode: "read_only" }), "PREVIEW / READ ONLY");
  assert.deepEqual(PREVIEW_ACCOUNT.positions, []);
  assert.equal(PREVIEW_PORTFOLIO.provenance.account, "private sync disabled — preview only");
});

test("chart picks build canonical Macro, Range, and Fib API payloads", () => {
  const macro = setup("macro", "MACRO_BREAKOUT_LONG", "WATCH");
  assert.deepEqual(buildStructureDraft(macro, "MACRO_RESISTANCE", [{ time: 100, price: 120 }], "4h"), { target: "CHART_STRUCTURE", body: { symbol: "BTCUSDT", timeframe: "4h", structure_type: "HORIZONTAL_ZONE", label: "Scanner resistance", lower_price: 120, upper_price: 120, active: true } });
  const range = setup("range", "RANGE_LONG", "WATCH"); range.trade_plan_id = "plan-range";
  assert.deepEqual(buildStructureDraft(range, "RANGE", [{ time: 100, price: 90 }, { time: 200, price: 110 }], "1h"), { target: "RANGE", planId: "plan-range", body: { range_low: 90, range_high: 110 } });
  const trend = setup("trend", "TREND_PULLBACK_SHORT", "WATCH"); trend.trade_plan_id = "plan-trend";
  assert.deepEqual(buildStructureDraft(trend, "FIB", [{ time: 100, price: 80 }, { time: 200, price: 120 }], "1h"), { target: "FIB", planId: "plan-trend", body: { direction: "SHORT", swing_low: 80, swing_high: 120 } });
});

test("malformed chart interactions never produce persistence payloads", () => {
  const macro = setup("macro", "MACRO_BREAKOUT_LONG", "WATCH");
  assert.throws(() => buildStructureDraft(macro, "MACRO_TRENDLINE", [{ time: 100, price: 90 }, { time: 100, price: 110 }], "4h"), /distinct timestamps/);
  const range = setup("range", "RANGE_LONG", "WATCH"); range.trade_plan_id = "plan-range";
  assert.throws(() => buildStructureDraft(range, "RANGE", [{ time: 100, price: 110 }, { time: 200, price: 90 }], "1h"), /Range high/);
  assert.throws(() => buildStructureDraft(macro, "MACRO_SUPPORT", [{ time: 100, price: Number.NaN }], "4h"), /valid chart point/);
});
