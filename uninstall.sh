#!/usr/bin/env bash
# Scavio for Claude SEO: uninstaller (macOS and Linux).
# Removes the seo-scavio skill and the stored key. Touches nothing else.
set -euo pipefail

SKILLS_DIR="${CLAUDE_SKILLS_DIR:-${HOME}/.claude/skills}"
TARGET="${SKILLS_DIR}/seo-scavio"
CONFIG_DIR="${HOME}/.config/claude-seo"
CONFIG="${CONFIG_DIR}/scavio.json"

if [ -d "${TARGET}" ]; then
    rm -rf "${TARGET}"
    echo "Removed ${TARGET}"
else
    echo "No skill at ${TARGET}"
fi

if [ -f "${CONFIG}" ]; then
    rm -f "${CONFIG}"
    echo "Removed ${CONFIG}"
fi
# Other Claude SEO integrations may keep files here; remove the folder only if empty.
rmdir "${CONFIG_DIR}" 2>/dev/null || true

echo "Done."
