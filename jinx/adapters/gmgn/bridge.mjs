// JINX GMGN bridge.
// Runs GMGN's own OpenApiClient, compiled unmodified from the pinned upstream by
// scripts/build_gmgn_bridge.sh, and answers one JSON request per stdin line:
//   {"id": 1, "method": "getTokenInfo", "args": ["sol", "<mint>"]}
// Only read methods are enabled. Trading and state-changing methods are refused
// before any request is made.
//
// Portions translated from GMGNAI/gmgn-skills @ 402671e (MIT License, Copyright (c) 2025 GMGN;
// license in third_party/gmgn/LICENSE; see THIRD_PARTY_NOTICES.md): the escaped-newline handling
// of the private key and the default API host, both from src/config.ts.
import { createInterface } from "node:readline";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// GMGN_DEBUG makes the upstream client print full requests, API key included.
delete process.env.GMGN_DEBUG;

const build = process.env.JINX_GMGN_BUILD;
const { OpenApiClient } = await import(pathToFileURL(join(build, "client", "OpenApiClient.js")).href);

const READ_ONLY = new Set([
  "getTokenInfo", "getTokenSecurity", "getTokenPoolInfo", "getTokenTopHolders", "getTokenTopTraders",
  "getTokenKline", "getWalletHoldings", "getWalletActivity", "getWalletStats", "getWalletProfits",
  "getWalletTokenBalance", "getTrenches", "getTrendingSwaps", "getHotSearches", "searchMarket", "getUserInfo",
  "getFollowWallet", "getFollowTokens", "getFollowGroupNames", "getKol", "getSmartMoney", "getCreatedTokens",
  "getCalloutRecord", "getCalloutGlobal", "quoteOrder", "queryOrder", "getGasPrice", "getStrategyOrders",
  "getCookingStatistics", "getTokenSignalV2",
]);

function credentials() {
  let apiKey = process.env.GMGN_API_KEY;
  let privateKey = process.env.GMGN_PRIVATE_KEY;
  // Fall back to the file `gmgn-cli config --apply` writes.
  const file = join(process.env.HOME || "", ".config", "gmgn", ".env");
  if (!apiKey && existsSync(file)) {
    for (const line of readFileSync(file, "utf-8").split("\n")) {
      const eq = line.indexOf("=");
      if (eq < 0) continue;
      const key = line.slice(0, eq).trim();
      const value = line.slice(eq + 1).trim();
      if (key === "GMGN_API_KEY") apiKey = value;
      if (key === "GMGN_PRIVATE_KEY") privateKey = value;
    }
  }
  // Withheld unless the adapter allows it: with the request-signing key, the API key can submit trades.
  if (process.env.JINX_GMGN_NO_SIGNING_KEY === "1") privateKey = undefined;
  // SRC: gmgn-skills@402671e src/config.ts:62 (single-line .env values carry escaped newlines)
  if (privateKey) privateKey = privateKey.replace(/\\n/g, "\n");
  return { apiKey, privateKey };
}

const { apiKey, privateKey } = credentials();
// SRC: gmgn-skills@402671e src/config.ts:67
// Observe GMGN's own rate-limit headers on every reply the pinned client receives. The client keeps using the
// real fetch; only header names starting with x-ratelimit- are copied, never request data.
let lastRateLimit = null;
const realFetch = globalThis.fetch;
globalThis.fetch = async (...fetchArgs) => {
  const res = await realFetch(...fetchArgs);
  const seen = {};
  res.headers.forEach((value, name) => {
    if (name.toLowerCase().startsWith("x-ratelimit-")) seen[name.toLowerCase()] = value;
  });
  if (Object.keys(seen).length) lastRateLimit = seen;
  return res;
};

const host = process.env.JINX_GMGN_HOST || "https://openapi.gmgn.ai";
const client = apiKey ? new OpenApiClient({ apiKey, privateKeyPem: privateKey, host }) : null;

const reply = (msg) => process.stdout.write(JSON.stringify(msg) + "\n");

createInterface({ input: process.stdin })
  .on("line", async (line) => {
    let req;
    try {
      req = JSON.parse(line);
    } catch {
      return reply({ id: null, ok: false, error: { code: "BAD_REQUEST", message: "request is not JSON" } });
    }
    const { id, method, args = [] } = req;
    if (method === "__health") {
      return reply({ id, ok: true, result: { configured: client !== null, signing: Boolean(privateKey), rate_limit: lastRateLimit } });
    }
    if (!READ_ONLY.has(method)) {
      return reply({ id, ok: false, error: { code: "METHOD_NOT_ENABLED", message: `${method} is not enabled` } });
    }
    if (!client) {
      return reply({ id, ok: false, error: { code: "NOT_CONFIGURED", message: "GMGN API key is not configured" } });
    }
    try {
      reply({ id, ok: true, result: await client[method](...args) });
    } catch (e) {
      // GMGN's rate-limit identity and reset time (OpenApiError.apiError / resetAtUnix) are passed on so JINX can
      // stop sending until the ban lifts instead of extending it.
      reply({ id, ok: false, error: { code: String(e?.apiCode ?? e?.status ?? "GMGN_ERROR"), message: String(e?.message ?? e),
                                      api_error: e?.apiError ?? null, reset_at: e?.resetAtUnix ?? null } });
    }
  })
  .on("close", () => process.exit(0));
