#!/usr/bin/env bash
# Strategy 1, authoring aid: select a specific Spack *variant* from a
# spaxi-built conda channel by its conda "flags", using `spaxi add-spec`.
#
# spaxi conda records each package's variants as CEP-45 flags, e.g.
#   +programs            -> programs:true
#   ~programs            -> programs:false
#   compression=zlib     -> compression:zlib   and   compression_set:zlib
#   compression=lz4,zlib -> compression:lz4, compression:zlib,
#                           compression_set:lz4_zlib   (atomic, lexically sorted)
# plus hash:<dag-hash>.  `spaxi add-spec` concretizes a spec and writes the
# matching flag-based dependency into pixi.toml.
#
# This aid needs Spack (to concretize the spec); the pixi.toml it produces is
# consumed by end-users who need only pixi.
#
# Usage: author-pixi.sh <channel-url> <spec> [--exact]
#   --exact: also pin hash:<dag-hash> (the exact build + transitive closure)
set -eu

CHANNEL="${1:?usage: author-pixi.sh <channel-url> <spec> [--exact]}"
SPEC="${2:?usage: author-pixi.sh <channel-url> <spec> [--exact]}"
EXACT="${3:-}"                          # pass --exact to pin the DAG hash
PIXI="${PIXI:-pixi}"
SPAXI="${SPAXI:-spaxi}"

# Start from a normal pixi project (channel + platform), then let add-spec
# fill in the flag-based dependency.  add-spec updates an existing pixi.toml.
"$PIXI" init --channel "$CHANNEL" .

# Declare glibc on the platform (see enduser-pixi.sh for why this is needed).
GLIBC=$(ldd --version | sed -n '1s/.* //p')
sed -i -E \
  "s|^platforms = \[\"([^\"]+)\"\]|platforms = [{ platform = \"\1\", glibc = \"$GLIBC\" }]|" \
  pixi.toml

# Turn the spack spec into a flag-based dependency in ./pixi.toml.  With
# --exact, add hash:<dag-hash> which pins the exact build and, since spaxi
# packages pin their runtime deps exactly, the whole transitive closure.
# Use -c/--config <file> to target a pixi.toml other than ./pixi.toml.
"$SPAXI" add-spec $EXACT "$SPEC"

"$PIXI" install
"$PIXI" list

echo
echo "pixi.toml now selects '$SPEC' by conda flags; environment at .pixi/envs/default."
