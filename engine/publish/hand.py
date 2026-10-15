"""
The adapter for what a person posts: the LinkedIn page until incorporation,
and every platform whose API adapter is not written yet. Publishing here
means the card is ready: the Posts screen shows the text to copy, the file
to attach, the short link and a link that opens the platform's composer with
the text already in it; the person posts, pastes the permalink back, and
that paste is what marks the post published.
"""

SCHEDULES = False
SENDS = False  # a card, not a send: no late guard
NEEDS = ""


def ready(env):
    return True


def describe(post, episode, release):
    return f"a card for {post['account']} on its day: {post['title']}"


def publish(post, episode, release, files, env, account, log):
    return {"status": "hand", "note": "card ready"}
