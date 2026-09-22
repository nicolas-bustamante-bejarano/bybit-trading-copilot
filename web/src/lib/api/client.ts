import type { Account, AccountStatus, Coach, ExecutionEvent, Health, LiveStatus, Portfolio, Snapshot, StateChange, StateChangeMonitorStatus, TradePlan, TradeReview } from "./types";

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
};
