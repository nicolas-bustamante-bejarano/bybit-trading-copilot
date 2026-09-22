"use client";
import { useCallback } from "react";
import { api } from "@/lib/api/client";
import { usePolling } from "@/lib/api/use-polling";
import { StatusBadge } from "./status-badge";
import { ErrorState, PageHeader, Panel } from "./ui";

export function SystemClient() {
  const loader = useCallback(async () => {
    const [health, status, live, account, monitor] = await Promise.all([
      api.getHealth(), api.getAccountStatus(), api.getLiveStatus(), api.getAccount(),
      api.getStateChangeMonitorStatus(),
    ]);
    return { health, status, live, account, monitor };
  }, []);
  const { data, error } = usePolling(loader, 5000);
  return <><PageHeader eyebrow="OPERATIONAL STATE" title="System" detail="Health and read-only connectivity. Credential values and authentication headers are never exposed."/>
    {error && !data && <ErrorState message={error}/>} {data && <div className="grid gap-5 md:grid-cols-2">
      <Panel title="FastAPI"><Rows rows={[["Health",data.health.status,"CONFIRMED"],["Account data",`${data.account.positions.length} open positions`,"CONFIRMED"],["Mode",data.status.mode,"CONFIRMED"]]}/></Panel>
      <Panel title="Bybit private integration"><Rows rows={[["Read-only sync",data.status.enabled?"Enabled":"Disabled",data.status.enabled?"CONFIRMED":"MISSING"],["Credentials configured",data.status.credentials_configured?"Yes":"No",data.status.credentials_configured?"CONFIRMED":"MISSING"],["Browser credentials","Never exposed","CONFIRMED"]]}/></Panel>
      <Panel title="Public market stream"><Rows rows={[["Collector",data.live.enabled?"Enabled":"Disabled",data.live.enabled?"CONFIRMED":"MISSING"],["Active symbols",data.live.active_symbols.join(", ")||"Warming up",data.live.active_symbols.length?"CONFIRMED":"PARTIAL"],["Configured symbols",data.live.configured_symbols.join(", "),"CONFIRMED"]]}/></Panel>
      <Panel title="State Change Monitor"><Rows rows={[["Configuration",data.monitor.enabled?"Enabled":"Disabled",data.monitor.enabled?"CONFIRMED":"MISSING"],["Worker",data.monitor.running?"Running":"Stopped",data.monitor.running?"CONFIRMED":"MISSING"],["Interval",`${data.monitor.interval_seconds}s`,"CONFIRMED"],["Last successful cycle",data.monitor.last_success_at?new Date(data.monitor.last_success_at).toLocaleString():"Never",data.monitor.last_success_at?"CONFIRMED":"MISSING"],["Last error",data.monitor.last_error??"None",data.monitor.last_error?"BREACH":"CONFIRMED"]]}/></Panel>
      <Panel title="Safety boundary"><div className="space-y-2 text-sm text-slate-400"><p>No order placement or mutation methods.</p><p>No transfer or withdrawal capability.</p><p>All exchange connectivity remains server-side.</p><p>Manual execution is required.</p></div></Panel>
    </div>}
  </>;
}
function Rows({rows}:{rows:[string,string,string][]}){return <div>{rows.map(([name,value,status])=><div key={name} className="flex items-center justify-between gap-4 border-b border-white/7 py-3 last:border-0"><div><div className="text-xs text-slate-500">{name}</div><div className="mt-1 text-sm text-slate-300">{value}</div></div><StatusBadge value={status}/></div>)}</div>}
