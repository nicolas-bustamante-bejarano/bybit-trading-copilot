"use client";
import { useCallback } from "react";
import { RefreshCw, Wifi, WifiOff } from "lucide-react";
import { api } from "@/lib/api/client";
import type { Account, AccountStatus, Coach, LiveStatus, Portfolio } from "@/lib/api/types";
import { money, percent } from "@/lib/format";
import { usePolling } from "@/lib/api/use-polling";
import { PREVIEW_ACCOUNT, PREVIEW_PORTFOLIO, privateSyncLabel } from "@/lib/dashboard-preview";
import { PositionCoachCard } from "./position-coach-card";
import { StateChangeFeed } from "./state-change-feed";
import { StatusBadge } from "./status-badge";
import { EmptyState, ErrorState, Metric, PageHeader, Panel } from "./ui";

type Data = { account: Account; status: AccountStatus; portfolio: Portfolio; live: LiveStatus; coaches: Record<string, Coach | null>; changes: Awaited<ReturnType<typeof api.getStateChanges>> };
export function DashboardClient() {
  const loader = useCallback(async (): Promise<Data> => {
    const status = await api.getAccountStatus();
    const [live, changes] = await Promise.all([api.getLiveStatus(), api.getStateChanges(20)]);
    const [account, portfolio] = status.enabled ? await Promise.all([api.getAccount(), api.getPortfolio()]) : [PREVIEW_ACCOUNT, PREVIEW_PORTFOLIO];
    const coachResults = await Promise.all(account.positions.map(async p => { try { return [p.symbol, await api.getPositionCoach(p.symbol)] as const; } catch { return [p.symbol, null] as const; }}));
    return { account,status,portfolio,live,changes,coaches:Object.fromEntries(coachResults) };
  }, []);
  const { data, error, loading, refresh } = usePolling(loader, 5000);
  return <>
    <PageHeader eyebrow="PORTFOLIO COMMAND" title="Dashboard" detail="Live account exposure, structural risk, and evidence-based position coaching." action={<button onClick={()=>void refresh()} className="flex items-center gap-2 rounded border border-white/10 px-3 py-2 text-xs text-slate-400 hover:bg-white/5"><RefreshCw size={13}/>Refresh</button>}/>
    {error && !data && <ErrorState message={error}/>} {loading && !data && <div className="animate-pulse text-sm text-slate-600">Establishing read-only account state…</div>}
    {data && <div className="space-y-5">
      {!data.status.enabled && <div className="rounded border border-cyan-400/15 bg-cyan-400/[.04] p-3 text-xs text-cyan-100/75">Preview mode: private Bybit account sync is intentionally disabled. Public market and scanner components remain available.</div>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7"><Metric label="ACCOUNT EQUITY" value={data.status.enabled ? money(data.account.equity_usdt) : "—"}/><Metric label="AVAILABLE" value={data.status.enabled ? money(data.account.available_balance_usdt) : "—"}/><Metric label="KNOWN RISK" value={data.status.enabled ? money(data.portfolio.known_structural_risk_usdt) : "—"}/><Metric label="KNOWN RISK %" value={data.status.enabled ? percent(data.portfolio.known_structural_risk_pct) : "—"}/><Metric label="TOTAL RISK" value={data.status.enabled ? data.portfolio.risk_policy_status : "PREVIEW"}/><Metric label="BYBIT PRIVATE" value={privateSyncLabel(data.status)} tone={data.status.enabled && data.status.credentials_configured ? "text-emerald-300" : "text-cyan-300"}/><Metric label="LIVE STREAM" value={data.live.enabled ? "ENABLED" : "OFFLINE"} tone={data.live.enabled ? "text-emerald-300" : "text-slate-400"}/></div>
      <div className="grid gap-5 xl:grid-cols-[1fr_320px]"><div className="space-y-4"><div className="flex items-center justify-between"><h2 className="text-sm font-medium text-slate-300">Open positions</h2><span className="font-mono text-[10px] text-slate-600">{data.account.positions.length} ACTIVE</span></div>{data.account.positions.length ? data.account.positions.map(position => data.coaches[position.symbol] ? <PositionCoachCard key={position.symbol} position={position} coach={data.coaches[position.symbol]!}/> : <Panel key={position.symbol}><ErrorState message={`Coach evidence unavailable for ${position.symbol}`}/></Panel>) : <EmptyState title="No open positions" detail="The dashboard will populate when a read-only Bybit position is detected."/>}</div>
        <PortfolioRisk portfolio={data.portfolio} live={data.live}/></div>
      <StateChangeFeed events={data.changes}/>
    </div>}
  </>;
}
function PortfolioRisk({ portfolio, live }: { portfolio: Portfolio; live: LiveStatus }) { const knownPct=portfolio.known_structural_risk_pct; const state=portfolio.risk_policy_status; return <div className="space-y-4"><Panel title="Portfolio risk" kicker="CRYPTO_BETA"><div className="flex items-start justify-between"><div><div className="font-mono text-[9px] tracking-[.14em] text-slate-600">KNOWN STRUCTURAL RISK</div><div className="mt-1 text-2xl font-semibold text-white">{money(portfolio.known_structural_risk_usdt)}</div><div className="mt-1 text-xs text-slate-600">Known risk {percent(knownPct)} · total risk {portfolio.total_structural_risk_pct===null?"INDETERMINATE":percent(portfolio.total_structural_risk_pct)} · budget {percent(portfolio.risk_policy_pct)}</div></div><StatusBadge value={state}/></div>{knownPct!==null&&<div className="my-4 h-1 overflow-hidden rounded bg-slate-800"><div className={`h-full ${state==="BREACH"?"bg-red-400":"bg-cyan-400"}`} style={{width:`${Math.min(100,(knownPct/portfolio.risk_policy_pct)*100)}%`}}/></div>}<div className="rounded border border-amber-400/12 bg-amber-400/5 p-3"><div className="font-mono text-[9px] tracking-[.14em] text-amber-300">UNKNOWN / UNPROTECTED</div><div className="mt-2 text-xs text-slate-400">{portfolio.unknown_symbols.length ? portfolio.unknown_symbols.join(", ") : "none"}</div></div></Panel><Panel title="Connectivity" kicker="5S POLL"><div className="space-y-3 text-xs"><div className="flex justify-between text-slate-400"><span className="flex items-center gap-2">{live.enabled?<Wifi size={14}/>:<WifiOff size={14}/>}Public market stream</span><span>{live.active_symbols.length} active</span></div><div className="text-slate-600">Configured: {live.configured_symbols.join(", ") || "none"}</div></div></Panel></div> }
