#!/bin/bash
# Makes a copy of the profile with a random removal password, and puts the
# password on paper (or in a file) without showing it on screen.
#
# Keep this script in the same folder as no-shortform.mobileconfig, then in
# Terminal on the Mac:
#   bash ~/Downloads/set-removal-password.sh             # prints the password
#   bash ~/Downloads/set-removal-password.sh --to-file   # no printer
#
# Output: no-shortform.locked.mobileconfig in the same folder. Install that
# with Apple Configurator, then delete it: the password is readable inside it.
# Uses only tools built into macOS (openssl, plutil, lpr).

set -euo pipefail
TO_FILE=0
if [ "${1:-}" = "--to-file" ]; then TO_FILE=1; shift; fi
TEMPLATE="${1:-$(dirname "$0")/no-shortform.mobileconfig}"
PLACEHOLDER="CHANGE_ME_REMOVAL_PASSWORD"

[ -f "$TEMPLATE" ] || { echo "Profile not found: $TEMPLATE" >&2; exit 2; }
grep -q "$PLACEHOLDER" "$TEMPLATE" || { echo "$TEMPLATE has no $PLACEHOLDER placeholder. Use an unedited template." >&2; exit 2; }

OUT="$(dirname "$TEMPLATE")/no-shortform.locked.mobileconfig"
PW_FILE="$(mktemp)"
trap 'rm -f "$PW_FILE"' EXIT

# Letters and digits only, so it is easy to copy from paper and safe in XML.
PW=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-28)
[ "${#PW}" -ge 20 ] || { echo "Could not generate a password; run again." >&2; exit 1; }
{
  echo "ShortformBeGone profile removal password"
  echo "Created $(date '+%Y-%m-%d %H:%M')"
  echo
  echo "$PW"
} > "$PW_FILE"

sed "s|$PLACEHOLDER|$PW|" "$TEMPLATE" > "$OUT"
unset PW
plutil -lint "$OUT" > /dev/null

if [ "$TO_FILE" -eq 1 ]; then
  KEEP="$(dirname "$TEMPLATE")/removal-password.txt"
  cp "$PW_FILE" "$KEEP"
  echo "Password written to: $KEEP"
  echo "Copy it onto paper for your keyholder without reading it aloud, then delete the file."
else
  lpr "$PW_FILE"
  echo "Password sent to the default printer. Hand the page to your keyholder."
fi
echo "Locked profile: $OUT"
echo "Install it with Apple Configurator, check it works, then delete it:"
echo "  rm \"$OUT\""
