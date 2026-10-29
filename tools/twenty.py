#!/usr/bin/env python3
"""
The twenty questions on the site, from the one copy of the skill.

    ./tools/twenty.py            # writes the page's blocks and the public skill file
    ./tools/twenty.py --check    # exits 1 if either is stale, writes nothing

The skill lives at .claude/skills/twenty-questions/SKILL.md, where Claude Code
finds it. This writes two things from it, so there is one copy to edit:

  public/twenty-questions/SKILL.md     the file a visitor downloads for Claude's
                                       Skills or for Claude Code, as is
  public/twenty-questions/index.html   the blocks between its twenty markers:
                                       the prompt (the skill without its
                                       frontmatter) for reading and copying, and
                                       the open-in links, each carrying either
                                       the whole prompt or the launcher below in
                                       its q parameter

The q parameter is what the chat products take on their new-chat pages
(chatgpt.com, claude.ai/new, grok.com). None of them documents it and each has
been seen to change, so the page never depends on it: the prompt is on the
page to copy, and the skill file is there to download. Claude and ChatGPT
take the whole prompt in the link (both tried, 29 October 2026). Grok answers
431 (request header too large) to a link that long, so its link carries a
short launcher that asks it to open the public skill file instead. Output is
deterministic: a re-run with nothing changed writes nothing new.
"""

import argparse
import html
import pathlib
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "twenty-questions" / "SKILL.md"
PUBLIC_SKILL = ROOT / "public" / "twenty-questions" / "SKILL.md"
PAGE = ROOT / "public" / "twenty-questions" / "index.html"

# Name, the new-chat URL that takes the prompt in q, and what the link carries:
# the whole prompt, or the launcher for a product that refuses a link that
# long. Order is the order on the page.
OPEN_IN = [
    ("Claude", "https://claude.ai/new?q=", "prompt"),
    ("ChatGPT", "https://chatgpt.com/?q=", "prompt"),
    ("Grok", "https://grok.com/?q=", "launcher"),
]

# What a launcher link says. The assistant opens the skill from the site, so
# the link stays short and the questions stay in one place.
SKILL_URL = "https://agentsee.work/twenty-questions/SKILL.md"
LAUNCHER = (
    f"Open {SKILL_URL} and follow the instructions in it exactly, as the assistant: "
    "ask me its twenty questions one at a time, starting now with question 1. "
    "If you cannot open links, tell me and I will paste the questions in."
)


def prompt_of(skill_text):
    """The skill without its frontmatter: what a person pastes into a chat."""
    if not skill_text.startswith("---\n"):
        return skill_text.strip() + "\n"
    end = skill_text.find("\n---\n", 4)
    if end < 0:
        sys.exit(f"{SKILL}: frontmatter never closes")
    return skill_text[end + len("\n---\n") :].strip() + "\n"


def encoded(text):
    """Text as a query value: spaces as +, and the punctuation a query may
    carry unencoded left alone, so the link is as short as it can be."""
    return urllib.parse.quote_plus(text, safe="!$'()*,:/@")


def links_html(prompt):
    lines = []
    for name, base, carries in OPEN_IN:
        q = encoded(prompt if carries == "prompt" else LAUNCHER)
        lines.append(f'      <a href="{base}{q}" target="_blank" rel="noopener">Open in {html.escape(name)}</a>')
    return "\n".join(lines) + "\n"


def prompt_html(prompt):
    return f'      <pre id="prompt" tabindex="0">{html.escape(prompt)}</pre>\n'


def replace_block(page, tag, body):
    start = f"      <!-- twenty:{tag}: written by tools/twenty.py -->\n"
    end = f"      <!-- /twenty:{tag} -->\n"
    i = page.find(start)
    j = page.find(end)
    if i < 0 or j < 0 or j < i:
        sys.exit(f"{PAGE}: markers for twenty:{tag} missing or out of order")
    return page[: i + len(start)] + body + page[j:]


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the page or the public skill is stale")
    args = ap.parse_args()

    skill_text = SKILL.read_text(encoding="utf-8")
    prompt = prompt_of(skill_text)
    page = PAGE.read_text(encoding="utf-8")
    new_page = replace_block(page, "links", links_html(prompt))
    new_page = replace_block(new_page, "prompt", prompt_html(prompt))
    public_skill = PUBLIC_SKILL.read_text(encoding="utf-8") if PUBLIC_SKILL.exists() else None

    stale = []
    if new_page != page:
        stale.append(PAGE)
    if public_skill != skill_text:
        stale.append(PUBLIC_SKILL)

    if args.check:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}", file=sys.stderr)
        sys.exit(1 if stale else 0)

    for p in stale:
        if p == PAGE:
            PAGE.write_text(new_page, encoding="utf-8")
        else:
            PUBLIC_SKILL.parent.mkdir(parents=True, exist_ok=True)
            PUBLIC_SKILL.write_text(skill_text, encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)}")
    if not stale:
        print("nothing to do")
    print(f"prompt: {len(prompt)} characters, {len(encoded(prompt))} in a link; the launcher is {len(encoded(LAUNCHER))}")


if __name__ == "__main__":
    main()
