import { NextRequest, NextResponse } from "next/server";
import { createSessionToken, SESSION_COOKIE, verifyPassword } from "@/lib/auth";

export async function POST(request: NextRequest) {
  const form = await request.formData();
  const password = String(form.get("password") ?? "");
  const next = String(form.get("next") ?? "/");
  if (!(await verifyPassword(password))) {
    return new NextResponse(null, { status: 303, headers: { location: "/login?error=1" } });
  }
  const destination = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  const response = new NextResponse(null, { status: 303, headers: { location: destination } });
  response.cookies.set(SESSION_COOKIE, await createSessionToken(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 60 * 60 * 12,
  });
  return response;
}
