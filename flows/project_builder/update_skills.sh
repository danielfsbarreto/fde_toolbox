#!/usr/bin/env bash
# Refresh the CrewAI skills the builder's agents load (src/project_builder/skills) from github.com/crewaiinc/skills.
# Deployed with the flow, so a new release of the skills reaches AMP on the next deploy. Records the commit in SOURCE.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
target="$here/src/project_builder/skills"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone -q --depth 1 https://github.com/crewaiinc/skills "$tmp/skills"
rm -rf "$target"
mkdir -p "$target"
cp -R "$tmp/skills/skills/." "$target/"
cp "$tmp/skills/LICENSE" "$target/LICENSE"
echo "github.com/crewaiinc/skills $(git -C "$tmp/skills" log -1 --format='%H %cs')" > "$target/SOURCE"
echo "Skills updated: $(cat "$target/SOURCE")"
