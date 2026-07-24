#!/usr/bin/env bash
# Strategy 1, provider role: build Spack packages and publish them as
# a conda channel that end-users consume with pixi.
#
# Usage: provider-conda.sh [spec] [channel-dir]
set -eu

SPEC="${1:-zstd}"
CHANNEL="${2:-$PWD/channel}"
SPACK="${SPACK:-$PWD/spack/bin/spack}"

# Isolate spack's config and cache under ./.cache/
. "$(dirname "$0")/spack-env.sh"

# Recommended: pad install prefixes so they are long.  Conda-style
# relocation rewrites the build prefix to the end-user's environment
# prefix *in place*, so the end-user prefix must be no longer than
# the placeholder recorded at packaging time (the spack prefix).
# This must be set BEFORE installing the packages to be converted.
"$SPACK" config add config:install_tree:padded_length:128

# Build (or reuse) the package in the spack install area.
"$SPACK" install "$SPEC"

# Convert it, and its runtime dependencies, into .conda packages and
# index them into the channel.
spaxi --spack-exe "$SPACK" --channel "$CHANNEL" conda "$SPEC"

# Tell end-users the glibc their pixi projects must declare.
GLIBC=$("$SPACK" find --format '{version}' glibc | head -1)

cat <<EOF

Channel ready: $CHANNEL
Publish it with any static file server (or use it via file://).

End-users need in their pixi.toml:

    [system-requirements]
    libc = { family = "glibc", version = "$GLIBC" }
EOF
