import type { Account, AccountStatus, Chart, ChartStructure, Coach, ExecutionEvent, Health, LiveStatus, Portfolio, ScannerMonitorStatus, ScannerSetup, ScannerTransition, ScannerWatchlistInput, ScannerWatchlistItem, ScannerWatchlistPatch, Sizing, Snapshot, StateChange, StateChangeMonitorStatus, TradePlan, TradeReview, TriggerAttempt, TriggerCurrent, TriggerMonitorStatus, TriggerTransition } from "./types";

const API_BASE = "/backend";

export class ApiError extends Error {
  constructor(public path: string, public status: number, message: string) { super(message); }
}

async function get<T>(path: string): Promise<T> {
  let response: Response;
  try { response = await fetch(`${API_BASE}${path}`, { cache: "no-store" }); }
  catch { throw new ApiError(path, 0, "FastAPI is unreachable"); }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new ApiError(path, response.status, detail.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
async function mutate<T>(path: string, method: "POST" | "PATCH" | "PUT" | "DELETE", body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { method, cache: "no-store", headers: body ? { "content-type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new ApiError(path, response.status, detail.detail ?? `Request failed (${response.status})`); }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
function query(filters: Record<string, string | number | undefined>): string {
  return new URLSearchParams(Object.entries(filters).filter((entry): entry is [string, string | number] => entry[1] !== undefined).map(([key, value]) => [key, String(value)])).toString();
}

export const api = {
  getHealth: () => get<Health>("/health"),
  getAccountStatus: () => get<AccountStatus>("/account/status"),
  getAccount: () => get<Account>("/account/normalized"),
  getPortfolio: () => get<Portfolio>("/portfolio/live"),
  getLiveStatus: () => get<LiveStatus>("/live/status"),
  getPositionCoach: (symbol: string) => get<Coach>(`/positions/${encodeURIComponent(symbol)}/coach`),
  getTradePlans: () => get<TradePlan[]>("/trade-plans"),
  getTradePlan: (id: string) => get<TradePlan>(`/trade-plans/${encodeURIComponent(id)}`),
  getSnapshots: (id: string) => get<Snapshot[]>(`/trade-plans/${encodeURIComponent(id)}/snapshots`),
  getExecutionEvents: (id: string) => get<ExecutionEvent[]>(`/trade-plans/${encodeURIComponent(id)}/execution-events`),
  getTradeReview: (id: string) => get<TradeReview>(`/trade-plans/${encodeURIComponent(id)}/review`),
  getStateChanges: (limit = 50) => get<StateChange[]>(`/state-changes?limit=${limit}`),
  getStateChangeMonitorStatus: () => get<StateChangeMonitorStatus>("/state-change-monitor/status"),
  getChart: (symbol: string, timeframe: string) => get<Chart>(`/market/${encodeURIComponent(symbol)}/chart?timeframe=${timeframe}`),
  getChartStructures: (symbol: string) => get<ChartStructure[]>(`/chart-structures?symbol=${encodeURIComponent(symbol)}`),
  createChartStructure: (body: Record<string, unknown>) => mutate<ChartStructure>("/chart-structures", "POST", body),
  patchChartStructure: (id: string, body: Record<string, unknown>) => mutate<ChartStructure>(`/chart-structures/${encodeURIComponent(id)}`, "PATCH", body),
  deleteChartStructure: (id: string) => mutate<void>(`/chart-structures/${encodeURIComponent(id)}`, "DELETE"),
  createPlan: (body: Record<string, unknown>) => mutate<TradePlan>("/trade-plans", "POST", body),
  patchPlan: (id: string, body: Record<string, unknown>) => mutate<TradePlan>(`/trade-plans/${encodeURIComponent(id)}`, "PATCH", body),
  getFibs: (id: string) => get<Record<string, unknown>[]>(`/trade-plans/${encodeURIComponent(id)}/fib-definitions`),
  putFib: (id: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(`/trade-plans/${encodeURIComponent(id)}/fib-definition`, "PUT", body),
  getRanges: (id: string) => get<Record<string, unknown>[]>(`/trade-plans/${encodeURIComponent(id)}/range-definitions`),
  putRange: (id: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(`/trade-plans/${encodeURIComponent(id)}/range-definition`, "PUT", body),
  getSizing: (body: Record<string, unknown>) => mutate<Sizing>("/workspace/sizing", "POST", body),
  getScannerStatus: () => get<ScannerMonitorStatus>("/scanner/status"),
  getScannerWatchlist: () => get<ScannerWatchlistItem[]>("/scanner/watchlist"),
  createScannerWatchlistItem: (body: ScannerWatchlistInput) => mutate<ScannerWatchlistItem>("/scanner/watchlist", "POST", body),
  patchScannerWatchlistItem: (symbol: string, body: ScannerWatchlistPatch) => mutate<ScannerWatchlistItem>(`/scanner/watchlist/${encodeURIComponent(symbol)}`, "PATCH", body),
  deleteScannerWatchlistItem: (symbol: string) => mutate<void>(`/scanner/watchlist/${encodeURIComponent(symbol)}`, "DELETE"),
  getScannerSetups: (filters: { symbol?: string; setup_type?: string; status?: string; limit?: number } = {}) => get<ScannerSetup[]>(`/scanner/setups?${new URLSearchParams(Object.entries(filters).filter((entry): entry is [string, string | number] => entry[1] !== undefined).map(([key, value]) => [key, String(value)])).toString()}`),
  getScannerSetup: (symbol: string, setupType: string) => get<ScannerSetup>(`/scanner/setups/${encodeURIComponent(symbol)}/${encodeURIComponent(setupType)}`),
  getScannerTransitions: (filters: { symbol?: string; setup_type?: string; watched_setup_id?: string; limit?: number } = {}) => get<ScannerTransition[]>(`/scanner/transitions?${new URLSearchParams(Object.entries(filters).filter((entry): entry is [string, string | number] => entry[1] !== undefined).map(([key, value]) => [key, String(value)])).toString()}`),
  getTriggerStatus: () => get<TriggerMonitorStatus>("/trigger/status"),
  getCurrentTriggers: (filters: { symbol?: string; setup_type?: string; watched_setup_id?: string; limit?: number } = {}) => get<TriggerCurrent[]>(`/trigger/current?${query(filters)}`),
  getTriggerAttempts: (filters: { symbol?: string; setup_type?: string; state?: string; watched_setup_id?: string; arm_key?: string; limit?: number } = {}) => get<TriggerAttempt[]>(`/trigger/attempts?${query(filters)}`),
  getTriggerAttempt: (id: string) => get<TriggerAttempt>(`/trigger/attempts/${encodeURIComponent(id)}`),
  getTriggerTransitions: (filters: { trigger_attempt_id?: string; symbol?: string; setup_type?: string; to_state?: string; limit?: number } = {}) => get<TriggerTransition[]>(`/trigger/transitions?${query(filters)}`),
};
