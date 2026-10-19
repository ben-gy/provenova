/**
 * Provenova on Cloudflare: a Worker in front of one Container running the
 * FastAPI app (deploy/cloudflare/Dockerfile).
 *
 * - Every request goes to a single, named container instance. The database is
 *   SQLite replicated to R2 by Litestream, which supports exactly one writer,
 *   so the app must never run as two instances at once (max_instances: 1).
 * - Public, cookie-free assets (badges, /static) are cached at the edge so
 *   README badge traffic doesn't keep waking the container.
 * - The real client IP is forwarded as X-Forwarded-For for the app's rate
 *   limiter (uvicorn runs with --proxy-headers).
 * - A daily cron (wrangler.jsonc "triggers") wakes the container to run the
 *   data collectors (POST /internal/collect, authorised by QL_COLLECT_TOKEN),
 *   so new benchmark and calibration data lands without a redeploy.
 */
import { Container, getContainer } from "@cloudflare/containers";
import { env as workerEnv } from "cloudflare:workers";

const APP_PORT = 8000;

// Container env: plain vars plus secrets. Optional integrations are passed only
// when set, so an unset secret never shadows the app's own default.
const PASSTHROUGH = [
  "QL_DEPLOYMENT",
  "QL_BASE_URL",
  "QL_PUBLIC_CARDS",
  "QL_ADMIN_EMAIL",
  "QL_DOI_PROVIDER",
  "QL_ZENODO_ENDPOINT",
  "QL_DATACITE_ENDPOINT",
  "QL_DATACITE_REPOSITORY_ID",
  "QL_DATACITE_PREFIX",
  "QL_SMTP_HOST",
  "QL_SMTP_PORT",
  "QL_SMTP_USER",
  "QL_SMTP_FROM",
  "CLOUDFLARE_ACCOUNT_ID",
  "LITESTREAM_BUCKET",
  "LITESTREAM_PATH",
  // secrets
  "QL_SECRET_KEY",
  "QL_ATTESTATION_KEY_B64",
  "QL_ZENODO_TOKEN",
  "QL_DATACITE_PASSWORD",
  "QL_SMTP_PASSWORD",
  "QL_INDEXNOW_KEY",
  "QL_GITHUB_TOKEN",
  "QL_COLLECT_TOKEN",
  "LITESTREAM_ACCESS_KEY_ID",
  "LITESTREAM_SECRET_ACCESS_KEY",
] as const;

function containerEnv(): Record<string, string> {
  const source = workerEnv as unknown as Record<string, unknown>;
  const out: Record<string, string> = {};
  for (const key of PASSTHROUGH) {
    const value = source[key];
    if (typeof value === "string" && value !== "") out[key] = value;
  }
  return out;
}

export class ProvenovaContainer extends Container<Env> {
  override defaultPort = APP_PORT;
  // Sleep soon after the last request (billing stops); Litestream syncs on SIGTERM.
  override sleepAfter = "5m";
  // Outbound access for Zenodo/DataCite, IndexNow, GitHub (Metriq) and SMTP.
  override enableInternet = true;
  override envVars = containerEnv();

  override async fetch(request: Request): Promise<Response> {
    // A cold start restores the DB from R2 and imports Qiskit before uvicorn
    // binds, which can exceed the library's 20s default port wait.
    const { status } = await this.getState();
    if (!this.ctx.container?.running || status !== "healthy") {
      try {
        await this.startAndWaitForPorts({
          ports: APP_PORT,
          cancellationOptions: {
            abort: request.signal,
            instanceGetTimeoutMS: 30_000,
            portReadyTimeoutMS: 120_000,
          },
        });
      } catch (err) {
        console.error("container start failed", err);
        return new Response("Provenova is starting up. Please retry in a few seconds.", {
          status: 503,
          headers: { "Retry-After": "5", "Content-Type": "text/plain; charset=utf-8" },
        });
      }
    }
    return this.containerFetch(request, APP_PORT);
  }

  override onError(error: unknown): void {
    console.error("container error", error);
  }
}

// Paths whose responses are public and identical for every visitor.
const EDGE_CACHEABLE = [/^\/badge\//, /^\/static\//];
const STATIC_MAX_AGE = 60 * 60; // /static has no Cache-Control from the app

function isEdgeCacheable(request: Request, url: URL): boolean {
  return (
    (request.method === "GET" || request.method === "HEAD") &&
    EDGE_CACHEABLE.some((re) => re.test(url.pathname))
  );
}

function forwardedRequest(request: Request, dropCookies: boolean): Request {
  const headers = new Headers(request.headers);
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) headers.set("X-Forwarded-For", ip);
  headers.set("X-Forwarded-Proto", new URL(request.url).protocol.replace(":", ""));
  if (dropCookies) headers.delete("Cookie");
  return new Request(request, { headers });
}

async function toContainer(request: Request, env: Env): Promise<Response> {
  return getContainer(env.PROVENOVA, "primary").fetch(request);
}

// Runs the due data collectors inside the container. Collectors decide for
// themselves what is due (Metriq daily, IBM and the experiments dataset weekly).
async function runCollectors(env: Env): Promise<void> {
  if (!env.QL_COLLECT_TOKEN) {
    console.error("collect: QL_COLLECT_TOKEN is not set; skipping");
    return;
  }
  const request = new Request(`${env.QL_BASE_URL}/internal/collect`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.QL_COLLECT_TOKEN}` },
  });
  const response = await toContainer(request, env);
  const body = await response.text();
  if (response.ok) console.log("collect ok", body);
  else console.error("collect failed", response.status, body.slice(0, 2000));
}

export default {
  async scheduled(_controller, env, ctx): Promise<void> {
    ctx.waitUntil(runCollectors(env));
  },

  async fetch(request, env, ctx): Promise<Response> {
    const url = new URL(request.url);

    if (!isEdgeCacheable(request, url)) {
      return toContainer(forwardedRequest(request, false), env);
    }

    // Cookie-less request so the app never attaches a session cookie, which
    // keeps the response shareable.
    const cache = caches.default;
    const cacheKey = new Request(url.toString(), { method: "GET" });
    const hit = await cache.match(cacheKey);
    if (hit) return hit;

    const origin = await toContainer(forwardedRequest(request, true), env);
    if (origin.status !== 200 || origin.headers.has("Set-Cookie")) return origin;

    const response = new Response(origin.body, origin);
    if (!response.headers.has("Cache-Control")) {
      response.headers.set("Cache-Control", `public, max-age=${STATIC_MAX_AGE}`);
    }
    if (request.method === "GET") ctx.waitUntil(cache.put(cacheKey, response.clone()));
    return response;
  },
} satisfies ExportedHandler<Env>;
