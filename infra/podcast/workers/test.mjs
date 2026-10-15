// The two edge Workers under Node, against a fake bucket, KV and log:
//
//   node infra/podcast/workers/test.mjs
//
// No dependencies: Node has Request, Response, Headers and WebCrypto, which
// is all the Workers use. What is faked is what Cloudflare binds (R2, KV,
// Analytics Engine) and request.cf, so this checks the Workers' own logic
// (keys, ranges, conditionals, the hash, what goes in a data point), not the
// platform. The first apply checks the platform.
import assert from "node:assert/strict";
const media = (await import("./media.js")).default;
const links = (await import("./links.js")).default;

const bytes = new Uint8Array(1000).map((_, i) => i % 251);
const OBJ = {
  "podcast/ep1/episode.mp3": { body: bytes, etag: "abc", type: "audio/mpeg", cache: "public, max-age=86400", uploaded: new Date("2026-10-14T19:52:00Z") },
  "podcast/ep1/a b.vtt": { body: new TextEncoder().encode("WEBVTT\n"), etag: "v1", type: "text/vtt", cache: null, uploaded: new Date("2026-10-14T19:52:00Z") },
};
class Bucket {
  constructor() { this.calls = []; }
  meta(k, extra = {}) {
    const x = OBJ[k];
    return { key: k, size: x.body.length, etag: x.etag, httpEtag: `"${x.etag}"`, uploaded: x.uploaded,
      writeHttpMetadata(h) { h.set("Content-Type", x.type); if (x.cache) h.set("Cache-Control", x.cache); }, ...extra };
  }
  async head(k) { this.calls.push(["head", k]); return OBJ[k] ? this.meta(k) : null; }
  async get(k, opts = {}) {
    this.calls.push(["get", k, opts.range instanceof Headers ? opts.range.get("Range") : null]);
    const x = OBJ[k]; if (!x) return null;
    const h = opts.onlyIf instanceof Headers ? opts.onlyIf : new Headers();
    const inm = h.get("If-None-Match"); if (inm && inm === `"${x.etag}"`) return this.meta(k, { body: undefined });
    const im = h.get("If-Match"); if (im && im !== `"${x.etag}"`) return this.meta(k, { body: undefined });
    let range; const rh = opts.range instanceof Headers ? opts.range.get("Range") : null;
    if (rh) {
      const m = /^bytes=(\d*)-(\d*)$/.exec(rh);
      if (m) {
        if (m[1] === "" && m[2] !== "") range = { suffix: +m[2] };
        else if (m[1] !== "") {
          const off = +m[1]; if (off >= x.body.length) throw new Error("range not satisfiable");
          range = m[2] !== "" ? { offset: off, length: Math.min(+m[2], x.body.length - 1) - off + 1 } : { offset: off };
        }
      }
    }
    let body = x.body;
    if (range) {
      const [a, b] = range.suffix != null ? [Math.max(0, x.body.length - range.suffix), x.body.length - 1]
        : [range.offset, range.length != null ? range.offset + range.length - 1 : x.body.length - 1];
      body = x.body.slice(a, b + 1);
    }
    return this.meta(k, { body, range });
  }
}
const log = { points: [], writeDataPoint(p) { this.points.push(p); } };
const ctx = () => ({ ps: [], waitUntil(p) { this.ps.push(p); } });
const bucket = new Bucket();
const env = { MEDIA: bucket, LOG: log, HASH_KEY: "k".repeat(64) };

async function call(worker, e, url, init = {}) {
  const c = ctx();
  const before = log.points.length;
  const res = await worker.fetch(new Request(url, init), e, c);
  await Promise.all(c.ps);
  return { res, point: log.points.length > before ? log.points[log.points.length - 1] : null };
}
const M = "https://media.agentsee.work/podcast/ep1/episode.mp3";
const H = { "User-Agent": "Overcast/2026 (+http://overcast.fm/; iOS)", "CF-Connecting-IP": "203.0.113.7", Referer: "https://agentsee.work/podcast/ep1/?x=1" };

// 1. the whole file
let { res, point } = await call(media, env, M, { headers: H });
assert.equal(res.status, 200);
assert.equal(res.headers.get("content-type"), "audio/mpeg");
assert.equal(res.headers.get("content-length"), "1000");
assert.equal(res.headers.get("etag"), '"abc"');
assert.equal(res.headers.get("accept-ranges"), "bytes");
assert.equal(res.headers.get("cache-control"), "public, max-age=86400");
assert.equal(res.headers.get("access-control-allow-origin"), "*");
assert.equal(res.headers.get("last-modified"), "Wed, 14 Oct 2026 19:52:00 GMT");
assert.deepEqual(new Uint8Array(await res.arrayBuffer()), bytes);
assert.deepEqual(point.indexes, ["podcast/ep1/episode.mp3"]);
assert.equal(point.blobs[0], "podcast/ep1/episode.mp3"); assert.equal(point.blobs[1], "GET");
assert.equal(point.blobs[2], H["User-Agent"]); assert.equal(point.blobs[5], "https://agentsee.work");
assert.match(point.blobs[4], /^[0-9a-f]{24}$/); assert.equal(point.blobs[7], "audio/mpeg");
assert.ok(!JSON.stringify(point).includes("203.0.113.7"), "raw address in the point");
assert.deepEqual(point.doubles, [200, 1000, -1, -1, 1000, 0]);
const hash1 = point.blobs[4];
console.log("full file ok");

// 2. HEAD
({ res, point } = await call(media, env, M, { method: "HEAD", headers: H }));
assert.equal(res.status, 200); assert.equal(res.headers.get("content-length"), "1000");
assert.equal((await res.arrayBuffer()).byteLength, 0); assert.deepEqual(point.doubles, [200, 0, -1, -1, 1000, 0]);
console.log("head ok");

// 3. a range
({ res, point } = await call(media, env, M, { headers: { ...H, Range: "bytes=0-99" } }));
assert.equal(res.status, 206); assert.equal(res.headers.get("content-range"), "bytes 0-99/1000");
assert.equal(res.headers.get("content-length"), "100");
assert.deepEqual(new Uint8Array(await res.arrayBuffer()), bytes.slice(0, 100));
assert.deepEqual(point.doubles, [206, 100, 0, 99, 1000, 1]); assert.equal(point.blobs[6], "bytes=0-99");
console.log("range ok");

// 4. a suffix, 5. an open end
({ res } = await call(media, env, M, { headers: { ...H, Range: "bytes=-50" } }));
assert.equal(res.headers.get("content-range"), "bytes 950-999/1000"); assert.equal(res.headers.get("content-length"), "50");
({ res, point } = await call(media, env, M, { headers: { ...H, Range: "bytes=100-" } }));
assert.equal(res.headers.get("content-range"), "bytes 100-999/1000"); assert.equal(point.doubles[1], 900);
console.log("suffix and open ranges ok");

// 6. a condition that holds
({ res, point } = await call(media, env, M, { headers: { ...H, "If-None-Match": '"abc"' } }));
assert.equal(res.status, 304); assert.equal((await res.arrayBuffer()).byteLength, 0);
assert.equal(res.headers.get("content-type"), null); assert.equal(res.headers.get("etag"), '"abc"');
assert.deepEqual(point.doubles.slice(0, 2), [304, 0]);
console.log("304 ok");

// 7. missing, 8. a method we do not serve, 9. a path that is not a key
({ res, point } = await call(media, env, "https://media.agentsee.work/podcast/ep9/none.mp3", { headers: H }));
assert.equal(res.status, 404); assert.equal(point.doubles[0], 404); assert.equal(point.blobs[0], "podcast/ep9/none.mp3");
const n = log.points.length;
({ res } = await call(media, env, M, { method: "POST", headers: H }));
assert.equal(res.status, 405); assert.equal(log.points.length, n);
const calls = bucket.calls.length;
// the URL parser folds %2e%2e away before the Worker sees it; an empty segment does reach the guard
({ res, point } = await call(media, env, "https://media.agentsee.work/podcast/%2e%2e/x", { headers: H }));
assert.equal(res.status, 404);
({ res, point } = await call(media, env, "https://media.agentsee.work/podcast//x", { headers: H }));
assert.equal(res.status, 404); assert.equal(bucket.calls.length, calls + 1, "an empty segment must not reach the bucket"); assert.equal(point.doubles[0], 404);
({ res } = await call(media, env, "https://media.agentsee.work/", { headers: H }));
assert.equal(res.status, 404);
console.log("404, 405 and bad paths ok");

// 10. the hash: stable for an address within a day, different across addresses, never the address
({ point } = await call(media, env, M, { headers: H }));
assert.equal(point.blobs[4], hash1);
({ point } = await call(media, env, M, { headers: { ...H, "CF-Connecting-IP": "2001:db8::1" } }));
assert.notEqual(point.blobs[4], hash1); assert.ok(!JSON.stringify(point).includes("2001:db8"));
({ point } = await call(media, env, M, { headers: { "User-Agent": "x" } }));
assert.equal(point.blobs[4], ""); assert.equal(point.blobs[5], "");
console.log("hash ok");

// 11. OPTIONS, 12. an unsatisfiable range, 13/14. If-Range
({ res } = await call(media, env, M, { method: "OPTIONS" }));
assert.equal(res.status, 204); assert.equal(res.headers.get("access-control-allow-methods"), "GET, HEAD, OPTIONS");
({ res, point } = await call(media, env, M, { headers: { ...H, Range: "bytes=5000-" } }));
assert.equal(res.status, 416); assert.equal(res.headers.get("content-range"), "bytes */1000"); assert.deepEqual(point.doubles.slice(0, 2), [416, 0]);
({ res, point } = await call(media, env, M, { headers: { ...H, Range: "bytes=0-99", "If-Range": '"old"' } }));
assert.equal(res.status, 200); assert.equal(point.doubles[1], 1000);
assert.equal(bucket.calls[bucket.calls.length - 1][2], null, "second get must carry no range");
({ res } = await call(media, env, M, { headers: { ...H, Range: "bytes=0-99", "If-Range": '"abc"' } }));
assert.equal(res.status, 206);
console.log("options, 416 and If-Range ok");

// 15. the user agent is cut, 16. an encoded key, 17. a long UA and a bad referer
({ point } = await call(media, env, M, { headers: { "User-Agent": "u".repeat(900), Referer: "not a url" } }));
assert.equal(point.blobs[2].length, 400); assert.equal(point.blobs[5], "");
({ res } = await call(media, env, "https://media.agentsee.work/podcast/ep1/a%20b.vtt", { headers: H }));
assert.equal(res.status, 200); assert.equal(res.headers.get("content-type"), "text/vtt"); assert.equal(res.headers.get("cache-control"), null);
console.log("truncation and encoded keys ok");

// ── links ──
const kv = { store: {
  ep1x02: JSON.stringify({ url: "https://agentsee.work/podcast/ep1/", episode: "ep1", post: "x-clip-02", platform: "x" }),
  bare01: "https://www.youtube.com/watch?v=abc",
  nourl1: JSON.stringify({ episode: "ep1" }),
  evil01: JSON.stringify({ url: "javascript:alert(1)" }),
}, calls: [], async get(k) { this.calls.push(k); return this.store[k] ?? null; } };
const lenv = { LINKS: kv, LOG: log, HASH_KEY: "k".repeat(64) };
let fetchMode = "ok";
globalThis.fetch = async (u) => {
  if (fetchMode === "fail") throw new Error("down");
  assert.equal(u, "https://agentsee.work/404.html");
  return new Response("<html>404 page</html>", { status: 200, headers: { "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": "default-src 'none'" } });
};
const LH = { "User-Agent": "Mozilla/5.0", "CF-Connecting-IP": "203.0.113.7", Referer: "https://t.co/abc" };

({ res, point } = await call(links, lenv, "https://agentsee.work/p/ep1x02", { headers: LH }));
assert.equal(res.status, 302); assert.equal(res.headers.get("location"), "https://agentsee.work/podcast/ep1/");
assert.equal(res.headers.get("cache-control"), "no-store");
assert.deepEqual(point.indexes, ["ep1x02"]);
assert.deepEqual(point.blobs.slice(0, 3), ["ep1x02", "Mozilla/5.0", ""]); assert.equal(point.blobs[4], "https://t.co");
assert.deepEqual(point.blobs.slice(5, 9), ["ep1", "x-clip-02", "x", "GET"]); assert.deepEqual(point.doubles, [302, 1]);
assert.equal(point.blobs[3], hash1, "same address, same day, same hash as the media Worker");
({ res } = await call(links, lenv, "https://agentsee.work/p/bare01/", { headers: LH }));
assert.equal(res.status, 302); assert.equal(res.headers.get("location"), "https://www.youtube.com/watch?v=abc");
console.log("redirects ok");

({ res, point } = await call(links, lenv, "https://agentsee.work/p/nope01", { headers: LH }));
assert.equal(res.status, 404); assert.equal(await res.text(), "<html>404 page</html>");
assert.equal(res.headers.get("content-security-policy"), "default-src 'none'"); assert.equal(res.headers.get("cache-control"), "no-store");
assert.deepEqual(point.doubles, [404, 1]); assert.equal(point.blobs[0], "nope01");
const kvCalls = kv.calls.length;
for (const p of ["/p/ABC", "/p/", "/p", "/p/a", "/p/" + "a".repeat(17), "/p/x_y"]) {
  ({ res } = await call(links, lenv, "https://agentsee.work" + p, { headers: LH }));
  assert.equal(res.status, 404, p);
}
assert.equal(kv.calls.length, kvCalls, "bad ids must not reach KV");
for (const id of ["nourl1", "evil01"]) {
  ({ res } = await call(links, lenv, "https://agentsee.work/p/" + id, { headers: LH }));
  assert.equal(res.status, 404, id);
}
fetchMode = "fail";
({ res } = await call(links, lenv, "https://agentsee.work/p/nope01", { headers: LH }));
assert.equal(res.status, 404); assert.equal(await res.text(), "not found");
fetchMode = "ok";
console.log("not-found paths ok");

({ res } = await call(links, lenv, "https://agentsee.work/p/ep1x02", { method: "HEAD", headers: LH }));
assert.equal(res.status, 302); assert.equal((await res.arrayBuffer()).byteLength, 0);
const n2 = log.points.length;
({ res } = await call(links, lenv, "https://agentsee.work/p/ep1x02", { method: "POST", headers: LH }));
assert.equal(res.status, 405); assert.equal(log.points.length, n2);
console.log("head and 405 ok");
console.log("EDGE-TESTS-OK");
