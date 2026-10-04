#!/usr/bin/env bash
# Publish the dashboard to a static Hugging Face Space from this machine.
# Same result as .github/workflows/deploy.yml: data rebuilt from csv/, only the page and its data uploaded.
#
#   scripts/deploy_static.sh <owner>/<space>          e.g. scripts/deploy_static.sh r4mkum4r/return-pulse
#
# Needs: the hf CLI, signed in (hf auth login). Creates the Space (static, public) if it doesn't exist.
set -euo pipefail

SPACE="${1:?Usage: scripts/deploy_static.sh <owner>/<space>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HF="$(command -v hf || echo "$HOME/.local/bin/hf")"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

cd "$ROOT"
SHA="$(git rev-parse --short HEAD)"
if [ -n "$(git status --porcelain -- frontend app scripts csv)" ]; then
  SHA="$SHA-dirty"
  echo "Note: uncommitted changes in frontend/, app/, scripts/ or csv/ are included ($SHA)."
fi

python3 scripts/build_sample_data.py

# Only the page and its data. No server code, no answer sheet, no binary files.
cp -R frontend/index.html frontend/css frontend/js frontend/sample "$OUT/"
python3 - "$OUT/index.html" "$SHA" <<'EOF'
import sys, pathlib
p, sha = pathlib.Path(sys.argv[1]), sys.argv[2]
s = p.read_text()
for old, new in [('<meta name="return-pulse-data" content="api">', '<meta name="return-pulse-data" content="sample">'),
                 ('<meta name="return-pulse-build" content="local">', f'<meta name="return-pulse-build" content="{sha}">')]:
    assert old in s, f"index.html no longer contains {old}"
    s = s.replace(old, new)
p.write_text(s)
EOF
cat deploy/space_header.md README.md > "$OUT/README.md"

"$HF" repos create "$SPACE" --repo-type space --space-sdk static --public --exist-ok >/dev/null
"$HF" upload "$SPACE" "$OUT" . --repo-type space --delete "css/*" --delete "js/*" --delete "sample/*" --commit-message "Deploy $SHA"
echo "Published $SHA to https://huggingface.co/spaces/$SPACE"
