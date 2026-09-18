#!/usr/bin/env bash
#
# Is a handle free? Calibrated, so the answer means something.
#
#   ./tools/handle-probe.sh              # checks agentseework
#   ./tools/handle-probe.sh agentsee foo # checks several
#
# Why this exists as a script rather than a note of results: the results go
# stale (a handle can be taken tomorrow) and so do the *methods*. Between the
# August and September audits Instagram closed its unauthenticated endpoint and
# YouTube started 302ing every request. Both changes turn a naive probe into a
# confident wrong answer, which is worse than no answer.
#
# So every platform is measured against a known-TAKEN and a known-FREE control
# on the same run. If the controls don't separate, the platform is reported
# UNKNOWN and you go and use the signup form. A probe that cannot tell a real
# account from a nonsense one must never be allowed to say "free".
#
set -uo pipefail

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
FREE_CTL=zq7xnonsuchhandle42          # nobody will ever register this
TARGETS=("${@:-agentseework}")

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 -A "$UA" "$@"; }

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }
grey()  { printf '\033[33m%s\033[0m' "$1"; }

say() { # label verdict detail
  local v="$2" out
  case "$v" in
    FREE)    out=$(green FREE) ;;
    TAKEN)   out=$(red TAKEN) ;;
    *)       out=$(grey "$v") ;;
  esac
  printf '  %-14s %s %s\n' "$1" "$out" "${3:-}"
}

verdict() { # taken free target
  if [ "$1" = "$2" ]; then echo UNKNOWN
  elif [ "$3" = "$2" ]; then echo FREE
  elif [ "$3" = "$1" ]; then echo TAKEN
  else echo "ODD($3)"; fi
}

# label, url template with one %s, known-taken control, [follow]
http_probe() {
  local label="$1" fmt="$2" ctl="$3" follow="${4:-}"
  local t f x extra=()
  [ -n "$follow" ] && extra=(-L)
  t=$(code "${extra[@]}" "$(printf "$fmt" "$ctl")")
  f=$(code "${extra[@]}" "$(printf "$fmt" "$FREE_CTL")")
  x=$(code "${extra[@]}" "$(printf "$fmt" "$TARGET")")
  local v; v=$(verdict "$t" "$f" "$x")
  local note=""
  [ "$v" = UNKNOWN ] && note="(controls both $t — signup form is the only authority)"
  say "$label" "$v" "$note"
}

bluesky_probe() {
  local r
  r=$(curl -s --max-time 15 "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=${TARGET}.bsky.social")
  case "$r" in
    *Unable\ to\ resolve*) say bluesky FREE ;;
    *did:*)               say bluesky TAKEN ;;
    *)                    say bluesky UNKNOWN "$r" ;;
  esac
  # The domain handle is the one we actually want; it is free until we claim it.
  r=$(curl -s --max-time 15 "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=agentsee.work")
  case "$r" in
    *did:*) say "bluesky/dns" TAKEN "agentsee.work resolves — that should be us" ;;
    *)      say "bluesky/dns" FREE  "agentsee.work unclaimed" ;;
  esac
}

tiktok_probe() {
  # TikTok returns 200 for a real profile and 200 for nonsense, so the obvious
  # probe is useless. oEmbed is not: it 400s on a handle that does not exist.
  # Calibrated against tiktok/nasa (200) and four invented handles (400).
  local r
  r=$(code "https://www.tiktok.com/oembed?url=https://www.tiktok.com/@$TARGET")
  case "$r" in
    200) say tiktok TAKEN ;;
    400) say tiktok FREE ;;
    *)   say tiktok UNKNOWN "oembed returned $r — neither control value" ;;
  esac
}

twitch_probe() {
  local q r
  q="{\"query\":\"{user(login:\\\"$TARGET\\\"){id}}\"}"
  r=$(curl -s --max-time 15 -H 'Client-ID: kimne78kx3ncx6brgo4mv6wki5h1ko' \
        -H 'Content-Type: application/json' -d "$q" https://gql.twitch.tv/gql)
  case "$r" in
    *'"user":null'*) say twitch FREE ;;
    *'"id":"'*)      say twitch TAKEN ;;
    *)               say twitch UNKNOWN "$r" ;;
  esac
}

for TARGET in "${TARGETS[@]}"; do
  printf '\n\033[1m%s\033[0m\n' "$TARGET"

  # Identity handles.
  http_probe github      'https://api.github.com/users/%s'                        torvalds
  http_probe x           'https://x.com/%s'                                       jack
  http_probe youtube     'https://www.youtube.com/@%s'                            mkbhd    follow
  twitch_probe
  http_probe kick        'https://kick.com/api/v2/channels/%s'                    xqc
  bluesky_probe
  http_probe mastodon    'https://mastodon.social/api/v1/accounts/lookup?acct=%s' Gargron
  http_probe substack    'https://%s.substack.com'                                bariweiss
  http_probe patreon     'https://www.patreon.com/%s'                             patreon
  http_probe linkedin    'https://www.linkedin.com/company/%s'                    microsoft
  # Both of these normalise case with a redirect, so they MUST be followed:
  # huggingface.co/api/users/agentsee 307s to .../AgentSEE, a real account.
  # Unfollowed, that reads as neither control and the probe gives up.
  http_probe huggingface 'https://huggingface.co/api/users/%s/overview'           julien-c follow
  http_probe dockerhub   'https://hub.docker.com/v2/users/%s/'                    docker   follow

  # Package NAMESPACES, not user handles. Worth holding anyway: a name someone
  # can guess, published by someone who isn't us, is a supply-chain problem.
  http_probe npm-pkg     'https://registry.npmjs.org/%s'                          express
  http_probe pypi-pkg    'https://pypi.org/pypi/%s/json'                          requests

  tiktok_probe

  # Known-dead probes, listed so nobody assumes they were forgotten.
  say instagram UNKNOWN "web_profile_info returns 401 for everything since ~Sep 2026"
  say threads   UNKNOWN "Instagram's namespace — same answer"
  say reddit    UNKNOWN "403 from datacentre IPs"
  say facebook  UNKNOWN "never probeable"
done
echo
