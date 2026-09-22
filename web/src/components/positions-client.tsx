"use client";
import { useCallback, useState } from "react";
import { api } from "@/lib/api/client";
import type { Account, Coach } from "@/lib/api/types";
import { usePolling } from "@/lib/api/use-polling";
import { PositionCoachCard } from "./position-coach-card";
import { EmptyState, ErrorState, PageHeader } from "./ui";
export function PositionsClient() {
  const [expanded,setExpanded]=useState<string|null>(null);
  const loader=useCallback(async()=>{ const account=await api.getAccount(); const entries=await Promise.all(account.positions.map(async p=>[p.symbol,await api.getPositionCoach(p.symbol)] as const)); return {account,coaches:Object.fromEntries(entries) as Record<string,Coach>};},[]);
  const {data,error}=usePolling<{account:Account;coaches:Record<string,Coach>}>(loader,5000);
  return <><PageHeader eyebrow="POSITION CONTROL" title="Positions" detail="Current Bybit positions with lifecycle confidence, structural risk, and expanded coach evidence."/>{error&&!data&&<ErrorState message={error}/>}<div className="space-y-4">{data?.account.positions.length ? data.account.positions.map(p=><button key={p.symbol} onClick={()=>setExpanded(expanded===p.symbol?null:p.symbol)} className="block w-full text-left"><PositionCoachCard position={p} coach={data.coaches[p.symbol]} expanded={expanded===p.symbol}/></button>) : <EmptyState title="No open positions" detail="No current read-only Bybit positions were found."/>}</div></>;
}
