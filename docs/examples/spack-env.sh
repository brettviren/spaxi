# Source me: point Spack's config/cache paths at local directories.
#
# Keeps every spaxi/spack exercise isolated from any spack
# configuration elsewhere on the machine.  SPAXI_CACHE defaults to
# .cache/ under the current directory.

SPAXI_CACHE="${SPAXI_CACHE:-$PWD/.cache}"

export SPACK_USER_CONFIG_PATH="$SPAXI_CACHE/spack/user-config"
export SPACK_USER_CACHE_PATH="$SPAXI_CACHE/spack/user-cache"
export SPACK_SYSTEM_CONFIG_PATH="$SPAXI_CACHE/spack/system-config"

mkdir -p "$SPACK_USER_CONFIG_PATH" "$SPACK_USER_CACHE_PATH" "$SPACK_SYSTEM_CONFIG_PATH"
