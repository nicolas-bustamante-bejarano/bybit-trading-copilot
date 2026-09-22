"use client";
import { Activity, BookOpen, FlaskConical, LayoutDashboard, ListTree, Server, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
const links = [
  ["/", "Dashboard", LayoutDashboard], ["/positions", "Positions", Activity], ["/trade-plans", "Trade Plans", ListTree],
  ["/journal", "Journal", BookOpen], ["/system", "System", Server], ["/research-lab", "Research Lab", FlaskConical],
] as const;
export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  if (path === "/login") return <div className="min-h-screen bg-[#080b10] p-4 text-slate-200">{children}</div>;
  return <div className="min-h-screen bg-[#080b10] text-slate-200">
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-white/8 bg-[#0b0f15] lg:block">
      <div className="flex h-16 items-center gap-3 border-b border-white/8 px-5"><div className="grid size-8 place-items-center rounded bg-cyan-400/12 text-cyan-300"><ShieldCheck size={18}/></div><div><div className="text-sm font-semibold tracking-wide">TRADING COPILOT</div><div className="font-mono text-[9px] tracking-[.2em] text-slate-500">READ-ONLY WORKSTATION</div></div></div>
      <nav className="space-y-1 p-3">{links.map(([href,name,Icon]) => { const active=href === "/" ? path === "/" : path.startsWith(href); return <Link key={href} href={href} className={`flex items-center gap-3 rounded px-3 py-2.5 text-sm transition ${active ? "bg-cyan-400/10 text-cyan-200" : "text-slate-400 hover:bg-white/5 hover:text-slate-200"}`}><Icon size={16}/><span>{name}</span>{name==="Research Lab"&&<span className="ml-auto font-mono text-[8px] text-slate-600">SOON</span>}</Link>})}</nav>
      <div className="absolute bottom-5 left-5 right-5 rounded border border-white/8 bg-white/[.02] p-3 text-xs text-slate-500"><div className="mb-1 flex items-center gap-2 text-emerald-300"><span className="size-1.5 rounded-full bg-emerald-400"/>Manual execution only</div>No exchange actions are available.</div>
    </aside>
    <div className="lg:pl-60"><header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-white/8 bg-[#080b10]/90 px-4 backdrop-blur md:px-7"><div className="lg:hidden text-sm font-semibold">TRADING COPILOT</div><div className="hidden font-mono text-[10px] tracking-[.16em] text-slate-500 lg:block">REGIME → LOCATION → REACTION → EXECUTION → REVIEW</div><div className="flex items-center gap-3"><div className="flex items-center gap-2 font-mono text-[10px] text-slate-500"><span className="size-1.5 rounded-full bg-emerald-400"/>READ ONLY</div><form action="/api/auth/logout" method="post"><button className="text-xs text-slate-600 hover:text-slate-300">Log out</button></form></div></header><nav className="flex gap-1 overflow-x-auto border-b border-white/8 bg-[#0b0f15] p-2 lg:hidden">{links.slice(0,5).map(([href,name])=><Link key={href} href={href} className={`shrink-0 rounded px-3 py-2 text-xs ${path===href?"bg-cyan-400/10 text-cyan-200":"text-slate-500"}`}>{name}</Link>)}</nav><main className="p-4 md:p-7">{children}</main></div>
  </div>;
}
