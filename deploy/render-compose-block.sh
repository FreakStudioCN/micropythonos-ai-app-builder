#!/usr/bin/env bash
# Render compose.mpos-services.yml with every secret generated and consistent.
#
# The template has five credentials written across nine places, four of which
# must agree pairwise (DB password, MinIO root user, MinIO root password, app
# key, app secret). Filling those by hand is the failure mode this script
# exists to remove: a mismatch is not caught at edit time, it surfaces as a
# container that will not start, or worse, an S3 403 an hour later.
#
# Usage:
#   DEEPSEEK_API_KEY=sk-... ./render-compose-block.sh            # -> stdout
#   DEEPSEEK_API_KEY=sk-... ./render-compose-block.sh -o out.yml # -> 0600 file
#
# Then paste the output into the production host's existing compose.yml.
# Rotating one credential later: re-run, and copy only the lines you meant to
# change. Never hand-edit one half of a pair.

set -euo pipefail

TEMPLATE="$(cd "$(dirname "$0")" && pwd)/compose.mpos-services.yml"
OUT=""

while [ $# -gt 0 ]; do
  case "$1" in
    -o) OUT="${2:?-o needs a path}"; shift 2 ;;
    *)  echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

test -f "${TEMPLATE}"

# Externally issued, so it is the one value we cannot generate — and the one
# that can carry characters compose would interpolate. Reject anything outside
# the DeepSeek key charset rather than trying to escape it.
: "${DEEPSEEK_API_KEY:?set DEEPSEEK_API_KEY (the sk-... key) in the environment}"
if ! printf '%s' "${DEEPSEEK_API_KEY}" | grep -Eq '^sk-[A-Za-z0-9_-]+$'; then
  echo "DEEPSEEK_API_KEY does not look like sk-<alnum>. Refusing to render:" >&2
  echo "a key containing \$ or | would break compose interpolation or this script." >&2
  exit 1
fi

# Pure hex on purpose: no '$' for compose to interpolate, and no percent-encoding
# needed when the DB password is embedded in a postgresql:// URL.
DB_PW="$(openssl rand -hex 16)"
ROOT_USER="$(openssl rand -hex 16)"
ROOT_PW="$(openssl rand -hex 32)"
APP_KEY="$(openssl rand -hex 8)"
APP_SECRET="$(openssl rand -hex 32)"

rendered="$(sed \
  -e "s|CHANGE-ME-DB-PW-hex16|${DB_PW}|g" \
  -e "s|CHANGE-ME-root-user-hex16|${ROOT_USER}|g" \
  -e "s|CHANGE-ME-root-pw-hex32|${ROOT_PW}|g" \
  -e "s|CHANGE-ME-app-key-hex8|${APP_KEY}|g" \
  -e "s|CHANGE-ME-app-secret-hex32|${APP_SECRET}|g" \
  -e "s|CHANGE-ME-sk-deepseek|${DEEPSEEK_API_KEY}|g" \
  "${TEMPLATE}")"

# The launch sequence scans the host file for leftovers; catch it here instead,
# where the token that was missed is still obvious.
if printf '%s' "${rendered}" | grep -n 'CHANGE-ME'; then
  echo "^ unsubstituted placeholder(s) — the template gained a token this script does not know" >&2
  exit 1
fi

if [ -n "${OUT}" ]; then
  # umask only governs CREATION. Redirecting into a file that already exists
  # keeps its current mode, so a rerun over a 0644 path would leave live
  # credentials world-readable while this still printed "0600". chmod covers
  # both cases.
  ( umask 077; printf '%s\n' "${rendered}" > "${OUT}" )
  chmod 600 "${OUT}"
  echo "wrote ${OUT} (0600) — contains live secrets, transmit over an encrypted channel only" >&2
else
  printf '%s\n' "${rendered}"
fi
