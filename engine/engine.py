#!/usr/bin/env python3
"""
The engine: the private side of the podcast, from an approved episode to
every post, and back from every post to the numbers. This is the command
line; the screens are `serve`. The design is on AGE-69.

    ./engine/engine.py ingest ep1              the staged episode into the ledger
    ./engine/engine.py plan ep1                its day plan into posts, with short links
    ./engine/engine.py publish --due --dry-run what the next run would do
    op run --env-file=engine/engine.env -- ./engine/engine.py publish --due
    op run --env-file=engine/engine.env -- ./engine/engine.py serve

`ingest` and `plan` need no credentials; `links` (which `plan` tries) wants
the Cloudflare token, `publish` and the worker in `serve` want each
platform's. All come from the vault through engine.env; a run without them
skips what it cannot do and says so.
"""

import argparse
import getpass
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ingest  # noqa: E402
import ledger  # noqa: E402
import plan  # noqa: E402
import publish  # noqa: E402


def who():
    return os.environ.get("ENGINE_USER") or getpass.getuser()


def cmd_ingest(a):
    c = ledger.connect(a.ledger)
    for slug in a.slug:
        n = ingest.ingest(c, slug, a.src, who())
        print(f"{slug}: {n} deliverables in the ledger")


def cmd_plan(a):
    c = ledger.connect(a.ledger)
    for slug in a.slug:
        made = plan.plan(c, slug, who())
        total = c.execute("SELECT COUNT(*) FROM post WHERE episode=?", (slug,)).fetchone()[0]
        print(f"{slug}: {len(made)} new posts ({total} in all)")
        if not a.no_links:
            try:
                print(f"  {plan.links(c, slug, who=who())} short links in KV")
            except RuntimeError as e:
                print(f"  short links not pushed: {e}\n  (run `engine.py links` under op run when ready)")


def cmd_links(a):
    c = ledger.connect(a.ledger)
    print(f"{plan.links(c, a.slug, who=who())} short links in KV")


def cmd_posts(a):
    c = ledger.connect(a.ledger)
    q, args = "SELECT * FROM post", []
    cond = []
    if a.episode:
        cond.append("episode = ?"); args.append(a.episode)
    if a.status:
        cond.append("status = ?"); args.append(a.status)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    for p in c.execute(q + " ORDER BY scheduled_at, id", args):
        t = ledger.local(p["scheduled_at"])
        print(f"{p['id']:4d}  {t.strftime('%Y-%m-%d %H:%M') if t else '           '}  {p['status']:<10} "
              f"{p['account']:<15} {p['asset']:<28} {p['link_id'] or '':<10} {(p['title'] or '')[:50]}")


def cmd_publish(a):
    c = ledger.connect(a.ledger)
    if a.post:
        posts = [ledger.post(c, i) for i in a.post]
        if not all(posts):
            sys.exit("error: no such post")
    else:
        posts = publish.due(c, a.as_of)
    if not posts:
        print("nothing is due")
        return
    print(f"{len(posts)} due" + (" (dry run)" if a.dry_run else ""))
    res = publish.run(c, posts, dry_run=a.dry_run, who=who())
    if not a.dry_run:
        ledger.event(c, "publisher:run", who(), detail={"results": res})


def cmd_serve(a):
    import web
    web.serve(a.port, not a.no_worker, a.ledger)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", help="the SQLite file (default $ENGINE_DATA/ledger.sqlite, or build/engine/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="a staged episode into the ledger")
    p.add_argument("slug", nargs="+")
    p.add_argument("--src", help="the out/ directory, if not build/episodes/<slug>/out")
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("plan", help="the day plan into posts, with short links")
    p.add_argument("slug", nargs="+")
    p.add_argument("--no-links", action="store_true", help="do not push the short links to KV")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("links", help="push every short link to KV (idempotent)")
    p.add_argument("slug", nargs="?")
    p.set_defaults(fn=cmd_links)
    p = sub.add_parser("posts", help="list posts")
    p.add_argument("--episode")
    p.add_argument("--status")
    p.set_defaults(fn=cmd_posts)
    p = sub.add_parser("publish", help="run the publisher")
    p.add_argument("--due", action="store_true", help="everything due (the default when no --post)")
    p.add_argument("--post", type=int, nargs="*", help="these posts, whatever their state")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--as-of", help="pretend it is this ISO instant")
    p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("serve", help="the screens, with the worker")
    p.add_argument("--port", type=int, default=8877)
    p.add_argument("--no-worker", action="store_true")
    p.set_defaults(fn=cmd_serve)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
