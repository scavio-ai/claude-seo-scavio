#!/usr/bin/env bash
# Installer round trip in a throwaway HOME. Spends no credits: the first run
# skips the key check, the second uses a key the API rejects (401 is free).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAKE_HOME="$(mktemp -d)"
trap 'rm -rf "${FAKE_HOME}"' EXIT
export HOME="${FAKE_HOME}"
unset CLAUDE_SKILLS_DIR

fail() { echo "FAIL: $*" >&2; exit 1; }
mode_of() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

KEY="sk_test_install_roundtrip_0123456789"
OUT="$(SCAVIO_API_KEY="${KEY}" "${ROOT}/install.sh" --no-check 2>&1)"
echo "${OUT}" | grep -q "${KEY}" && fail "installer printed the key"

SKILL="${HOME}/.claude/skills/seo-scavio"
CONFIG="${HOME}/.config/claude-seo/scavio.json"
[ -f "${SKILL}/SKILL.md" ] || fail "SKILL.md not installed"
[ -f "${SKILL}/scavio_seo.py" ] || fail "scavio_seo.py not installed"
[ -f "${CONFIG}" ] || fail "config not written"
[ "$(mode_of "${CONFIG}")" = "600" ] || fail "config mode is $(mode_of "${CONFIG}"), want 600"
python3 -c "import json,sys; assert json.load(open(sys.argv[1]))['api_key'] == sys.argv[2]" "${CONFIG}" "${KEY}" \
    || fail "config does not hold the key"

# The installed script finds the key without SCAVIO_API_KEY.
python3 - "${SKILL}" <<'PY' || fail "installed script cannot load the key"
import sys
sys.path.insert(0, sys.argv[1])
import scavio_seo
assert scavio_seo.load_key({}) == "sk_test_install_roundtrip_0123456789"
PY

# A sibling integration's file must survive uninstall.
echo '{}' > "${HOME}/.config/claude-seo/other.json"
"${ROOT}/uninstall.sh" >/dev/null
[ -e "${SKILL}" ] && fail "skill not removed"
[ -e "${CONFIG}" ] && fail "config not removed"
[ -f "${HOME}/.config/claude-seo/other.json" ] || fail "uninstall removed another integration's file"
rm "${HOME}/.config/claude-seo/other.json"
"${ROOT}/uninstall.sh" >/dev/null
[ -e "${HOME}/.config/claude-seo" ] && fail "empty config folder left behind"

# A rejected key changes nothing.
if [ "${SKIP_NETWORK:-0}" != "1" ]; then
    if SCAVIO_API_KEY="sk_invalid_key_for_install_test" "${ROOT}/install.sh" >/dev/null 2>&1; then
        fail "installer accepted an invalid key"
    fi
    [ -e "${CONFIG}" ] && fail "config written after a rejected key"
    [ -e "${SKILL}" ] && fail "skill installed after a rejected key"
fi

echo "install round trip: OK"
