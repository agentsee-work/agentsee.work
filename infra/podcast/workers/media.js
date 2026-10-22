/**
 * media.agentsee.work: the public podcast files, served from the R2 bucket
 * and counted. Sits on a route in front of the bucket's custom domain, so
 * every request passes through here once; with the route gone the custom
 * domain serves the same files uncounted, which is the fallback.
 *
 * What it does with a request, in order: GET and HEAD only; the path is the
 * object key; conditional and range headers go to R2 as they are, and come
 * back as 304, 206 or 416 the way a web server would answer them; the
 * object's own headers (content type, cache control, from the upload) are
 * passed on, with an ETag, Last-Modified, Accept-Ranges and an open CORS
 * grant, because everything here is public. Nothing is cached by this
 * script: R2 reads are a fraction of a cent per thousand and every request
 * is meant to be seen.
 *
 * What it writes, one data point per request, to the Analytics Engine
 * dataset bound as LOG (README.md beside this file has the column map):
 * the key, the method, the status, the bytes the response carries, the
 * range asked for, the user agent, the country, the referer's origin, and
 * a one-way hash of the network address. The hash is HMAC-SHA256 under a
 * key that never leaves the Worker's bindings, over the UTC date and the
 * address, so the same listener hashes the same within a day and differently
 * across days: enough to count one listener once, which is what the IAB
 * rules want, and no more. The address itself goes nowhere; the privacy
 * notice on the site says exactly this.
 */

const UA_MAX = 400;
const RANGE_MAX = 64;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Range, If-None-Match, If-Modified-Since, If-Range",
  "Access-Control-Expose-Headers": "Accept-Ranges, Content-Length, Content-Range, ETag, Last-Modified",
  "Access-Control-Max-Age": "86400",
};

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

/** The span R2 actually returned, as [first, last] byte offsets. */
function span(range, size) {
  if (range.suffix != null) {
    const n = Math.min(range.suffix, size);
    return [size - n, size - 1];
  }
  const first = range.offset ?? 0;
  const last = range.length != null ? Math.min(first + range.length, size) - 1 : size - 1;
  return [first, last];
}

function keyOf(url) {
  let path;
  try {
    path = decodeURIComponent(url.pathname);
  } catch {
    return null;
  }
  const key = path.replace(/^\/+/, "");
  if (!key || key.endsWith("/") || key.split("/").some((p) => p === "" || p === "." || p === "..")) {
    return null;
  }
  return key;
}

function plain(status, text, extra = {}) {
  return new Response(text, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", ...CORS, ...extra },
  });
}

export default {
  async fetch(request, env, ctx) {
    const method = request.method;
    if (method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    if (method !== "GET" && method !== "HEAD") {
      return plain(405, "method not allowed", { Allow: "GET, HEAD, OPTIONS" });
    }
    const url = new URL(request.url);
    const key = keyOf(url);
    const point = {
      key: key ?? url.pathname.slice(0, 96),
      method,
      status: 0,
      bytes: 0,
      first: -1,
      last: -1,
      size: 0,
      partial: 0,
      type: "",
    };
    const log = (response) => {
      ctx.waitUntil(record(request, env, point));
      return response;
    };

    if (key === null) {
      point.status = 404;
      return log(plain(404, "not found"));
    }

    let object;
    try {
      object = method === "HEAD"
        ? await env.MEDIA.head(key)
        : await env.MEDIA.get(key, { range: request.headers, onlyIf: request.headers });
    } catch (e) {
      // R2 refuses a range that starts past the end; answer as a server would.
      const meta = await env.MEDIA.head(key);
      if (!meta) {
        point.status = 404;
        return log(plain(404, "not found"));
      }
      point.status = 416;
      point.size = meta.size;
      return log(plain(416, "range not satisfiable", { "Content-Range": `bytes */${meta.size}` }));
    }
    if (!object) {
      point.status = 404;
      return log(plain(404, "not found"));
    }

    // If-Range names an entity; a request carrying a different one wants the
    // whole file, which R2 does not know, so ask again without the range.
    const ifRange = request.headers.get("If-Range");
    if (method === "GET" && object.body && object.range && ifRange
        && ifRange !== object.httpEtag && ifRange !== object.uploaded.toUTCString()) {
      object = await env.MEDIA.get(key, { onlyIf: request.headers });
    }

    const headers = new Headers(CORS);
    object.writeHttpMetadata(headers);
    headers.set("ETag", object.httpEtag);
    headers.set("Last-Modified", object.uploaded.toUTCString());
    headers.set("Accept-Ranges", "bytes");
    headers.set("X-Content-Type-Options", "nosniff");
    point.size = object.size;
    point.type = headers.get("Content-Type") || "";

    if (method === "HEAD") {
      headers.set("Content-Length", String(object.size));
      point.status = 200;
      return log(new Response(null, { status: 200, headers }));
    }
    if (!object.body) {
      // A condition held: R2 sent the metadata and kept the body.
      const asks304 = request.headers.has("If-None-Match") || request.headers.has("If-Modified-Since");
      point.status = asks304 ? 304 : 412;
      headers.delete("Content-Type");
      return log(new Response(null, { status: point.status, headers }));
    }
    // A 206 is the answer to a Range request and nothing else. R2, handed the
    // request's headers, reports a range on the object whether or not the
    // request carried one, so a plain GET came back 206 with a Content-Range
    // of the whole file, which a strict fetcher (Spotify's) rejects as not
    // the file. The request decides.
    if (object.range && request.headers.has("Range")) {
      const [first, last] = span(object.range, object.size);
      headers.set("Content-Range", `bytes ${first}-${last}/${object.size}`);
      headers.set("Content-Length", String(last - first + 1));
      Object.assign(point, { status: 206, bytes: last - first + 1, first, last, partial: 1 });
      return log(new Response(object.body, { status: 206, headers }));
    }
    headers.set("Content-Length", String(object.size));
    Object.assign(point, { status: 200, bytes: object.size });
    return log(new Response(object.body, { status: 200, headers }));
  },
};

async function record(request, env, p) {
  const cf = request.cf || {};
  const hash = await hashAddress(request.headers.get("CF-Connecting-IP") || "", env.HASH_KEY);
  env.LOG.writeDataPoint({
    indexes: [p.key.slice(0, 96)],
    blobs: [
      p.key,
      p.method,
      (request.headers.get("User-Agent") || "").slice(0, UA_MAX),
      cf.country || "",
      hash,
      refererOrigin(request),
      (request.headers.get("Range") || "").slice(0, RANGE_MAX),
      p.type,
      cf.colo || "",
    ],
    doubles: [p.status, p.bytes, p.first, p.last, p.size, p.partial],
  });
}
