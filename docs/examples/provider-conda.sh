#!/usr/bin/env bash
# Strategy 1, provider role: build Spack packages and publish them as
# a conda channel that end-users consume with pixi.
#
# Usage: provider-conda.sh [spec] [channel-dir]
set -eu

# NOTE: build variants that install the executables you want end-users
# to run.  Spack's zstd defaults to ~programs (library only), so the
# `zstd` CLI would be absent from the channel; +programs installs it.
SPEC="${1:-zstd +programs}"
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

# The build-machine glibc becomes the packages' `__glibc >=` floor.
GLIBC=$("$SPACK" find --format '{version}' glibc | head -1)

cat <<EOF

Channel ready: $CHANNEL
Publish it with any static file server (or use it via file://).

These packages require glibc >= $GLIBC.  End-users MUST declare a
glibc at least this high on their pixi.toml platforms entry -- pixi's
solver uses a declared __glibc (default 2.17 on linux-64), not the
detected host value, so without this the __glibc constraint has no
candidate and the solve fails:

    platforms = [{ platform = "linux-64", glibc = "$GLIBC" }]
EOF
