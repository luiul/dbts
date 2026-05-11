# scripts

Maintainer scripts for `dbts`. Run them from anywhere inside the repo — they `cd` to the git root themselves.

## `release.sh`

Cuts a release end-to-end: pre-flight checks → optional version bump → local CI → tag → push → GitHub release → PyPI publish (via the existing `publish.yml` workflow).

```bash
./scripts/release.sh <version>
# e.g.
./scripts/release.sh 0.6.0
```

### Two release workflows

The script supports both styles. Pick whichever matches how you've been working.

**A. Clean release from a clean tree (recommended)**

```bash
# 1. Make your changes on main, commit them as feature/fix commits.
# 2. Update CHANGELOG.md: move [Unreleased] entries under a new [<version>] heading.
git add CHANGELOG.md
git commit -m "docs: prep CHANGELOG for v<version>"

# 3. Run the release script. It bumps pyproject.toml, re-syncs uv.lock,
#    runs the full check suite, commits the bump, tags, pushes, releases.
./scripts/release.sh 0.6.0
```

**B. Pre-bumped tree (when iteration already touched the version)**

If you've been editing `pyproject.toml` during development and `version` is already at the target — for example, when working on a release in flight — the script detects that nothing's left to commit and uses `HEAD` as the release commit:

```bash
# pyproject.toml already says version = "0.6.0"
# CHANGELOG.md already has a [0.6.0] section
# All changes are committed; working tree is clean.

./scripts/release.sh 0.6.0
# → "no version-bump changes to commit; using HEAD as the release commit"
# → tag, push, release.
```

### What the script does, in order

1. **Pre-flight checks** (any failure exits with a clear message):
   - Must be on `main`.
   - Working tree must be clean.
   - Tag `v<version>` must not already exist.
   - `CHANGELOG.md` must contain a `## [<version>]` heading.
2. `git pull --ff-only origin main` — refuses to release if your local `main` has diverged.
3. **Bump** `version = "..."` in `pyproject.toml` (idempotent — sed is a no-op if it's already correct).
4. `uv sync --group dev` — refreshes `uv.lock` if needed.
5. **Local CI gate**: `ruff format --check`, `ruff check`, `ty check`, `pytest`. Same checks GitHub Actions runs; failing locally avoids a broken release commit.
6. **Stage and conditionally commit** the bump. If nothing's staged after `git add`, the commit is skipped and `HEAD` becomes the release commit.
7. `git tag v<version> && git push origin main v<version>`. Pushing the tag triggers `.github/workflows/publish.yml`, which builds the wheel and uploads to PyPI via the trusted-publisher OIDC flow.
8. **Extract the `## [<version>]` block** from `CHANGELOG.md` via awk and `gh release create v<version> --notes ...`. Falls back to GitHub's auto-generated notes if extraction returns empty.

### Aborting / recovering

- If the local CI step fails, no commit, tag, or push has happened yet. Fix the failure and rerun.
- If `git push origin main` fails (someone else pushed in the meantime), pull/rebase and rerun. The bump commit and tag are local-only at that point.
- If the `gh release create` step fails after the tag is pushed: the PyPI workflow won't trigger because it watches for the GitHub release, not the tag. Manually run `gh release create v<version>` once the issue is fixed; the publish workflow will pick it up.
- If you need to redo a release before publishing succeeded: `git tag -d v<version> && git push origin :refs/tags/v<version>`, then rerun.

## Day-to-day development loop

For when you're just iterating on the codebase, no release in mind:

```bash
# One-time setup
uv sync --group dev
prek install   # installs the pre-commit hook locally (matches CI)

# Iteration
# ...edit src/dbts/*.py and tests/...
uv run pytest                      # tests
uv run ruff format .               # auto-fix formatting
uv run ruff check .                # lint
uv run ty check                    # typecheck

# Commit normally; prek runs ruff format / check / ty on staged Python files.
git commit -m "feat: ..."
```

Local-editable install if you want to test the CLI as you change source:

```bash
uv tool install --from . -e dbts --force --reinstall
# Now `dbts` on your PATH is the local editable copy.

# Switch back to the PyPI version later:
uv tool install dbts --force --reinstall
```

## Files in this directory

| File         | Purpose                                                |
|--------------|--------------------------------------------------------|
| `release.sh` | Cut a release. See above.                              |
| `README.md`  | This file.                                             |
