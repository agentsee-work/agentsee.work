# Issue tracker: Linear

Issues and specs for this repo live in Linear, workspace team **AgentSee** (identifier prefix `AGE`). Use the Linear MCP tools (`mcp__linear__*`) for every operation. Never use `gh issue`; GitHub Issues are not used.

## Conventions

- **Create an issue**: `save_issue` with `team: "AgentSee"`, `title`, and a Markdown `description` with real newlines.
- **Read an issue**: `get_issue` with the identifier (e.g. `AGE-12`), `includeRelations: true` when blocking matters; `list_comments` for the thread.
- **List issues**: `list_issues` with `team: "AgentSee"` plus `label`, `state`, `assignee`, `project` or `parentId` filters.
- **Comment**: `save_comment` with `issueId`.
- **Apply / remove labels**: `save_issue` with `addLabels` / `removeLabels`. If a label doesn't exist yet, create it with `save_issue_label` first.
- **Close**: comment with the outcome, then `save_issue` with `state: "Done"`. Use `Canceled` for won't-do and `Duplicate` with `duplicateOf` for duplicates.
- **Statuses**: Backlog, Todo, In Progress, Done, Canceled, Duplicate.
- **People**: refer to users by name or email. Pass `"me"` for the user running the session.

## Podcast episodes and guests

Everything not yet recorded lives in the project **Podcast** (`P-AGE-2`), one issue per episode, created before the running order is known. Two shapes, both set out with a paste-in skeleton in the project document "How a podcast idea becomes an issue":

- **Episode topic**: title `Episode topic: <working title> (<proposer>)`, assigned to the proposer, in the shape of AGE-51 and AGE-52 (the premise, a run length, an outline with rough minutes, questions to put to each other, worth adding, traps, an *Episode prep* checklist).
- **Guest episode**: title `Guest episode: <Name> — <working topic>`, one label from the group **Guest status** (`Confirmed`, `Warm`, `Awaiting reply`), priority from the status (Urgent for the next to record, High for Confirmed, Medium for Warm, Low for Awaiting reply), the description covering who they are, how we know them, their status and what they said, the angle and the site question it leans on, caveats, the next action, then the same checklist. Create the issue whether or not they have said yes.

Given a raw idea (a message, a name, a sentence), write the issue in the matching shape; do not number it. The site's questions are quoted into the issue, not referenced by number, because the list on the site changes.

## When a skill says "publish to the issue tracker"

Create a Linear issue in team AgentSee.

## When a skill says "fetch the relevant ticket"

`get_issue` with `includeRelations: true`, then `list_comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **sub-issues** as tickets.

- **Map**: one issue labelled `wayfinder:map`, holding the Destination / Notes / Decisions-so-far / Not-yet-specified / Out-of-scope body.
- **Child ticket**: `save_issue` with `parentId: <map identifier>` and one label from `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, `wayfinder:task`. Unclaimed tickets are unassigned.
- **Blocking**: Linear's native relations. After all tickets exist, wire edges in a second pass with `save_issue` `blockedBy: [<identifiers>]`. A ticket is unblocked when every blocker is Done or Canceled.
- **Frontier query**: `list_issues` with `parentId: <map>`, keep open (not Done/Canceled/Duplicate) and unassigned, then `get_issue` with `includeRelations: true` and drop any with an open blocker. First in creation order wins.
- **Claim**: `save_issue` with `assignee: "me"` and `state: "In Progress"`, as the session's first write.
- **Resolve**: `save_comment` with the answer, `save_issue` `state: "Done"`, then patch the map's Decisions-so-far with a one-line gist linking the ticket.
