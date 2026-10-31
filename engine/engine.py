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
        n = ingest.ingest(c, slug, a.src, who(), from_archive=a.archive)
        print(f"{slug}: {n} deliverables in the ledger")


def cmd_snapshot(a):
    import archive

    c = ledger.connect(a.ledger)
    try:
        archive.snapshot(c, who=who())
    except archive.ArchiveError as e:
        sys.exit(f"error: {e}")


def cmd_restore(a):
    import archive

    path = pathlib.Path(a.ledger) if a.ledger else ledger.data_dir() / "ledger.sqlite"
    try:
        archive.restore(path, key=a.key, force=a.force)
    except archive.ArchiveError as e:
        sys.exit(f"error: {e}")
    c = ledger.connect(path)
    n = c.execute("SELECT COUNT(*) FROM post").fetchone()[0]
    print(f"{path}: restored from {a.key}, {n} posts")


def cmd_plan(a):
    c = ledger.connect(a.ledger)
    for slug in a.slug:
        made = plan.plan(c, slug, who())
        total = c.execute("SELECT COUNT(*) FROM post WHERE episode=?", (slug,)).fetchone()[0]
        print(f"{slug}: {len(made)} new posts ({total} in all)")
        if a.prune:
            gone = plan.prune(c, slug, who())
            print(f"  {len(gone)} planned posts pruned: the plan no longer makes them")
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
        cond.append("episode = ?")
        args.append(a.episode)
    if a.status:
        cond.append("status = ?")
        args.append(a.status)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    for p in c.execute(q + " ORDER BY scheduled_at, id", args):
        t = ledger.local(p["scheduled_at"])
        print(
            f"{p['id']:4d}  {t.strftime('%Y-%m-%d %H:%M') if t else '           '}  {p['status']:<10} "
            f"{p['account']:<15} {p['asset']:<28} {p['link_id'] or '':<10} {(p['title'] or '')[:50]}"
        )


def cmd_publish(a):
    c = ledger.connect(a.ledger)
    if a.post:
        posts = [ledger.post(c, i) for i in a.post]
        if not all(posts):
            sys.exit("error: no such post")
    else:
        posts = publish.due(c, a.as_of, include_late=a.late)
        held = [] if a.late else publish.late(c, a.as_of)
        if held:
            print(f"{len(held)} late, left for a person (or --late): " + ", ".join(str(p["id"]) for p in held))
    if not posts:
        print("nothing is due")
        return
    print(f"{len(posts)} due" + (" (dry run)" if a.dry_run else ""))
    res = publish.run(c, posts, dry_run=a.dry_run, who=who())
    if not a.dry_run:
        ledger.event(c, "publisher:run", who(), detail={"results": res})


def cmd_collect(a):
    import collect

    c = ledger.connect(a.ledger)
    res = collect.run(c, only=a.only, who=who())
    print("; ".join(f"{k}: {v}" for k, v in res.items()))


def cmd_serve(a):
    import web

    web.serve(a.port, not a.no_worker, a.ledger, a.host)


def cmd_mail(a):
    import mail

    c = ledger.connect(a.ledger)
    try:
        msgid = mail.send(
            a.to,
            "A test from the engine",
            "This is the engine, saying hello. If it reached the inbox rather than the junk folder, the office can "
            "send mail: codes, invitations, confirmations and signed copies.",
        )
    except mail.MailError as e:
        mail.record(c, who(), a.to, "test", error=e)
        sys.exit(f"error: {e}")
    mail.record(c, who(), a.to, "test", msgid)
    print(f"sent to {a.to}: {msgid}")


def cmd_idea(a):
    import idea

    if not idea.ready(os.environ):
        sys.exit(f"error: {idea.NEEDS} is not set: run under op run --env-file=engine/engine.env")
    cfg = idea.settings()
    text = a.text or "In one sentence, say hello from the office and name today's day of the week."
    try:
        out, usage, stop, model = idea.request(
            os.environ,
            "You are the office at AgentSee, answering a test from the engine. One sentence.",
            [{"role": "user", "content": text}],
            on_delta=lambda s: print(s, end="", flush=True),
        )
    except idea.IdeaError as e:
        sys.exit(f"\nerror: {e}")
    print(
        f"\n[{model}: {usage['input']} tokens in, {usage['output']} out, stop {stop}; "
        f"a guest gets {cfg['max_turns']} turns and {cfg['token_budget']} tokens]"
    )


def cmd_release(a):
    import release

    c = ledger.connect(a.ledger)
    try:
        if a.action == "add":
            if a.issue:
                import linear

                try:
                    docs = linear.documents(os.environ, a.issue)
                except linear.LinearError as e:
                    sys.exit(f"error: {e}")
                if a.title:
                    docs = [d for d in docs if d["title"] == a.title]
                if len(docs) != 1:
                    names = "; ".join(d["title"] for d in docs)
                    sys.exit(
                        f"error: {a.issue} has {len(docs)} documents"
                        + (f": {names}; name one with --title" if docs else "")
                    )
                text, source = docs[0]["content"], f"{a.issue}: {docs[0]['title']}"
            elif a.what:
                text, source = pathlib.Path(a.what).read_text(encoding="utf-8"), a.what
            else:
                sys.exit("error: add takes a file, or --issue AGE-55 for the issue's document")
            try:
                v = release.add(c, text, who(), source)
            except release.ReleaseError as e:
                sys.exit(f"error: {e}")
            print(f"version {v['n']}: {v['title']}, sha256 {v['sha256'][:16]}…")
            print(f"{' and '.join(release.hosts())} sign it on /you before a guest sees it")
            return
        if a.action == "show":
            v = release.version(c, int(a.what)) if a.what else release.latest(c)
            if not v:
                sys.exit("error: no such version")
            print(v["text"], end="")
            return
        rows = release.versions(c)
        if not rows:
            print("no versions yet: engine.py release add <file>, or add --issue AGE-55")
            return
        cur = release.issued(c)
        for v in rows:
            signed = release.signed_by(c, v["n"])
            waiting = release.waiting_for(c, v["n"])
            state = "issued" if v["n"] == cur else (f"waiting for {', '.join(waiting)}" if waiting else "superseded")
            by = ", ".join(f"{h} {r['at'][:10]}" for h, r in signed.items()) or "nobody"
            print(
                f"v{v['n']}  {v['title']}  {v['sha256'][:12]}  {v['created_at'][:10]} by {v['created_by']}  signed: {by}  [{state}]"
            )
    finally:
        c.close()


def cmd_linear(a):
    import linear

    c = ledger.connect(a.ledger)
    try:
        if a.drain:
            n = linear.drain(c, os.environ)
            print(f"{n} sent; {linear.waiting(c)} waiting, {linear.stuck(c)} given up")
            return
        if not a.issue:
            sys.exit("error: name an issue (AGE-73), or --drain")
        text = a.text or (
            f"A test from the engine, {ledger.now()}. If this comment shows AgentSee Engine as its author, "
            "the bridge is live."
        )
        try:
            cm = linear.comment(os.environ, a.issue, text)
        except linear.LinearError as e:
            ledger.event(c, "linear:failed", who(), detail={"issue": a.issue, "error": str(e)[:500]})
            sys.exit(f"error: {e}")
        ledger.event(c, "linear:done", who(), detail={"issue": a.issue, "comment": cm["url"]})
        print(f"commented on {a.issue}: {cm['url']}")
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", help="the SQLite file (default $ENGINE_DATA/ledger.sqlite, or build/engine/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="a staged episode into the ledger")
    p.add_argument("slug", nargs="+")
    p.add_argument("--src", help="the out/ directory, if not build/episodes/<slug>/out")
    p.add_argument(
        "--archive",
        action="store_true",
        help="read release.json and staged.json from the archive bucket (the default when build/ is not here)",
    )
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("plan", help="the day plan into posts, with short links")
    p.add_argument("slug", nargs="+")
    p.add_argument("--no-links", action="store_true", help="do not push the short links to KV")
    p.add_argument(
        "--prune",
        action="store_true",
        help="skip planned posts the plan no longer makes (an account dropped, or an asset it no longer takes)",
    )
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
    p.add_argument(
        "--late", action="store_true", help="also what is past its time by more than late_hours (config.json)"
    )
    p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("collect", help="read what every post and file did, into the ledger (the worker does it daily)")
    p.add_argument(
        "--only", nargs="*", choices=["edge", "youtube", "bluesky", "mastodon", "x"], help="these sources only"
    )
    p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("serve", help="the screens, with the worker")
    p.add_argument("--port", type=int, default=8877)
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="loopback by default; 0.0.0.0 inside the container, where the tunnel is the only way in",
    )
    p.add_argument("--no-worker", action="store_true")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("mail", help="a test message from the office account, to prove the mail credential")
    p.add_argument("to", help="an address of yours")
    p.set_defaults(fn=cmd_mail)
    p = sub.add_parser("idea", help="one streamed reply from the office's assistant, to prove the key")
    p.add_argument("--text", help="what to say to it (default: a hello)")
    p.set_defaults(fn=cmd_idea)
    p = sub.add_parser("linear", help="a comment on an issue as AgentSee Engine, to prove the bridge")
    p.add_argument("issue", nargs="?", help="the issue, AGE-73")
    p.add_argument("--text", help="the comment's words (default: a test sentence)")
    p.add_argument(
        "--drain", action="store_true", help="send what the outbox holds, now (the worker does it each minute)"
    )
    p.set_defaults(fn=cmd_linear)
    p = sub.add_parser("release", help="the release's versions: list them, add one, show one")
    p.add_argument("action", nargs="?", choices=["list", "add", "show"], default="list")
    p.add_argument("what", nargs="?", help="add: the file holding the text; show: the version (default the latest)")
    p.add_argument("--issue", help="add: the issue whose Linear document holds the text (AGE-55)")
    p.add_argument("--title", help="add --issue: which document, when the issue has more than one")
    p.set_defaults(fn=cmd_release)
    p = sub.add_parser("snapshot", help="a copy of the ledger to the archive bucket, now (the worker does it nightly)")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("restore", help="the ledger back from the archive, for a rebuilt box")
    p.add_argument(
        "--key",
        default="engine/ledger-latest.sqlite",
        help="which copy; a dated one is engine/ledger-YYYY-MM-DD.sqlite",
    )
    p.add_argument("--force", action="store_true", help="replace a ledger that exists")
    p.set_defaults(fn=cmd_restore)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
