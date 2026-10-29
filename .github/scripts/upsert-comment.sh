#!/usr/bin/env bash
# Create or update the bot comment that starts with a given marker on a pull request.
#   upsert-comment.sh <marker> <file>   (needs GH_TOKEN, REPO, PR)
set -euo pipefail
marker="$1"
file="$2"
id=$(gh api "repos/$REPO/issues/$PR/comments" --paginate \
  --jq ".[] | select(.body | startswith(\"$marker\")) | .id" | head -n1)
if [ -n "$id" ]; then
  gh api -X PATCH "repos/$REPO/issues/comments/$id" -F "body=@$file" > /dev/null
else
  gh api "repos/$REPO/issues/$PR/comments" -F "body=@$file" > /dev/null
fi
