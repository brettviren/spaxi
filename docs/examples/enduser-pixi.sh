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

# Declare glibc on the platforms entry.  This is REQUIRED: pixi's solver
# uses a *declared* __glibc for reproducible cross-platform solving, and
# its linux-64 default (glibc 2.17) is older than any modern Spack build
# host, so the package's `__glibc >=` constraint would otherwise have no
# candidate.  (pixi's auto-detected host value, shown by `pixi info`, is
# NOT used by the solver.)  A safe value is this host's own glibc: if it
# is >= the provider's build-host glibc the package both solves and runs;
# if it is older, the binaries could not run here anyway.  Pass an
# explicit glibc-version argument when locking for a different machine.
sed -i -E \
  "s|^platforms = \[\"([^\"]+)\"\]|platforms = [{ platform = \"\1\", glibc = \"$GLIBC\" }]|" \
  pixi.toml

"$PIXI" add "$PACKAGE"

# A bare name takes whatever build the solver prefers.  When a channel holds
# several variants of one name+version, select the one you want by its conda
# flags (spaxi records Spack variants as flags).  Edit pixi.toml, e.g.:
#
#   [dependencies]
#   zstd = { version = "*", flags = ["programs:true"] }              # has the CLI
#   zstd = { version = "*", flags = ["compression_set:zlib"] }       # exactly zlib
#
# See author-pixi.sh / `spaxi add-spec` to generate these from a spack spec.

"$PIXI" list

# Demonstrate the Spack-built program itself runs from the pixi env
# (requires the package to have been built with its executables, e.g.
# `zstd +programs` on the provider side).
echo
echo "which $PACKAGE ->"; "$PIXI" run which "$PACKAGE"
"$PIXI" run "$PACKAGE" --version

echo
echo "Environment at .pixi/envs/default -- use it via 'pixi shell' or 'pixi run'."
