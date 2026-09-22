import { NextResponse } from "next/server";
import { authConfigured, authDisabled } from "@/lib/auth";

export function GET() {
  const ready = authDisabled() || authConfigured();
  return NextResponse.json({ status: ready ? "ok" : "configuration_required" }, { status: ready ? 200 : 503 });
}
