#!/usr/bin/env bash
# Update Formula/deployguard.rb with the correct sha256 for a release tarball.
# Run this after creating a GitHub release tag.
#
# Usage: ./scripts/update-formula-sha.sh v0.1.0
set -euo pipefail

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  echo "Usage: $0 <version>  (e.g. v0.1.0)" >&2
  exit 1
fi

BARE="${VERSION#v}"
URL="https://github.com/your-org/deployguard/archive/refs/tags/${VERSION}.tar.gz"

echo "Fetching tarball: $URL"
SHA256="$(curl -fsSL "$URL" | shasum -a 256 | awk '{print $1}')"
echo "sha256: $SHA256"

FORMULA="Formula/deployguard.rb"
# Update URL
sed -i '' "s|archive/refs/tags/v[0-9.]*.tar.gz|archive/refs/tags/${VERSION}.tar.gz|g" "$FORMULA"
# Update sha256
sed -i '' "s|sha256 \"REPLACE_WITH_SHA256_OF_RELEASE_TARBALL\"|sha256 \"${SHA256}\"|g" "$FORMULA"
# Update version in url line
sed -i '' "s|/v[0-9.]*\.tar\.gz|/${VERSION}.tar.gz|g" "$FORMULA"

echo "Updated $FORMULA for ${VERSION}"
echo ""
echo "Next: run 'brew update-python-resources Formula/deployguard.rb' to regenerate resource sha256s"
