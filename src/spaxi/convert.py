"""Orchestrate Spack-to-conda package conversion (strategy 1).

Resolves a user spec against the installed Spack packages and converts
the resulting install tree(s) into .conda packages in a local channel.
"""

from dataclasses import dataclass
from pathlib import Path

from . import channel as channel_mod
from . import conda
from .spack import Spack, SpackError


@dataclass
class Converted:
    """Result of converting one spack package."""

    name: str
    version: str
    hash: str
    path: Path | None  # None if skipped (already in channel, external, ...)
    note: str = ""


def _node_is_external(node: dict, prefix: Path) -> bool:
    if node.get("external"):
        return True
    # Externals registered without an "external" field still live
    # outside the spack store; a missing .spack metadir is the tell.
    return not (prefix / conda.SPACK_METADIR).is_dir()


def convert_spec(
    spack: Spack,
    spec: str,
    channel_dir: Path,
    with_deps: bool = True,
    force: bool = False,
) -> list[Converted]:
    """Convert the single installed package matching ``spec``.

    With ``with_deps`` (default) the transitive runtime (link/run)
    dependencies are converted as well so the channel is
    self-contained.  Existing packages in the channel are skipped
    unless ``force``.  Returns one Converted record per visited spec.
    """
    root = spack.resolve_one(spec)
    todo = [root]
    seen: set[str] = set()
    results: list[Converted] = []

    while todo:
        node = todo.pop(0)
        if node["hash"] in seen:
            continue
        seen.add(node["hash"])

        # Gather full nodes for runtime deps: needed both for the
        # depends list and (optionally) for recursive conversion.
        dep_nodes: dict[str, dict] = {}
        for dep in node.get("dependencies", []):
            deptypes = set(dep.get("parameters", {}).get("deptypes", []))
            if not deptypes & conda.RUNTIME_DEPTYPES:
                continue
            dep_nodes[dep["hash"]] = spack.resolve_one(f"/{dep['hash']}")

        prefix = spack.prefix(node["hash"])
        if _node_is_external(node, prefix):
            if node["name"] in conda.VIRTUAL_PACKAGES:
                note = f"external, satisfied by {conda.VIRTUAL_PACKAGES[node['name']]} virtual package"
            else:
                note = "external to spack, not converted"
            results.append(
                Converted(node["name"], str(node["version"]), node["hash"], None, note)
            )
            continue

        meta = conda.meta_from_spec(node, dep_nodes)
        dest = Path(channel_dir) / meta.subdir / f"{meta.filestem}.conda"
        if dest.exists() and not force:
            results.append(
                Converted(meta.name, meta.version, node["hash"], dest,
                          "already in channel, skipped")
            )
        else:
            built = conda.build_conda_package(prefix, meta, dest.parent)
            final = channel_mod.add_package(Path(channel_dir), built, meta.index_json())
            results.append(Converted(meta.name, meta.version, node["hash"], final))

        if with_deps:
            for dep_node in dep_nodes.values():
                if dep_node["name"] not in conda.VIRTUAL_PACKAGES:
                    todo.append(dep_node)

    return results
