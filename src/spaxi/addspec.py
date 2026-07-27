"""Author a pixi.toml dependency from a Spack spec (strategy 1 helper).

``spaxi add-spec <spec>`` concretizes the spec with Spack, renders its variants
as conda flags (see :mod:`spaxi.flags`) and merges a flag-based dependency into
a ``pixi.toml``.  With ``exact`` the concretized DAG hash is added as a
``hash:<hash>`` flag; because spaxi-built packages pin their runtime
dependencies to exact builds, pinning the root this way fixes the whole
transitive closure.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from .flags import variant_flags
from .spack import Spack


@dataclass
class AddSpecResult:
    """Outcome of an add-spec operation."""

    name: str
    version: str
    flags: list[str]
    path: Path
    created: bool


def _scaffold(config_path: Path) -> dict:
    """A minimal, structurally valid pixi.toml for a freshly created file.

    Channels and platforms are left empty for the user to fill (they are not
    derivable from a spec alone).
    """
    return {
        "workspace": {
            "name": config_path.parent.resolve().name or "project",
            "version": "0.1.0",
            "channels": [],
            "platforms": [],
        },
        "dependencies": {},
    }


def add_spec(
    spack: Spack,
    spec: str,
    config_path: Path,
    exact: bool = False,
) -> AddSpecResult:
    """Concretize ``spec`` and merge a flag-based dependency into ``config_path``.

    Creates ``config_path`` (with a minimal workspace scaffold) when it does
    not yet exist, otherwise updates it in place.
    """
    node = spack.concretize_one(spec)
    name = node["name"]
    flags = variant_flags(node, include_hash=exact)

    config_path = Path(config_path)
    created = not config_path.is_file()
    manifest = _scaffold(config_path) if created else tomllib.loads(config_path.read_text())

    deps = manifest.setdefault("dependencies", {})
    entry: dict = {"version": "*"}
    if flags:
        entry["flags"] = flags
    deps[name] = entry

    config_path.write_text(tomli_w.dumps(manifest))
    return AddSpecResult(
        name=name,
        version=str(node["version"]),
        flags=flags,
        path=config_path,
        created=created,
    )
