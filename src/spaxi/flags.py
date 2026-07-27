"""Map Spack spec variants to conda "flags" (CEP-45).

A conda *flag* is a string matching ``^[a-z0-9_]+(:[a-z0-9_]+)?$`` recorded on
a package and selectable in a MatchSpec / ``pixi.toml``.  Each concretized
Spack variant renders to one or more flags::

    +variant     -> variant:true
    ~variant     -> variant:false
    key=value    -> key:value
    key=v1,v2    -> key:v1, key:v2                (one flag per value)
                 -> key_set:v1_v2                 (atomic, lexically sorted)

Multi-valued variants get *both* the per-value membership flags (order-free
subset queries) and a single atomic ``<key>_set`` flag whose value is the
squashed values de-duplicated, lexically sorted and joined with ``_``.  The
atomic flag lets a consumer pin the exact set (distinguishing a subset from a
superset that also contains those values) without resorting to the hash.  So
that an exact build can still be pinned by flag alone, the Spack DAG hash is
added as ``hash:<dag-hash>``.  Variant *values* (and names) are squashed into
the flag charset ``[a-z0-9_]`` -- see :func:`squash`.
"""

import re

# Keys in a spec node's ``parameters`` that hold compiler flags rather than
# build variants; these are not rendered as conda flags.
_FLAG_PARAMS = frozenset(
    {"cflags", "cppflags", "cxxflags", "fflags", "ldflags", "ldlibs"}
)

# Flag-key suffix for the atomic form of a multi-valued variant.
_SET_SUFFIX = "_set"

# Reserved flag key for the exact-pin DAG hash.
_HASH_KEY = "hash"

_DISALLOWED = re.compile(r"[^a-z0-9_]+")


class FlagCollisionError(Exception):
    """A real Spack variant name collides with a synthesized flag key.

    Raised when a variant would produce the reserved ``hash`` key or a
    ``<other>_set`` key that a multi-valued variant also synthesizes.  No
    current Spack builtin variant triggers this; the guard turns a would-be
    silent ambiguity into a loud, fixable error.
    """


def squash(text: object) -> str:
    """Squash arbitrary text into the conda flag charset ``[a-z0-9_]``.

    Lower-cases, replaces every run of disallowed characters with a single
    underscore and trims leading/trailing underscores.  Never returns the
    empty string: a value with no allowed characters collapses to ``"_"``.
    """
    squashed = _DISALLOWED.sub("_", str(text).lower()).strip("_")
    return squashed or "_"


def variant_flags(node: dict, include_hash: bool = True) -> list[str]:
    """Return the sorted conda flags describing a Spack spec ``node``.

    ``node`` is a spec-node dict as produced by ``spack spec/find --json``.
    Boolean variants become ``name:true`` / ``name:false``; single-valued
    variants become ``name:value``; multi-valued variants become one
    ``name:value`` membership flag per value *plus* an atomic
    ``name_set:<sorted_join>`` flag.  Compiler flag parameters (``cflags``
    ...) are ignored.  With ``include_hash`` the DAG hash is appended as
    ``hash:<hash>`` so the exact build can be pinned by flag alone.

    Raises :class:`FlagCollisionError` if a variant name would occupy a
    synthesized key (``hash`` or another variant's ``<name>_set``).
    """
    params = {
        key: value
        for key, value in (node.get("parameters") or {}).items()
        if key not in _FLAG_PARAMS
    }
    variant_keys = {squash(key) for key in params}
    synthesized = {_HASH_KEY}
    for key, value in params.items():
        if isinstance(value, (list, tuple)) and len(value) > 0:
            synthesized.add(f"{squash(key)}{_SET_SUFFIX}")
    clash = variant_keys & synthesized
    if clash:
        raise FlagCollisionError(
            f"variant name(s) {sorted(clash)} collide with reserved flag keys "
            f"for {node.get('name', '?')}/{str(node.get('hash', ''))[:7]}"
        )

    flags: set[str] = set()
    for key, value in params.items():
        name = squash(key)
        # bool must precede the list/int checks (bool is a subclass of int).
        if isinstance(value, bool):
            flags.add(f"{name}:{'true' if value else 'false'}")
        elif isinstance(value, (list, tuple)):
            values = sorted({squash(item) for item in value})
            for item in values:
                flags.add(f"{name}:{item}")
            if values:
                flags.add(f"{name}{_SET_SUFFIX}:{'_'.join(values)}")
        else:
            flags.add(f"{name}:{squash(value)}")
    if include_hash and node.get("hash"):
        flags.add(f"{_HASH_KEY}:{squash(node['hash'])}")
    return sorted(flags)
