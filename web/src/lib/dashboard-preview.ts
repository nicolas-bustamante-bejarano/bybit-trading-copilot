import type { Account, AccountStatus, Portfolio } from "./api/types";

export const PREVIEW_ACCOUNT: Account = {
  equity_usdt: 0, available_balance_usdt: null, max_group_risk_pct: 0.02,
  risk_budget_usdt: 0, total_structural_risk_usdt: 0, total_structural_risk_pct: 0,
  within_risk_budget: true, unprotected_symbols: [], positions: [],
};

export const PREVIEW_PORTFOLIO: Portfolio = {
  equity_usdt: 0, available_balance_usdt: null, risk_policy_pct: 0.02,
  risk_budget_usdt: 0, known_structural_risk_usdt: 0, known_structural_risk_pct: null,
  total_structural_risk_pct: null, risk_policy_status: "INDETERMINATE", unknown_symbols: [],
  provenance: { account: "private sync disabled — preview only" }, correlation_groups: {}, positions: [],
};

export function privateSyncLabel(status: AccountStatus): string {
  if (!status.enabled) return "PREVIEW / READ ONLY";
  return status.credentials_configured ? "CONNECTED" : "CREDENTIALS NEEDED";
}
