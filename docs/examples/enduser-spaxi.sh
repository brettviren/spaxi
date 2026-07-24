#!/usr/bin/env bash
# Strategy 2, end-user role: a pixi-like workflow staying fully in
# Spack.  Uses spaxi's pixi-equivalent subcommands; binaries come
# from the provider's build cache when one is configured.
#
# Usage: enduser-spaxi.sh [package] [mirror-url]
#   mirror-url: optional provider build cache,
#               e.g. file:///path/to/buildcache
set -eu

PACKAGE="${1:-zstd}"
MIRROR="${2:-}"
SPACK="${SPACK:-$PWD/spack/bin/spack}"

# Isolate spack's config and cache under ./.cache/
. "$(dirname "$0")/spack-env.sh"

# Use the provider's binary build cache, if given.
if [ -n "$MIRROR" ]; then
    "$SPACK" mirror list | grep -q '^provider ' ||
        "$SPACK" mirror add --unsigned provider "$MIRROR"
fi

# The pixi-like flow: init a project, add a dependency, inspect.
spaxi --spack-exe "$SPACK" init --name demo
spaxi --spack-exe "$SPACK" add "$PACKAGE"
spaxi --spack-exe "$SPACK" list
spaxi --spack-exe "$SPACK" tree
spaxi --spack-exe "$SPACK" info

cat <<'EOF'

The environment view is at .spaxi/envs/default -- put its bin/ on
PATH (and lib/ on LD_LIBRARY_PATH if needed) to use it:

    export PATH="$PWD/.spaxi/envs/default/bin:$PATH"
EOF
