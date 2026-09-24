export type EvidenceStatus = "CONFIRMED" | "PARTIAL" | "STALE" | "MISSING" | "INDETERMINATE";
export type RiskStatus = "PASS" | "BREACH" | "INDETERMINATE";
export type ExecutionState = "HOLD" | "ADD" | "REDUCE" | "EXIT" | "INVALIDATE";

export interface Health { status: string }
export interface AccountStatus { enabled: boolean; credentials_configured: boolean; mode: string }
export interface LiveStatus { enabled: boolean; configured_symbols: string[]; active_symbols: string[] }
export interface Position {
  symbol: string; side: "long" | "short"; quantity: number; average_entry: number;
  mark_price: number | null; unrealized_pnl: number | null; stop_loss: number | null;
  take_profit: number | null; correlation_group: string; structural_risk_usdt: number | null;
  protected: boolean;
}
export interface Account {
  equity_usdt: number; available_balance_usdt: number | null; max_group_risk_pct: number;
  risk_budget_usdt: number; total_structural_risk_usdt: number; total_structural_risk_pct: number;
  within_risk_budget: boolean; unprotected_symbols: string[]; positions: Position[];
}
export interface Portfolio {
  equity_usdt: number; available_balance_usdt: number | null; risk_policy_pct: number;
  risk_budget_usdt: number; known_structural_risk_usdt: number; known_structural_risk_pct: number | null;
  total_structural_risk_pct: number | null; risk_policy_status: RiskStatus; unknown_symbols: string[];
  provenance: Record<string, string>;
  correlation_groups: Record<string, { known_risk_usdt: number; positions: string[]; unknown: string[] }>;
  positions: Position[];
}
export interface Coach {
  symbol: string; timestamp: string; side: string; confidence: EvidenceStatus;
  position: { quantity: string; average_entry: string; mark_price: string | null; unrealized_pnl_usdt: string | null; planned_open_risk_usdt: string | null; open_position_r: string | null; lifecycle_history_complete: boolean; lifecycle_confidence: EvidenceStatus };
  plan: { status: EvidenceStatus; trade_plan_id: string | null; setup_type: string | null; lifecycle_status: string | null; thesis: string | null; hard_invalidation: string | null; thesis_warning: string | null; correlation_group: string | null; targets: Record<string, unknown>[]; stop_provenance: string };
  market_context: { status: EvidenceStatus; regime_4h: string | null; context_1h: string | null; ema_12: string | null; ema_21: string | null; stoch_rsi: { k?: string | null; d?: string | null }; location: string; location_status: EvidenceStatus; playbook_state: string | null; reaction: string | null; reaction_status: EvidenceStatus; funding: string | null; open_interest: string | null; order_flow: Record<string, unknown> };
  risk: { account_equity: string; position_structural_risk_usdt: string | null; position_risk_pct: string | null; correlation_group: string; known_group_risk_usdt: string; group_risk_pct: string | null; max_risk_pct: string | null; policy_status: RiskStatus; provenance: Record<string, string>; incomplete_symbols: string[] };
  execution: { state: ExecutionState; add_allowed: boolean; evidence_present: string[]; evidence_missing: string[]; blocking_reasons: string[]; warnings: string[]; next_conditions: string[]; invalidation: string | null; condition_evidence: string[] };
}
export interface ExecutionRule { id: string; action: string; rule_type: string; description: string; parameters: Record<string, unknown>; ordering: number }
export interface TradePlan {
  id: string; symbol: string; side: string; setup_type: string; thesis: string; lifecycle_status: string;
  hard_invalidation: string; thesis_warning: string | null; max_risk_percent: string; max_risk_value: string | null;
  correlation_group: string | null; entry_probe_plan: Record<string, unknown>; add_conditions: Record<string, unknown>[];
  target_ladder: Record<string, unknown>[]; notes: string | null; created_at: string; updated_at: string;
  execution_rules?: ExecutionRule[];
}
export interface Snapshot { id: string; timestamp: string; symbol: string; price: string; action_considered: string; action_taken: string | null; state?: Record<string, unknown>; evidence_present: string[]; evidence_missing: string[]; reason: string | null }
export interface ExecutionEvent { id: string; timestamp: string; symbol: string; event_type: string; quantity: string | null; price: string | null; planned: boolean | null; confidence_status: string }
export interface TradeReview { id: string; created_at: string; realized_r: string | null; facts: Record<string, boolean | null>; deviations: string[]; lifecycle_history_complete: boolean | null; data_confidence: Record<string, unknown> }
export interface StateChange {
  id: string; timestamp: string; symbol: string; trade_plan_id: string | null;
  decision_snapshot_id: string | null; event_type: string; importance: "INFO" | "WARNING" | "CRITICAL";
  summary: string; changes: { field: string; from: unknown; to: unknown }[];
  state_before: Record<string, unknown>; state_after: Record<string, unknown>; confidence_status: string;
}
export interface StateChangeMonitorStatus {
  enabled: boolean; running: boolean; interval_seconds: number; last_cycle_at: string | null;
  last_success_at: string | null; last_error: string | null;
}
export interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number; ema12: number; ema21: number }
export interface Chart { symbol: string; timeframe: string; candles: Candle[] }
export interface ChartStructure { id: string; symbol: string; timeframe: string; structure_type: "HORIZONTAL_ZONE" | "TRENDLINE"; label: string | null; lower_price: string | null; upper_price: string | null; anchor_one_time: number | null; anchor_one_price: string | null; anchor_two_time: number | null; anchor_two_price: string | null; active: boolean }
export interface FibDefinition { id: string; trade_plan_id: string; symbol: string; direction: "LONG" | "SHORT"; swing_low: number; swing_high: number; levels: Record<string, number> }
export interface RangeDefinition { id: string; trade_plan_id: string; symbol: string; range_low: number; range_high: number }
export interface Sizing { maximum_quantity: string; maximum_notional: string; permitted_risk_usdt: string; risk_per_unit_usdt: string; estimated_margin_usdt: string | null; minimum_leverage_for_margin_fit: string | null; group_risk_used_usdt: string; group_risk_remaining_usdt: string; risk_budget_usdt: string; stop_distance: string; margin_percent_equity: string | null; liquidation_status: string; stages: { state: string; allocation?: string; quantity?: string; notional?: string; stage_risk_usdt?: string; cumulative_quantity?: string; cumulative_risk_usdt?: string; remaining_unlockable_risk_usdt?: string; allowed: boolean; status: string; reasons: string[] }[] }

export type ScannerSetupType = "TREND_PULLBACK_LONG" | "TREND_PULLBACK_SHORT" | "RANGE_LONG" | "RANGE_SHORT" | "MACRO_BREAKOUT_LONG" | "MACRO_BREAKOUT_SHORT";
export type ScannerStatus = "IGNORE" | "WATCH" | "APPROACHING_LOCATION" | "AT_LOCATION" | "REACTION_DEVELOPING" | "TRIGGER_ARMED" | "APPROACHING_BREAKOUT" | "BREAKOUT_ATTEMPT" | "ACCEPTANCE_PENDING" | "BREAKOUT_ACCEPTED" | "RETEST_PENDING";
export type ScannerDataStatus = "CONFIRMED" | "PARTIAL" | "INDETERMINATE";
export interface ScannerMonitorStatus {
  enabled: boolean; running: boolean; interval_seconds: number;
  last_cycle_started_at: string | null; last_cycle_completed_at: string | null;
  last_success_at: string | null; last_error: string | null; last_cycle_duration_ms: number | null;
  watchlist_count: number; setup_count: number; evaluated_symbols: string[]; failed_symbols: string[];
}
export interface ScannerWatchlistItem {
  id: string; symbol: string; enabled: boolean; enabled_playbooks: string[];
  approach_tolerance_bps: number; retest_tolerance_bps: number; acceptance_bars: number;
  created_at: string; updated_at: string;
}
export interface ScannerWatchlistInput {
  symbol: string; enabled?: boolean; enabled_playbooks: string[];
  approach_tolerance_bps: number; retest_tolerance_bps: number; acceptance_bars: number;
}
export type ScannerWatchlistPatch = Partial<Omit<ScannerWatchlistInput, "symbol">>;
export interface ScannerSetupState extends Record<string, unknown> {
  symbol?: string; setup_type?: string; side?: string; status?: string; price?: number | string;
  evaluated_at?: string; context?: Record<string, unknown>; location?: Record<string, unknown>;
  reaction?: Record<string, unknown>; structure?: Record<string, unknown>;
  distance_bps?: number | string; qualifying_close_count?: number; required_acceptance_bars?: number;
  blocking_reasons?: string[]; next_conditions?: string[]; data_status?: ScannerDataStatus;
  accepted_at?: string; accepted_breakout_level?: number | string; accepted_close_count?: number;
  acceptance_bars?: number; accepted_structure_id?: string; accepted_side?: string;
}
export interface ScannerSetup {
  id: string; symbol: string; setup_type: ScannerSetupType; status: ScannerStatus;
  state: ScannerSetupState; version: number; trade_plan_id: string | null;
  last_evaluated_at: string | null; updated_at: string; created_at: string;
}
export interface ScannerTransition {
  id: string; watched_setup_id: string; symbol: string; setup_type: ScannerSetupType;
  from_status: ScannerStatus | null; to_status: ScannerStatus; timestamp: string;
  state_before: ScannerSetupState; state_after: ScannerSetupState; version: number;
}

export type TriggerState = "WAITING" | "DEVELOPING" | "RECLAIMED" | "RETEST_HELD" | "CONFIRMED" | "FAILED" | "INDETERMINATE";
export type TriggerPattern = "DEVIATION_RECLAIM" | "RETEST_HOLD";
export type TriggerArmSource = "ARM_TRANSITION" | "FIRST_OBSERVATION_BASELINE";
export type TriggerReferenceSource = "RANGE_LOW" | "RANGE_HIGH" | "FIB_ZONE_LOWER" | "FIB_ZONE_UPPER" | "ACCEPTED_BREAKOUT_LEVEL";
export interface TriggerResult {
  symbol: string; setup_type: ScannerSetupType; side: "long" | "short";
  pattern: TriggerPattern; state: TriggerState; reference_level: number;
  armed_at: string; evaluated_at: string; trigger_confirmed: boolean;
  anchor_bar_end_ms: number | null; confirmation_bar_end_ms: number | null;
  local_15m_acceptance: boolean | null; reaction_state: string | null;
  reaction_supportive: boolean | null; anchor_high: number | null; anchor_low: number | null;
  anchor_close: number | null; confirmation_close: number | null;
  evidence_present: string[]; evidence_missing: string[]; blocking_reasons: string[];
  next_conditions: string[];
}
export interface TriggerAttempt {
  id: string; watched_setup_id: string; symbol: string; setup_type: ScannerSetupType;
  arm_key: string; arm_source: TriggerArmSource; arm_transition_id: string | null;
  armed_at: string; reference_level: number; reference_source: TriggerReferenceSource;
  reference_metadata: Record<string, unknown>; retest_tolerance_bps: number;
  failure_tolerance_bps: number; state: TriggerState; result: TriggerResult; version: number;
  first_evaluated_at: string; last_evaluated_at: string; created_at: string; updated_at: string;
}
export interface TriggerTransition {
  id: string; trigger_attempt_id: string; symbol: string; setup_type: ScannerSetupType;
  from_state: TriggerState | null; to_state: TriggerState; timestamp: string;
  result_before: TriggerResult; result_after: TriggerResult; version: number;
}
export interface TriggerCurrent {
  watched_setup_id: string; symbol: string; setup_type: ScannerSetupType;
  scanner_status: ScannerStatus; eligible: boolean; blocking_reason: string | null;
  arm_key: string | null; arm_source: TriggerArmSource | null; armed_at: string | null;
  reference_level: number | null; reference_source: TriggerReferenceSource | null;
  attempt: TriggerAttempt | null; terminal: boolean;
}
export interface TriggerMonitorStatus {
  enabled: boolean; running: boolean; interval_seconds: number;
  last_cycle_started_at: string | null; last_cycle_completed_at: string | null;
  last_success_at: string | null; last_error: string | null; last_cycle_duration_ms: number | null;
  armed_candidate_count: number; eligible_candidate_count: number; evaluation_count: number;
  persisted_count: number; confirmed_count: number; terminal_count: number;
  evaluated_symbols: string[]; failed_symbols: string[];
}
