# Runbook

Where everything lives, and the things that cost us time to find out. Written
because most of this was discovered by probing APIs rather than by reading
docs, and none of it is guessable from the code.

Nothing here is a credential. Account and zone IDs are identifiers — they
appear in dashboard URLs and are useless without a token.

## The map

| Thing | Value |
|---|---|
| Domain | `agentsee.work` — registered via Cloudflare Registrar, 18 Aug 2026 |
| Cloudflare account | `9468eccb7caed9f96283a9139c37a4df` |
| Zone | `d2ab9ee564f8c164c1c32f57414ce749` |
| Pages project | `agentsee` → `agentsee-6bm.pages.dev` |
| Custom domains | apex + `www`, both CNAME → `agentsee-6bm.pages.dev`, proxied |
| GitHub | `agentsee-work/agentsee.work` (public) |
| Mail | Cloudflare Email Routing → personal inboxes |

## Token scopes

One token does everything we currently automate. If you mint a replacement,
these are the permissions and what each is actually for:

| Permission | Level | Needed for |
|---|---|---|
| Cloudflare Pages — Edit | Account | Deploying the site |
| Email Routing Addresses — Edit | **Account** | Adding a forwarding destination |
| Zone — Read | Zone | Resolving the zone by name at all |
| DNS — Edit | Zone | Custom domain, MX/SPF/DKIM/DMARC |
| Email Routing Rules — Edit | Zone | The `name@agentsee.work` rules |
| Cache & Performance → Cache | Zone | Purging stale assets |
| Zone Settings — Edit | Zone | Browser Cache TTL, Email Address Obfuscation |
| Workers Scripts — Edit | Account | *Not held.* An Email Worker, if we ever fan mail out |

Scope it to the `agentsee.work` zone, not "all zones".

**Record the expiry date here whenever the token is rolled, and put a reminder
somewhere you'll see it.** Deploys depend on this token, and an expired one
fails silently in the sense that nothing warns you in advance — the next push
just goes red. Editing a token's permissions keeps the same secret, so adding
a scope needs no update in CI; *rolling* it does.

| Token | Expires |
|---|---|
| CI (`CLOUDFLARE_API_TOKEN` secret, Pages-only) | _record when rolled_ |

> **The CI secret is over-privileged.** `CLOUDFLARE_API_TOKEN` in the repo
> secrets currently carries all of the above, but the workflow only needs
> *Pages — Edit*. The repo is public, so the blast radius of a leak matters:
> a Pages-only token means "someone redeploys the landing page"; the current
> one means "someone repoints the domain and the mail". Replace it with a
> Pages-only token:
>
> ```sh
> gh secret set CLOUDFLARE_API_TOKEN --repo agentsee-work/agentsee.work
> ```

## Deploying

Three routes, in order of how much you should trust them:

1. **Direct upload** — `npx wrangler pages deploy . --project-name=agentsee`.
   Does not touch GitHub. This is the one that always works.
2. **Push to `main`** — `.github/workflows/deploy.yml` publishes and then polls
   `agentsee.work` until it returns 200, so a green tick means the site really
   served, not just that wrangler exited 0.
3. **Manual** — `workflow_dispatch` on the same workflow.

If Actions is red and you need to publish, use route 1.

## Things that surprised us

Each of these cost real time. They are not in any obvious doc.

**Cloudflare's Email Address Obfuscation rewrites your `mailto:` links.** It is
on by default and turns them into `/cdn-cgi/l/email-protection#…` plus an
injected decoder script — which breaks every contact link without JavaScript
and adds a script we didn't write. It only happens on the proxied custom
domain, so `*.pages.dev` tests clean and you never see it. It is now **off**
for this zone. If the CTAs ever stop working, check that first.

**Email Routing cannot fan one address out to two people.** The API rejects
multiple destinations in one action (*"forward action must contain exactly one
destination"*) and then rejects multiple actions (*"only one action per rule is
allowed"*). So `hello@` reaches exactly one inbox. The real fix is a Cloudflare
Email Worker, which needs a Workers-scoped token. Until then the page must not
claim mail reaches both of us.

**A forwarding destination must be confirmed by a human.** Adding an address
emails a verification link to it, and no rule referencing it can be created
until someone clicks (`code 2054: Destination address is not verified`). There
is no API path around this — it is deliberate anti-abuse.

**Pages registers a custom domain but cannot create its DNS record.** Adding
the domain succeeds and then sits at `"CNAME record not set"` until a token
with Zone DNS access writes the CNAME.

**A Pages alias can serve stale HTML for a minute or two after a deploy.**
Check `canonical_deployment` on the project before concluding a deploy failed —
twice it was correct while the alias was still catching up. Wait, don't redeploy.

**The zone's Browser Cache TTL silently overrode our `_headers`.** It was set
to 14400, so CSS and JS were served with `max-age=14400` no matter what
`_headers` said — up to four hours before a change reached a returning
visitor, and `cf-cache-status: HIT` at the edge on top. It is now **0**
("Respect Existing Headers") so `_headers` actually governs, and `/assets/*`
is `max-age=600`. If a deploy looks like it hasn't landed, check the cache
headers before you check anything else — and note that a browser can hold a
stale stylesheet while `curl` shows the fresh one, which makes it look like
a rendering bug rather than a caching one.

**Purging the cache.** In the current Cloudflare UI the permission is grouped
as **Cache & Performance → Cache** (it used to be a flat "Cache Purge" entry,
which is what older docs and any older notes will call it). With it:

```sh
curl -X POST "https://api.cloudflare.com/client/v4/zones/$ZID/purge_cache" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data '{"files":["https://agentsee.work/assets/styles.css"]}'
```

Worth knowing you rarely need it now that `/assets/*` is ten minutes.

**GitHub has no API for creating an organisation.** `POST /admin/organizations`
is GitHub Enterprise Server only and 404s on github.com. Web UI only.

**GitHub has no API for setting an org avatar.** `PATCH /orgs/{org}` accepts
`avatar_url`, returns 200, and silently ignores it — it looks like it worked.
Web UI only. The source images are in `assets/brand/`.

**Org membership alone does not grant push.** This org's
`default_repository_permission` is `read`, so a new member can see the repo and
not push to it. Grant per-repo instead of loosening the org default — the
default would apply to every future repo, including anything with client work
in it.

## Why the page looks like this

The visual direction is a **broadsheet**, in newsprint by default and noir in
dark mode. It was chosen over a plain editorial treatment because a broadsheet
is a *system* rather than a look: a masthead, a dateline and an issue number
give every future episode somewhere to live, which suits spending a long period
publishing before taking on clients.

The rules that keep it honest, all of which are load-bearing:

- The dateline and day counter compute themselves. Nothing on the page is
  allowed to be a hardcoded date or count that quietly drifts.
- Without JavaScript they fall back to wording that is true whenever it is
  read ("Early days", "Founded 18 August 2026") rather than a stale number.
- The register never lists an issue that has not been published.
- Copy does not claim more than the infrastructure delivers. When Email
  Routing turned out not to support fan-out, the sentence changed.
