#!/usr/bin/env bash
# Put a merge-watch caller into every repository of the account.
#
# Why a caller and not inheritance: GitHub shares issue templates and community
# health files across an account, but Actions are per repository. So coverage of
# 43 repositories needs one small file in each, calling the logic that lives in
# local-lab. A fix there then applies everywhere without touching 43 repos.
#
# Usage:
#   scripts/install-merge-watch.sh              # report only, changes nothing
#   scripts/install-merge-watch.sh --apply      # open PRs in repositories that lack the file
#
# Requires: gh, authenticated with repo scope.
#
# Safety:
#   - archived repositories are skipped: they are read-only, a PR cannot land;
#   - an existing workflow file is never overwritten;
#   - every change is a pull request, never a direct push to the default branch;
#   - the only file touched is .github/workflows/merge-watch.yml.

set -euo pipefail

OWNER="${OWNER:-TeaShaman-cyber}"
SOURCE_REPO="TeaShaman-cyber/local-lab"
PATH_IN_REPO=".github/workflows/merge-watch.yml"
BRANCH="chore/merge-watch"

APPLY=0
ONLY=""
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --only=*) ONLY="${arg#--only=}" ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

CALLER='name: merge-watch

# Coverage for a merge nobody asked for. The logic lives in TeaShaman-cyber/local-lab;
# this file only calls it, so a fix lands once and applies everywhere.
#
# An expected merge carries a comment on the PR before merging:
#     merge-intent: <head_sha> issue: #N     (or)     reason: <why>

on:
  pull_request:
    types: [closed]

permissions:
  contents: read
  issues: write
  pull-requests: read

jobs:
  merge-watch:
    uses: TeaShaman-cyber/local-lab/.github/workflows/merge-watch.yml@main
    secrets: inherit
'

repos=$(gh repo list "$OWNER" --limit 200 --json name,isArchived,defaultBranchRef \
  --jq '.[] | "\(.name)\t\(.isArchived)\t\(.defaultBranchRef.name)"')

have=0
skipped_archived=0
need=0
installed=0
failed=0
failed_list=""

while IFS=$'\t' read -r name archived branch; do
  [ -z "$name" ] && continue
  slug="$OWNER/$name"

  # --only=<repo> limits the run to one repository. Used for a pilot before
  # fanning out: if the caller is wrong, one repository is broken, not 42.
  if [ -n "$ONLY" ] && [ "$name" != "$ONLY" ]; then
    continue
  fi

  if [ "$archived" = "true" ]; then
    skipped_archived=$((skipped_archived + 1))
    continue
  fi

  if gh api "repos/$slug/contents/$PATH_IN_REPO" >/dev/null 2>&1; then
    have=$((have + 1))
    continue
  fi

  need=$((need + 1))
  if [ "$APPLY" -eq 0 ]; then
    printf 'NEEDS  %s\n' "$slug"
    continue
  fi

  if [ -z "$branch" ] || [ "$branch" = "null" ]; then
    printf 'FAIL   %s (no default branch)\n' "$slug"
    failed=$((failed + 1)); failed_list="$failed_list $name(no-branch)"
    continue
  fi

  base_sha=$(gh api "repos/$slug/git/ref/heads/$branch" --jq .object.sha 2>/dev/null || true)
  if [ -z "$base_sha" ]; then
    printf 'FAIL   %s (cannot read %s head)\n' "$slug" "$branch"
    failed=$((failed + 1)); failed_list="$failed_list $name(no-head)"
    continue
  fi

  if ! gh api -X POST "repos/$slug/git/refs" \
        -f ref="refs/heads/$BRANCH" -f sha="$base_sha" >/dev/null 2>&1; then
    printf 'FAIL   %s (cannot create branch)\n' "$slug"
    failed=$((failed + 1)); failed_list="$failed_list $name(no-branch-create)"
    continue
  fi

  content_b64=$(printf '%s' "$CALLER" | base64 | tr -d '\n')
  if ! gh api -X PUT "repos/$slug/contents/$PATH_IN_REPO" \
        -f message="chore(guards): call merge-watch from local-lab" \
        -f content="$content_b64" -f branch="$BRANCH" >/dev/null 2>&1; then
    printf 'FAIL   %s (cannot write file)\n' "$slug"
    failed=$((failed + 1)); failed_list="$failed_list $name(no-write)"
    continue
  fi

  pr_url=$(gh pr create --repo "$slug" --base "$branch" --head "$BRANCH" \
      --title "chore(guards): merge-watch" \
      --body "Adds a merge-watch caller. The logic lives in $SOURCE_REPO; this file only calls it, so a fix lands there once and applies here too.

Detects a merge that nobody asked for: a merged PR with no \`merge-intent: <head_sha> issue: #N\` comment. Detection only — it never blocks a merge and never fails the build." 2>/dev/null || true)

  if [ -z "$pr_url" ]; then
    printf 'FAIL   %s (file written, PR failed)\n' "$slug"
    failed=$((failed + 1)); failed_list="$failed_list $name(no-pr)"
    continue
  fi

  printf 'PR     %s -> %s\n' "$slug" "$pr_url"
  installed=$((installed + 1))
done <<< "$repos"

echo
echo "=== merge-watch coverage ==="
echo "already have:   $have"
echo "archived:       $skipped_archived (skipped, read-only)"
echo "needs caller:   $need"
if [ "$APPLY" -eq 1 ]; then
  echo "PRs opened:     $installed"
  echo "failed:         $failed"
  [ -n "$failed_list" ] && echo "failed repos:  $failed_list"
else
  echo
  echo "dry run: nothing changed. Re-run with --apply to open PRs."
fi
