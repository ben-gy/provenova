// Bindings and vars from wrangler.jsonc, plus secrets set with `wrangler secret put`.
// (Hand-written: `wrangler types` needs a local workerd, so keep this in sync.)
interface Env {
  PROVENOVA: DurableObjectNamespace<import("./index").ProvenovaContainer>;
  QL_DEPLOYMENT: string;
  QL_BASE_URL: string;
  QL_PUBLIC_CARDS: string;
  QL_ADMIN_EMAIL: string;
  CLOUDFLARE_ACCOUNT_ID: string;
  LITESTREAM_BUCKET: string;
  LITESTREAM_PATH: string;
  QL_SECRET_KEY: string;
  QL_ATTESTATION_KEY_B64: string;
  LITESTREAM_ACCESS_KEY_ID: string;
  LITESTREAM_SECRET_ACCESS_KEY: string;
}

declare namespace Cloudflare {
  interface Env extends globalThis.Env {}
}
