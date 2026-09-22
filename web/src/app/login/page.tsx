export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const query = await searchParams;
  return <div className="mx-auto grid min-h-[70vh] max-w-md place-items-center"><div className="w-full rounded-md border border-white/10 bg-[#0d121a] p-7">
    <div className="font-mono text-[10px] tracking-[.18em] text-cyan-400">PRIVATE WORKSTATION</div>
    <h1 className="mt-3 text-2xl font-semibold text-white">Trading Copilot</h1>
    <p className="mt-2 text-sm text-slate-500">Enter the workstation password to continue.</p>
    {query.error && <div className="mt-4 rounded border border-red-500/20 bg-red-500/5 p-3 text-sm text-red-300">Invalid password.</div>}
    <form action="/api/auth/login" method="post" className="mt-6 space-y-4">
      <input type="hidden" name="next" value={typeof query.next === "string" ? query.next : "/"}/>
      <label className="block text-xs text-slate-400">Password<input name="password" type="password" required autoComplete="current-password" className="mt-2 w-full rounded border border-white/10 bg-[#080b10] px-3 py-2.5 text-slate-200 outline-none focus:border-cyan-400/40"/></label>
      <button type="submit" className="w-full rounded bg-cyan-300 px-4 py-2.5 text-sm font-semibold text-slate-950 hover:bg-cyan-200">Sign in</button>
    </form>
  </div></div>;
}
