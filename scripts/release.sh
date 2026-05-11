#!/usr/bin/env bash
# Cut a release. Pre-flight checks → bump → verify → commit → tag → push → GitHub release.
#
# Usage: scripts/release.sh <version>
#   e.g. scripts/release.sh 0.4.0
#
# Assumes you've already moved CHANGELOG.md's [Unreleased] entries under a [<version>] heading.

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <version>" >&2
    exit 1
fi

version="$1"
tag="v$version"

cd "$(git rev-parse --show-toplevel)"

# Pre-flight
current_branch="$(git symbolic-ref --short HEAD)"
if [ "$current_branch" != "main" ]; then
    echo "error: must be on main, got '$current_branch'" >&2
    exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
    echo "error: working tree has uncommitted changes" >&2
    git status --short
    exit 1
fi
if git rev-parse "$tag" >/dev/null 2>&1; then
    echo "error: tag $tag already exists" >&2
    exit 1
fi
if ! grep -q "^## \[$version\]" CHANGELOG.md; then
    echo "error: CHANGELOG.md has no [$version] section. Move [Unreleased] entries first." >&2
    exit 1
fi

git pull --ff-only origin main

# Bump version + lockfile
case "$(uname -s)" in
    Darwin) sed -i '' "s/^version = .*/version = \"$version\"/" pyproject.toml ;;
    *)      sed -i    "s/^version = .*/version = \"$version\"/" pyproject.toml ;;
esac
uv sync --group dev

# Verify everything CI checks, locally first
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest

# Commit, tag, push
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore: release $tag"
git tag "$tag"
git push origin main "$tag"

# Extract this version's CHANGELOG section as release notes
notes=$(awk -v ver="$version" '
    /^## \[/ {
        if (in_section) exit
        if ($0 ~ "\\[" ver "\\]") { in_section = 1; next }
    }
    in_section { print }
' CHANGELOG.md)

if [ -z "$notes" ]; then
    echo "warning: extracted CHANGELOG notes are empty; falling back to auto-generated notes" >&2
    gh release create "$tag" --title "$tag" --generate-notes
else
    gh release create "$tag" --title "$tag" --notes "$notes"
fi

echo "✓ Released $tag"
