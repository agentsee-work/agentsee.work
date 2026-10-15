# The edges

Two Workers on our zone, declared in [../edge.tf](../edge.tf), and the two
Analytics Engine datasets they write. The collector (AGE-69, item 4) reads
the datasets daily; this file is the contract between them.

```
media.js   media.agentsee.work/*   serves the public bucket, logs each request
links.js   agentsee.work/p/*       looks a short id up in KV, logs the click, 302s
test.mjs   node infra/podcast/workers/test.mjs   both, under Node, no dependencies
```

Neither Worker sets a cookie, loads anything, or writes the network address.
Both hash it the same way: HMAC-SHA256 under a key that lives only in the
Workers' bindings and the vault, over the UTC date and the address, cut to
24 hex characters. One listener hashes the same all day and differently
tomorrow, which is what counting a listener once a day needs and nothing
more. The privacy notice on the site promises exactly this.

## `podcast_media`, one row per request to the media host

| column | holds |
|---|---|
| `index1` | the object key, e.g. `podcast/ep1/episode.mp3` (sampling, if it ever happens, is per file) |
| `blob1` | the object key again, or the path if it was not a key |
| `blob2` | method: `GET` or `HEAD` |
| `blob3` | user agent, first 400 characters |
| `blob4` | country, two letters, from Cloudflare |
| `blob5` | the address hash |
| `blob6` | the referer's origin (`https://agentsee.work`), or empty |
| `blob7` | the `Range` header as sent, first 64 characters |
| `blob8` | the content type served |
| `blob9` | the Cloudflare colo that answered |
| `double1` | status: 200, 206, 304, 404, 412, 416 |
| `double2` | bytes the response carries: the size for 200, the span for 206, 0 otherwise |
| `double3` | first byte of the span, or -1 |
| `double4` | last byte of the span, or -1 |
| `double5` | the object's size |
| `double6` | 1 for a partial response |

Bytes are what was offered, not what arrived: a listener who stops after a
minute still produced a 206 for the span the app asked for. The IAB rules
(one listener per file per day, at least a minute's worth of bytes, known
apps and bots told apart by user agent) are the collector's to apply over
these columns; nothing here pre-judges them.

## `podcast_links`, one row per request to a short link

| column | holds |
|---|---|
| `index1` | the id |
| `blob1` | the id again, or the path if it was not one |
| `blob2` | user agent, first 400 characters |
| `blob3` | country |
| `blob4` | the address hash, same key and day as the media Worker's |
| `blob5` | the referer's origin: the platform the click came from |
| `blob6` | the link's `episode` |
| `blob7` | the link's `post` |
| `blob8` | the link's `platform` |
| `blob9` | method |
| `blob10` | colo |
| `double1` | status: 302, or 404 for an id with no link |
| `double2` | 1 |

## A link

A KV entry in the `podcast-links` namespace (`tofu output links_namespace`):
the key is the id, lower-case letters and digits, two to sixteen of them;
the value is JSON with `url` and, optionally, `episode`, `post` and
`platform`, which are copied into every click's row so they can be grouped
without a join. A bare URL as the value also works. The publisher writes
these as it creates posts; by hand, with a token that has Workers KV
Storage > Edit (the podcast token does, so `op run` on the root's `op.env`
provides it):

```sh
cd infra/podcast
op run --env-file=op.env -- sh -c 'curl -sS -X PUT \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/storage/kv/namespaces/$NAMESPACE/values/ep1x02" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -d "{\"url\": \"https://agentsee.work/podcast/ep1/\", \"episode\": \"ep1\", \"post\": \"x-clip-02\", \"platform\": \"x\"}"'
```

with `ACCOUNT` and `NAMESPACE` in the environment first (`tofu output` has
the namespace; the account id is in variables.tf). KV is eventually
consistent: a new link answers everywhere within a minute.

## Reading the datasets

Analytics Engine answers SQL over HTTPS. It needs its own token, Account >
Account Analytics > Read and nothing else, which is the collector's and does
not exist yet (CREDENTIALS.md, later). Each row carries `_sample_interval`,
1 unless Cloudflare sampled, in which case counts are `SUM(_sample_interval)`
rather than `COUNT()`. Data lives three months; the collector copies daily,
which is why the ledger, not this, is the record.

```sh
curl -sS "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/analytics_engine/sql" \
  -H "Authorization: Bearer $ANALYTICS_TOKEN" \
  -d "SELECT toDate(timestamp) AS day, index1 AS file, double1 AS status,
             SUM(_sample_interval) AS requests, SUM(double2 * _sample_interval) AS bytes
      FROM podcast_media WHERE timestamp > NOW() - INTERVAL '7' DAY
      GROUP BY day, file, status ORDER BY day, file, status FORMAT JSON"
```

## If the media route does not take

The route sits on the hostname of an R2 custom domain. If a request to the
media host is still answered by the bucket itself after the apply (no
`X-Content-Type-Options: nosniff` header, nothing in `podcast_media`), the
alternative is to drop the custom domain and give the Worker the hostname
through a Workers custom domain, which creates the DNS record itself and so
needs a token with Zone > DNS > Edit: that is the DNS token's root, not this
one, and the split would have to be rethought before doing it.
