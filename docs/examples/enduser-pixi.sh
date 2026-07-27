#!/usr/bin/env bash
# Strategy 1, end-user role: install packages from a spaxi-built conda
# channel using plain pixi.  No spack needed on this machine.
#
# Usage: enduser-pixi.sh <channel-url> [package] [glibc-version]
#   channel-url: e.g. file:///path/to/channel or https://example.org/channel
set -eu

CHANNEL="${1:?usage: enduser-pixi.sh <channel-url> [package] [glibc-version]}"
PACKAGE="${2:-zstd}"
GLIBC="${3:-}"          # optional; only needed for cross-machine solves
PIXI="${PIXI:-pixi}"

# NOTE: keep the project path short.  Conda relocation requires the
# environment prefix to fit inside the placeholder recorded by the
# provider (their padded spack prefix).

"$PIXI" init --channel "$CHANNEL" .

# Spack-built packages carry a `__glibc >=` constraint from the machine
# they were built on.  pixi models glibc as the `__glibc` virtual
# package and AUTO-DETECTS it from this host, so on a machine whose
# glibc already satisfies that constraint no pixi.toml change is needed.
# We only normalize the platforms entry to the table form here
# (illustrated in trial/pixi.toml):
sed -i -E 's|^platforms = \["([^"]+)"\]|platforms = [{ platform = "\1" }]|' pixi.toml

# Pin glibc explicitly ONLY when solving/locking for a *different*
# machine than this one (whose glibc pixi cannot detect), by passing a
# glibc-version argument:
if [ -n "$GLIBC" ]; then
  sed -i -E \
    "s|^platforms = \[\{ platform = \"([^\"]+)\" \}\]|platforms = [{ platform = \"\1\", glibc = \"$GLIBC\" }]|" \
    pixi.toml
fi

"$PIXI" add "$PACKAGE"
"$PIXI" list

# Demonstrate the Spack-built program itself runs from the pixi env
# (requires the package to have been built with its executables, e.g.
# `zstd +programs` on the provider side).
echo
echo "which $PACKAGE ->"; "$PIXI" run which "$PACKAGE"
"$PIXI" run "$PACKAGE" --version

echo
echo "Environment at .pixi/envs/default -- use it via 'pixi shell' or 'pixi run'."
