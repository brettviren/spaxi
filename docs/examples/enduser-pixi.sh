#!/usr/bin/env bash
# Strategy 1, end-user role: install packages from a spaxi-built conda
# channel using plain pixi.  No spack needed on this machine.
#
# Usage: enduser-pixi.sh <channel-url> [package] [glibc-version]
#   channel-url: e.g. file:///path/to/channel or https://example.org/channel
set -eu

CHANNEL="${1:?usage: enduser-pixi.sh <channel-url> [package] [glibc-version]}"
PACKAGE="${2:-zstd}"
GLIBC="${3:-$(ldd --version | sed -n '1s/.* //p')}"   # host glibc by default
PIXI="${PIXI:-pixi}"

# NOTE: keep the project path short.  Conda relocation requires the
# environment prefix to fit inside the placeholder recorded by the
# provider (their padded spack prefix).

"$PIXI" init --channel "$CHANNEL" .

# pixi solves against declared system requirements (glibc 2.17 by
# default), not the detected host glibc.  Spack-built packages carry
# a __glibc constraint from the machine they were built on.
cat >> pixi.toml <<EOF

[system-requirements]
libc = { family = "glibc", version = "$GLIBC" }
EOF

"$PIXI" add "$PACKAGE"
"$PIXI" list

echo
echo "Environment at .pixi/envs/default -- use it via 'pixi shell' or 'pixi run'."
