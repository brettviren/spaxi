#!/usr/bin/env bash
# Strategy 2, provider role: build Spack packages and publish them in
# a Spack binary build cache that end-users consume with spaxi.
#
# Usage: provider-spack.sh [spec] [mirror-dir]
set -eu

SPEC="${1:-zstd}"
MIRROR="${2:-$PWD/buildcache}"
SPACK="${SPACK:-$PWD/spack/bin/spack}"

# Isolate spack's config and cache under ./.cache/
. "$(dirname "$0")/spack-env.sh"

# Build (or reuse) the package in the spack install area.
"$SPACK" install "$SPEC"

# Push it and its dependencies to the binary build cache.  Sign the
# packages instead (drop --unsigned) if you maintain a gpg key that
# end-users import.
"$SPACK" buildcache push --unsigned "$MIRROR" "$SPEC"
"$SPACK" buildcache update-index "$MIRROR"

cat <<EOF

Build cache ready: $MIRROR
Publish it with any static file server (or use it via file://).
End-users add it with:

    spack mirror add --unsigned provider <url-of-mirror>
EOF
