#!/usr/bin/env bash
# Scavio for Claude SEO: installer (macOS and Linux).
#
# Copies the seo-scavio skill to ~/.claude/skills/seo-scavio/ and stores your
# Scavio API key in ~/.config/claude-seo/scavio.json (mode 0600). It checks the
# key with a free call before writing anything. Claude SEO's own files are not
# changed, and nothing is installed with pip or npm.
#
# The key comes from SCAVIO_API_KEY when it is set, otherwise from a hidden
# prompt. Pass --no-check to skip the key check (for offline installs).
set -euo pipefail

CHECK=1
for arg in "$@"; do
    case "$arg" in
        --no-check) CHECK=0 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

SKILLS_DIR="${CLAUDE_SKILLS_DIR:-${HOME}/.claude/skills}"
TARGET="${SKILLS_DIR}/seo-scavio"
CONFIG_DIR="${HOME}/.config/claude-seo"
CONFIG="${CONFIG_DIR}/scavio.json"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

echo "Scavio for Claude SEO: installer"

command -v python3 >/dev/null 2>&1 || { echo "Python 3.9 or later is required." >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || { echo "Python 3.9 or later is required." >&2; exit 1; }

KEY="${SCAVIO_API_KEY:-}"
if [ -z "${KEY}" ]; then
    echo "Get a key at https://dashboard.scavio.dev/sign-up (new accounts get 50 free credits, no card)."
    read -rsp "Scavio API key: " KEY
    echo
fi
if [ -z "${KEY}" ]; then
    echo "No key given. Nothing was changed." >&2
    exit 1
fi

if [ "${CHECK}" -eq 1 ]; then
    # Free call (0 credits). The key travels in the environment, not argv.
    if ! SCAVIO_API_KEY="${KEY}" python3 "${SOURCE_DIR}/skills/seo-scavio/scavio_seo.py" check >/dev/null; then
        echo "The key check failed (see the line above). Nothing was changed." >&2
        exit 1
    fi
    echo "Key accepted."
fi

mkdir -p "${CONFIG_DIR}"
chmod 700 "${CONFIG_DIR}" 2>/dev/null || true
CLAUDE_SEO_SECRET="${KEY}" python3 - "${CONFIG}" <<'PY'
import json, os, sys, tempfile
path = sys.argv[1]
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".scavio.", suffix=".json")
try:
    os.chmod(tmp, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"api_key": os.environ["CLAUDE_SEO_SECRET"]}, fh)
    os.replace(tmp, path)
except BaseException:
    if os.path.exists(tmp):
        os.unlink(tmp)
    raise
PY
echo "Saved the key to ${CONFIG} (readable only by you)."

mkdir -p "${TARGET}"
cp "${SOURCE_DIR}/skills/seo-scavio/SKILL.md" "${TARGET}/SKILL.md"
cp "${SOURCE_DIR}/skills/seo-scavio/scavio_seo.py" "${TARGET}/scavio_seo.py"
echo "Installed the skill to ${TARGET}."

echo
echo "Done. Open a new Claude Code session and try:"
echo "  /seo-scavio visibility \"best crm for small agencies\" --brand YourBrand --domain yourbrand.com"
