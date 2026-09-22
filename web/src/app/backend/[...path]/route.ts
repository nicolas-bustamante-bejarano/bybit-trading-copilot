import { NextRequest, NextResponse } from "next/server";

async function forward(request: NextRequest, context: RouteContext<"/backend/[...path]">) {
  const base = process.env.COPILOT_API_BASE_URL;
  if (!base) return NextResponse.json({ detail: "Backend service is not configured" }, { status: 503 });
  let configured: URL;
  try {
    configured = new URL(base);
  } catch {
    return NextResponse.json({ detail: "Backend service configuration is invalid" }, { status: 503 });
  }
  if (!['http:', 'https:'].includes(configured.protocol)) {
    return NextResponse.json({ detail: "Backend service configuration is invalid" }, { status: 503 });
  }
  const { path } = await context.params;
  const target = new URL(path.map(encodeURIComponent).join("/"), `${configured.toString().replace(/\/$/, "")}/`);
  target.search = request.nextUrl.search;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const response = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
    redirect: "manual",
  });
  const responseHeaders = new Headers();
  const responseType = response.headers.get("content-type");
  if (responseType) responseHeaders.set("content-type", responseType);
  return new NextResponse(response.body, { status: response.status, headers: responseHeaders });
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const DELETE = forward;
