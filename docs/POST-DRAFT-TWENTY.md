# Working notes — "Twenty questions, and the one that counted was the twenty-first"

**Published as Issue No. 7**:
[`public/issues/07-twenty.html`](../public/issues/07-twenty.html). That is
the version that ships, cut shorter and with the identifiers stripped.

This file is the longer argument it was cut from, plus the notes for
episode 3, which walks through the questions on the podcast. It stays in
`docs/` because only `public/` is published. The technical record is
`.claude/skills/twenty-questions/SKILL.md` (the one source) and
`tools/twenty.py` (what stamps it onto the site); the decisions, with dates,
are on AGE-28 and AGE-103 on Linear. This is the story of how it got that
way.

If the two ever disagree, the published page wins. This is the workings.

---

## The argument, at length

Episodes 0 to 2 and Issues No. 2 to No. 6 were us building for us: a mail
server, a mark, a theme, a podcast pipeline, a publishing engine. The twenty
questions are the first thing we made for someone else, and the first thing
on the site that asks a stranger to do something. They went from a Slack
message on Monday 26 October to a live page on Thursday 29 October, were
rewritten on Monday 2 November for the model the company is settling on,
and reviewed the same morning.

The version to avoid writing is "we built an AI intake form". The questions
are the least of it. What took thought was deciding not to build a product,
deciding what a question has to earn to be one of twenty, and deciding what
the brief should end with, which turned out to be the question that made
the company say what it sells.

---

## 1. Where it came from

Abrar, Slack, Monday 26 October, mid-afternoon. At GradeDrive a model marks
the exam papers and the teacher can review the entire lot; a chat interface
cannot give anyone that. He proposed a generic workflow generator: a
business vibes its own internal tooling out of whatever it already does with
a model, and gets a deterministic UI, a workflow with a proper screen,
rather than a chat. "Basically vibing UX." By 17:39 he had found MCP Apps,
which is most of that inside the chat products, and asked whether we should
build a more specific one.

James, to the model, Tuesday 27 October: tying the company to an MCP App
offering is not what we want; we want to enable any kind of software project
in any shape. The model agreed at the level of the offering and agreed with
Abrar at the level of the pattern: once a model does the work, the value is
in the surface where a person reviews all of it at once, and that pattern
can be delivered as an MCP App, a web app, a spreadsheet or an artifact.
What exists already: inline UIs from chat (MCP Apps, artifacts), vibe-an-app
builders (Lovable, Base44, Bolt, Replit), internal-tool builders with AI
(Retool, Airtable, Glide), workflow builders (Gumloop, n8n, Zapier, Relay),
review-at-scale tools built for labelling teams (Label Studio, Argilla). The
gap Abrar sees is a product bet, and a product needs a different shape of
company than twenty hours a week.

James, Thursday 29 October, 09:45. Keep the product workflows; an always-on
MCP chat is not right. "We are living in an ultra-versatile area; all of
what we need to do is enablement, so making a product for mass consumption
isn't going to work for us." Get people's ideas into an iteration loop as
soon as possible, with the initial parameters of their project scoped: the
software non-negotiables (CI/CD, automated review, automated tests), then
scale (an internal oft-used tool, through transactional, to viral social
media load), uptime (frequent, every deploy or more), workflow latency (a
couple of seconds up to ten to load infrastructure from a cold start, down
to sub-ten milliseconds). A wizard or inference loop leads to the one-shot
prompt that creates some form of MVP, with everything defined on those
scales documented, and then access to the environment created: a "project"
surface we and the client could work on. "I know that is quickly turning
into a product."

The model's reply, 09:46, is the pivot of the whole thing, so here it is in
full as a record:

> The shape is right. The weak link is the one-shot MVP, and the first
> version of this is not software.
>
> What is right. The dials are the decisions clients cannot articulate and
> that set the architecture and the bill: internal tool versus transactional
> versus viral, deploy-on-merge versus scheduled, cold start tolerable versus
> sub-10 ms. Making them explicit up front, with the non-negotiables fixed,
> gives you two things no vibe-coding tool offers: a quality floor on day
> one, and a written record of what the client chose not to buy. That record
> is what stops the "why isn't it instant" conversation six months in.
>
> What is weak. The one-shot prompt is the commodity part. Lovable, Base44,
> Bolt and Replit all produce an MVP from a paragraph, and the client could
> too. Your value sits either side of it: the scoping that produces the
> brief, and the loop after it, where CI, review, tests and a shared surface
> keep the thing alive. So the artefact is the project brief, with the dials
> recorded and a scaffold that already has the non-negotiables wired in. The
> prompt is derived from the brief, not the other way round.
>
> You already own the project surface. It is what this repo is: a repository
> with CI and automated review, a Linear project for the non-technical half,
> and Claude Code sessions the client can watch. The client gets a seat in
> the repo, a seat in Linear, and a preview deploy.
>
> So the first version is a skill and a template, not a wizard. Run it on
> the next three real projects, one of them your own. If the same steps
> repeat unchanged, that is the moment a product exists and you will know
> exactly what it is. Building the wizard first is how a twenty-hour-a-week
> consultancy becomes a product company by accident.

James, 09:53: "I agree that the one-shot prompt is the commodity. Ok, well
then I think the wizard/skill it is." Then the brief for the skill, which
section 2 quotes.

## 2. The brief, and the first draft

James's brief, in his words: something interactive, a 20-questions-like
approach, almost where you have a 0 to 10 score of what you need or
multiple choice (including Other) for the initial conditions for setting up
a really successful software project. Geared towards non-technical minds.
Comprehensive, but no more than twenty questions that get at the nexus of
initial conditions and thinking things through at the outset. Upfront that
basically anything is possible with software (handling PII and regulation
can get in the way). Health (a lot more care needed for PII), money (one
step below health in trickiness and potential regulation), scale (social
media scale means everything designed around queuing, which makes the
initial build harder to develop and test), uptime (high uptime means the
infrastructure must be very well thought out). All bases touched in a skill,
and people encouraged to run it in their own chat and post results back.

The model's draft came two minutes later: the behaviour (open by saying
nearly anything can be built; one question at a time; lettered options,
always with Other and Don't know yet; one plain line after each answer on
what it means; no unexplained jargon; a score out of ten only where there is
a real gradient, bands elsewhere because people pick a band more honestly
than a number), the twenty questions in five groups of four (the idea, care
needed, running conditions, shape, reality), and the brief's shape (care
tier, dials table, what it implies, an always-on list, not yet known). UK
law first. The route back left for James to pick.

James, 09:58: skill in the repo; prominently placed with easy ways to open
it in ChatGPT, Claude, Grok and the other products a typical user has;
consider closing the loop with an endpoint of ours, maybe an email; is
twenty the right number; and show progress to keep a user happy.

Built by 10:10, on a worktree. The three answers:

- **Twenty is right as a ceiling, not a count.** The skill skips any
  question an earlier answer settles, says so in a line, and still advances
  the bar. The expectation is fifteen to seventeen answered; nobody has
  measured it. Cutting below twenty loses a base; adding beyond it loses the
  person.
- **Progress** is a fixed line at the top of every question,
  `Question 7 of 20 · Care needed, 3 of 4 · ▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱▱`, plus a
  two-line recap after each group of four and a play-back of the idea after
  question 10.
- **Closing the loop: email.** The brief ends by telling them to send it to
  hello@agentsee.work with the subject "AgentSee: Twenty questions", and the
  page has the same button. The mail server (Issue No. 2) already receives
  it, the subject makes it filterable, nothing new has to run. A form would
  need a backend and a change to the content policy (`form-action 'none'`,
  `script-src 'self'`), so a Worker endpoint is worth it only if volume
  arrives.

James, 10:20: add that it is inspired by the little device of the 90s/2000s
that guessed anything in your head, "just to keep a little whimsy". The
toy's name is a registered trademark, so the copy describes it: "the little
plastic ball of the early 2000s that guessed what you were thinking". In the
skill the toy is a frame and two guesses: think of the software you want;
after question 10 the assistant says what it thinks the idea is and asks
whether it is warm (a no gets "what have I got wrong?"); after question 20
one sentence beginning "My guess:" names the shape of the build. Then the
brief, which opens with "The guess". The rules forbid padding, so it is a
frame, not a character.

## 3. The links, and the route back

Each open-in link carries the whole prompt URL-encoded in the `q` parameter
of the product's new-chat page. None of the products documents this.

- **Claude** (`claude.ai/new?q=`) and **ChatGPT** (`chatgpt.com/?q=`) took
  the whole prompt. James clicked both at about 9,600 characters on 29
  October and each opened on question 1.
- **Grok** (`grok.com/?q=`) answered 431 Request Header Fields Too Large to
  a link that long. James found it by clicking. Its link now carries a
  255-character launcher that asks it to open
  `agentsee.work/twenty-questions/SKILL.md` and run it; loaded headless, the
  launcher lands in Grok's box and the page answers 200. If Cloudflare's bot
  settings ever challenge AI fetchers, this is the link that breaks, and the
  fix is in the zone's crawl settings rather than in the repo.
- **Gemini** and **Copilot** have no working prefill and get a copy button
  (`public/assets/twenty.js`, only when the clipboard API exists).

After the rewrite the prompt is about 12,000 characters, 12,700 in a link.
Cloudflare's URL ceiling is 16 KB. Headless probes of claude.ai and
chatgpt.com with the long link return 403 from the bot challenge, not 431 or
414, which proves the edges accept the length and nothing about the origin;
only a real browser click proves that. If either product ever refuses, one
word in `OPEN_IN` in `tools/twenty.py` moves it to the launcher like Grok.

The timing, from the transcript: James's first message 09:45; the draft
09:55; built 10:10; the toy 10:21; Grok's 431 10:23; PR #59 open 10:27;
merged 10:43. Forty-two minutes to an open PR, under an hour to live. That
is what "volume is free" looks like from the inside. Choosing the twenty,
and the words a non-technical person will answer honestly, is what took
three people and four days, and it is not finished.

## 4. The rewrite for build and run (2 November)

James, 03:15, Monday 2 November, a written brief: revise the tool so it
qualifies for an ongoing build-and-run relationship, not a one-off build.
AgentSee's model is to build operational software with the client in the
room and then own and run it on a monthly retainer. The current tool
produces a brief and ends "keep it or send it to AgentSee", which invites
people to take the brief and self-serve. Required: questions that surface
ownership and operational reality (who will be responsible once it is live;
what breaks if it goes down for a day; what existing systems, data or
regulations it has to live alongside; has anyone tried to build this before;
how will you know in twelve months whether it was worth it). A "Running it"
section in the brief: ongoing attention, skills the client lacks, the risks
of self-building and handing over to no one. A closing question, "Who's
going to run this once it exists?", with two routes: a conversation with
AgentSee, or an honest note that this is simple enough to self-serve. Tone
plain, transparent, slightly subversive; no sales language; still useful to
someone who never writes to us. About twenty questions. Consider what else
on the site should change.

What went, and why each only served a hand-over: "what does it replace"
(absorbed into "what does it have to live alongside", which adds the rules
the business already works under and who else gets a say); "how fast must it
feel" (folded into scale: above a few thousand or instant, the machines are
always on); "how do people get in" (folded into who uses it); the hour on a
Tuesday afternoon out of ten (became "if it went down for a day, what
breaks, who notices first, who do they ring"); "once it is live, who looks
after it, (d) nobody, it should look after itself" (became "who signs it off
and whose name is next to it", where nobody is the commonest answer and the
most expensive). "Three months, how will you know it worked" became "twelve
months, worth it", which is the difference between a thing that gets
finished and a thing that gets kept. The monthly cost question now counts
people as well as machines. The last group was renamed from Reality to
Owning it.

What came in: what it lives alongside; has anyone tried before (whatever
stopped the last one is usually still there); a day down and who gets the
call (names who is on call today whether they know it or not); who is
responsible once live; who in-house could, today, restore a backup, read an
error, renew a certificate or ring a supplier.

The brief kept the build summary and gained **Running it**: what it will
need (the connections that break when the other side changes, the AI that
drifts, the data that must be deleted on time, the keys and certificates
that expire, the updates that cannot wait, the store reviews, the backups
nobody has tried restoring; how often someone must look; hours a month), who
you have (from questions 19 and 20), what you are missing, and if you build
it yourself and hand it to no one (two or three sentences, specific, in the
order it will arrive; "not much" is allowed).

The close, unnumbered: **Who is going to run this once it exists?** The
assistant gives one of two readings and is told to choose the one the
answers support, not the one that flatters the idea or the one that sells,
and to say in a sentence why. Simple enough to run themselves: say so, with
what to watch and how often; the brief is theirs. Or it needs running, and
the people who built a thing are the right people to keep it alive; AgentSee
builds with the client in the room and then runs it, on a monthly retainer;
email the brief; two people read it. The reasoning for keeping both routes:
a tool that only sells is useless to the reader, and a tool that only
informs makes the sale someone else's. The honest reading is the only one
worth the reader's twenty minutes and the only one worth our name on.

The front page the same morning (PR #72). Three places contradicted the
tool: the status strip ("Welcoming clients soon"), the "Built alongside you"
step (which, with one added sentence about a retainer, stated two commercial
models in one paragraph, the other being "partners with a share of the
reward"), and the Situations vacant ad ("Not today, we're still in
discovery"). All three now say build and run: "Taking the first
conversations", "Built alongside you, then run", "Discovery, and the first
few clients", "an unproven model", and the ad as the build-and-run ad. The
Linear document "What AgentSee is" (14 September) still says one fixed-price
unit at a time, no retainer, and the client walks away owning everything.
James settled it with Abrar on 2 November; the merge was the decision made
public, and the document is next.

## 5. Abrar's review (2 November)

Six findings from Abrar's own agent, on the original 29 October set; James
relayed them at 04:01, after the rewrite was merged, so each was judged
against the set then on main.

1. **Nothing asks what they can spend on the build.** True; Q16 was the
   monthly figure only. Folded into Q18 (when is the first version needed)
   rather than added, since twenty is the name: "What can you spend to get
   the first version live, and when do you need it? (a) near nothing, mostly
   my own time (b) low thousands (c) tens of thousands (d) more than that;
   and weeks, this quarter, this year, or no date." Money and date decide
   how small the first version is. Kept out of the sales register because it
   sizes the build rather than the pitch; "near nothing, mostly my own time"
   is the self-serve signal.
2. **Nothing asks who signs it off.** True. Folded into Q19: "Who signs it
   off, and whose name is next to it once it is live? Not who built it: who
   says it is done, who gets the call when it stops, who decides when it
   changes." Plus: "If that person is not the one answering, say so: they
   should see the brief." That is the acceptance case, and the one that most
   often goes wrong with a non-technical client. The brief's "Who you have"
   carries it.
3. **Q12 and Q7 are too long.** True, and the rewrite had made it worse with
   "also" clauses on Q2, Q9 and Q10; the prompt had grown by a third. The
   trim: personal data seven options to five ("names and emails; addresses,
   dates of birth or location; photos or voice; ID documents or children's
   data; none"); where-used's aside to one line ("Also: accessibility, other
   languages, old devices?"); what-it-talks-to ten options to seven; access
   into the options of who-uses-it; scale and speed one question with three
   bands; every explanation one sentence.
4. **Q16 overlaps Q2, Q15 overlaps Q19.** The first was already merged in
   the rewrite. Change rate and ownership are different dials, so both
   stay; the overlap was Q14's line "weekly or faster means you are in the
   loop with whoever builds it", which went, so Q19 and Q20 alone carry the
   people.
5. **The care tiers are debatable.** Agreed. The original rule was by
   domain (0 none; 1 heavier personal data; 2 money; 3 health, children's
   data or ID documents), which put a Stripe checkout in the same tier as
   moving money between people and "off sick today" with clinical records.
   Now by what each tier adds to the build: 0 nothing beyond good practice;
   1 a privacy notice, retention and deletion on time (names and emails,
   addresses, dates of birth, location); 2 an audit trail and a record of
   who looked (payments through a provider, photos or voice, health in
   passing); 3 a formal assessment, and a regulator who can ask (holding,
   moving or advising on money; clinical health; children's data; ID
   documents). The highest that applies. Q5 split "properly: records,
   symptoms, treatment" from "in passing: fitness, mood, off sick today";
   Q7 split ID documents and children's data from photos and voice. The
   number is now a statement about the build, which is harder to argue with
   than a statement about the domain.
6. **The "Always on" list sells a bit.** Agreed. "A repository, automated
   tests, automated review, a deploy on every change, a preview of every
   change, and a tracker the owner can see" was our own practice as a
   checklist. Now "Insist on these: Whoever builds it: a repository you can
   see, tests that run on every change, a deploy that is a button and can be
   undone, and a tracker you can read." Three things a non-technical owner
   can check for themselves from the first day. Section 05 of the page
   follows.

The honest number: the twenty questions came out about a fifth shorter, and
the two additions and the tier definition took most of it back. Prompt
12,072 characters before the review, 11,996 after; 12,756 in a link before,
12,694 after.

## 6. The lesson that ran the other way

Two days after the first version, the same shape (a bar, lettered options,
a guess) was used for the podcast's guest office (AGE-110, PR #70), where a
guest turns an idea into a run-sheet. James, 1 November: "I do think it may
result in a particularly onerous experience for the podcast guest. We took
the shape of the 20 questions for client introduction and probably
stretched it too far." The office became a conversation: one open question,
then only what the run-sheet still needs, six questions is plenty, ten the
ceiling, no progress bar, no lettered options. A shape that fits a client
being scoped does not fit a guest being welcomed.

## 7. What the brief is for

Two people read a brief that arrives and reply in plain words: what we would
build first, what we would leave out, and what it would cost each month,
with us or without us. No sequence, no drip, no CRM. The mail sits on our
own server.

Of the front page's ten questions, this touches Q1 (the client is buying the
scoping before and the running after; a working answer, not a settled one),
Q2 (is the brief what replaces the estimate? kept a question), Q3 (twenty
questions a non-technical person answers is the front end of an executable
specification) and Q5 (acceptance starts with who signs it off and whether a
person checks the model's output before it counts). It skirts Q4 (who is
accountable: whose name is next to it), Q7 (choosing twenty is taste; the
MVP is the free volume) and Q10 (running cost appetite is the honest half of
a bill, and "who runs it" is where an agency used to hide the retainer; we
have put it in the open instead).

---

## Notes for episode 3 (AGE-103)

- The ticket's outline was written on 29 October against the original set
  and names questions by their old numbers and groups ("Reality", the hour
  on a Tuesday, the always-on list). Record from the merged set: groups are
  The idea / Care needed / Running conditions / Shape / Owning it; the
  close is the unnumbered question; section 05 is "Insist on these".
- One example per group of four, from our own projects: the tennis league
  fixtures (replaced a spreadsheet, one number that mattered); GradeDrive
  (children's data in schools, what tier 3 changed); tutti.rocks (sub-ten
  milliseconds was the whole point) against the podcast engine (a day down
  costs nothing); the AI question as Abrar's review-at-scale point; "what
  must never happen", each of us for a current project.
- "Who is going to run this once it exists?" is where an agency used to
  hide the retainer. Say what we do instead and what it costs, if the price
  is settled by then; do not pretend to a rate card we do not have.
- Each host runs the twenty questions on a real idea beforehand and brings
  the brief.
- The toy's brand name is a trademark; describe it. The links are
  undocumented and could stop working; say so.

## Open

- The monthly price and what it covers. The site promises the number before
  the pitch three times; the tool sends people into a conversation expecting
  it.
- The "What AgentSee is" document in Linear, to be rewritten to the
  build-and-run cycle.
- Nobody has measured how many questions a real person answers, or how
  long it takes. The first briefs that arrive will say.
- The Grok launcher depends on Grok fetching the public skill file through
  Cloudflare; untested from a real Grok session since the rewrite.
- Whether the brief should carry a version or date, so a brief that arrives
  can be read against the set that produced it. It does not yet.
