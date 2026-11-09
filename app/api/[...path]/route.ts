// Runtime proxy: the browser talks only to the web origin; every /api/* request is
// forwarded to the FastAPI service (Render private network). Nothing here depends on
// build-time env. Runs per request on its own: it reads the request (Cache Components).
import type { NextRequest } from "next/server";

// Request headers the API needs. x-forwarded-for is passed through unchanged: Render's
// edge already puts the visitor's IP first, and a route handler has no socket IP to add.
const REQUEST_HEADERS = ["accept", "authorization", "content-type", "cookie", "x-forwarded-for"];
// Response headers worth returning (Set-Cookie is handled separately; encoding/length are
// recomputed because fetch already decoded the body).
const RESPONSE_HEADERS = ["content-type", "cache-control", "location", "www-authenticate"];

function apiBase(): string {
  const base = process.env.API_INTERNAL_BASE; // optional full-URL override (risks table)
  if (base) return base.replace(/\/+$/, "");
  return `http://${process.env.API_INTERNAL_HOSTPORT ?? "127.0.0.1:8000"}`;
}

async function proxy(req: NextRequest, ctx: RouteContext<"/api/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  const target = `${apiBase()}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = req.method !== "GET" && req.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return Response.json(
      { error: { code: "API_UNREACHABLE", message: "The API service can't be reached right now." } },
      { status: 502 },
    );
  }

  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  for (const cookie of upstream.headers.getSetCookie()) out.append("set-cookie", cookie);

  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
