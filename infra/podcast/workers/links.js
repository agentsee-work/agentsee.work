/**
 * agentsee.work/p/<id>: one short link per post, so a click back from any
 * platform is attributable without a tracking parameter on the page. The id
 * is looked up in the KV namespace bound as LINKS, the click is written to
 * the Analytics Engine dataset bound as LOG, and the visitor is sent on
 * with a 302, which is never cached, so every click is counted and a
 * destination can be corrected after the post is up.
 *
 * A link is a KV entry whose key is the id (lower-case letters and digits,
 * two to sixteen of them) and whose value is JSON:
 *
 *   {"url": "https://agentsee.work/podcast/ep1/", "episode": "ep1",
 *    "post": "x-clip-02", "platform": "x"}
 *
 * Only url is required; a bare URL string works too. The other fields are
 * copied into the data point so clicks can be grouped without a join. The
 * publisher writes these when it creates a post; by hand, see README.md.
 *
 * What the data point holds: the id, the user agent, the country, the
 * referer's origin (the platform the click came from, which is the point),
 * the link's episode, post and platform, and the same keyed daily hash of
 * the network address as the media Worker, for the same reason. Unknown
 * ids are logged too, with status 404, so a mistyped link shows up.
 */

const ID = /^\/p\/([a-z0-9]{2,16})\/?$/;
const UA_MAX = 400;
const SITE = "https://agentsee.work";

let hmacKey = null;

async function hashAddress(address, secret) {
  if (!address) return "";
  const enc = new TextEncoder();
  if (!hmacKey) {
    hmacKey = crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" },
      false, ["sign"]);
  }
  const day = new Date().toISOString().slice(0, 10);
  const sig = await crypto.subtle.sign("HMAC", await hmacKey, enc.encode(`${day}|${address}`));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 24);
}

function refererOrigin(request) {
  const ref = request.headers.get("Referer");
  if (!ref) return "";
  try {
    return new URL(ref).origin;
  } catch {
    return "";
  }
}

function parseLink(raw) {
  if (!raw) return null;
  let link = null;
  try {
    link = JSON.parse(raw);
  } catch {
    link = { url: raw.trim() };
  }
  if (typeof link === "string") link = { url: link };
  if (!link || typeof link !== "object" || typeof link.url !== "string") return null;
  try {
    const u = new URL(link.url);
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  } catch {
    return null;
  }
  const s = (v) => (typeof v === "string" ? v.slice(0, 96) : "");
  return { url: link.url, episode: s(link.episode), post: s(link.post), platform: s(link.platform) };
}

/** The site's own 404 page, so a dead short link looks like the site. */
async function notFound() {
  const headers = { "Cache-Control": "no-store" };
  try {
    const res = await fetch(`${SITE}/404.html`, { headers: { "User-Agent": "podcast-links" } });
    if (res.ok) {
      const h = new Headers(res.headers);
      h.set("Cache-Control", "no-store");
      return new Response(res.body, { status: 404, headers: h });
    }
  } catch {
    // fall through to the plain answer
  }
  return new Response("not found", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8", ...headers } });
}

export default {
  async fetch(request, env, ctx) {
    const method = request.method;
    if (method !== "GET" && method !== "HEAD") {
      return new Response("method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const url = new URL(request.url);
    const m = ID.exec(url.pathname);
    const point = { id: m ? m[1] : url.pathname.slice(0, 96), status: 404, link: null, method };
    const log = (response) => {
      ctx.waitUntil(record(request, env, point));
      return response;
    };
    if (!m) {
      return log(await notFound());
    }
    const link = parseLink(await env.LINKS.get(m[1], { type: "text" }));
    if (!link) {
      return log(await notFound());
    }
    point.status = 302;
    point.link = link;
    return log(new Response(null, {
      status: 302,
      headers: { Location: link.url, "Cache-Control": "no-store", "X-Robots-Tag": "noindex" },
    }));
  },
};

async function record(request, env, p) {
  const cf = request.cf || {};
  const hash = await hashAddress(request.headers.get("CF-Connecting-IP") || "", env.HASH_KEY);
  const link = p.link || {};
  env.LOG.writeDataPoint({
    indexes: [p.id.slice(0, 96)],
    blobs: [
      p.id,
      (request.headers.get("User-Agent") || "").slice(0, UA_MAX),
      cf.country || "",
      hash,
      refererOrigin(request),
      link.episode || "",
      link.post || "",
      link.platform || "",
      p.method,
      cf.colo || "",
    ],
    doubles: [p.status, 1],
  });
}
