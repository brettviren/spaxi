"""Orchestrate Spack-to-conda package conversion (strategy 1).

Resolves a user spec against the installed Spack packages and converts
the resulting install tree(s) into .conda packages in a local channel.

Conversion runs in two phases.  Discovery walks the runtime-dependency
DAG with ``spack`` (which holds a database lock, so it stays
sequential).  The build phase then converts each package -- scan, hash
and zstd, all CPU-bound and independent -- and may run in a thread pool
(``jobs``); only the shared ``repodata.json`` update is serialized.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from . import channel as channel_mod
from . import conda
from .spack import Spack, SpackError

log = logging.getLogger(__name__)


@dataclass
class Converted:
    """Result of converting one spack package."""

    name: str
    version: str
    hash: str
    path: Path | None  # None if skipped (already in channel, external, ...)
    note: str = ""
    # Longest end-user env prefix this package tolerates (binary relocation),
    # or None if unconstrained / not built this run.
    prefix_limit: int | None = None


@dataclass
class _Plan:
    """A resolved package to convert, with its runtime dependency nodes."""

    node: dict
    dep_nodes: dict[str, dict]  # hash -> full node, runtime deps only
    prefix: Path


def _node_is_external(node: dict, prefix: Path) -> bool:
    if node.get("external"):
        return True
    # Externals registered without an "external" field still live
    # outside the spack store; a missing .spack metadir is the tell.
    return not (prefix / conda.SPACK_METADIR).is_dir()


def _resolve_jobs(jobs: int) -> int:
    """Map the ``--jobs`` option to a worker count (0 -> one per CPU)."""
    if jobs == 0:
        return os.cpu_count() or 1
    return max(1, jobs)


def _discover(spack: Spack, root: dict, with_deps: bool) -> list[_Plan]:
    """Walk the runtime DAG into a de-duplicated, ordered build plan.

    All ``spack`` invocations happen here so the parallel build phase
    touches only the filesystem.  Externals are recorded but not
    recursed into (their subtree lives outside the spack store).

    Two batch calls (``spack find -d``) fetch the whole DAG's nodes and
    prefixes up front, so the traversal itself makes no per-node ``spack``
    calls -- only a fallback for a hash the batch somehow missed.
    """
    todo = [root]
    seen: set[str] = set()
    plan: list[_Plan] = []

    # Prime the node and prefix maps from the whole closure in two calls.
    try:
        resolved = {n["hash"]: n for n in spack.dag_nodes(root["hash"])}
        prefixes = spack.dag_prefixes(root["hash"])
    except SpackError:
        resolved, prefixes = {}, {}
    resolved.setdefault(root["hash"], root)

    def resolve(spec_hash: str, name: str) -> dict:
        node = resolved.get(spec_hash)
        if node is None:
            log.debug("resolving dependency %s/%s", name, spec_hash[:7])
            node = resolved[spec_hash] = spack.resolve_one(f"/{spec_hash}")
        return node

    def prefix_of(spec_hash: str) -> Path:
        path = prefixes.get(spec_hash)
        if path is None:
            path = prefixes[spec_hash] = spack.prefix(spec_hash)
        return path

    while todo:
        node = todo.pop(0)
        if node["hash"] in seen:
            continue
        seen.add(node["hash"])

        log.debug("resolving %s@%s/%s (%d queued, %d resolved)",
                  node["name"], node["version"], node["hash"][:7],
                  len(todo), len(seen) - 1)

        # Gather full nodes for runtime deps: needed both for the
        # depends list and (optionally) for recursive conversion.
        dep_nodes: dict[str, dict] = {}
        for dep in node.get("dependencies", []):
            deptypes = set(dep.get("parameters", {}).get("deptypes", []))
            if not deptypes & conda.RUNTIME_DEPTYPES:
                continue
            dep_nodes[dep["hash"]] = resolve(dep["hash"], dep["name"])

        prefix = prefix_of(node["hash"])
        plan.append(_Plan(node, dep_nodes, prefix))

        if _node_is_external(node, prefix):
            continue  # do not recurse into externals
        if with_deps:
            for dep_node in dep_nodes.values():
                if dep_node["name"] not in conda.VIRTUAL_PACKAGES:
                    todo.append(dep_node)

    return plan


def _convert_one(
    plan: _Plan,
    channel_dir: Path,
    force: bool,
    compression_level: int,
    relocate_prefixes: list[str] | None,
    index_lock: Lock,
    zstd_budget: conda.ZstdThreadBudget | None = None,
) -> Converted:
    """Convert a single planned package (thread-pool worker).

    The expensive build (scan/hash/zstd) runs unlocked; only the shared
    repodata update is serialized behind ``index_lock``.  ``zstd_budget``
    hands this package its share of zstd worker threads.
    """
    node, dep_nodes, prefix = plan.node, plan.dep_nodes, plan.prefix

    if _node_is_external(node, prefix):
        if node["name"] in conda.VIRTUAL_PACKAGES:
            note = f"external, satisfied by {conda.VIRTUAL_PACKAGES[node['name']]} virtual package"
        else:
            note = "external to spack, not converted"
        return Converted(node["name"], str(node["version"]), node["hash"], None, note)

    meta = conda.meta_from_spec(node, dep_nodes)
    dest = Path(channel_dir) / meta.subdir / f"{meta.filestem}.conda"
    if dest.exists() and not force:
        return Converted(meta.name, meta.version, node["hash"], dest,
                         "already in channel, skipped")

    log.debug("building %s", meta.filestem)
    result = conda.build_conda_package(
        prefix, meta, dest.parent, compression_level=compression_level,
        relocate_prefixes=relocate_prefixes, zstd_budget=zstd_budget)
    staged, record = channel_mod.stage_package(
        Path(channel_dir), result.path, meta.index_json())
    with index_lock:
        channel_mod.index_record(Path(channel_dir), staged, record)
    return Converted(meta.name, meta.version, node["hash"], staged,
                     prefix_limit=result.prefix_limit)


def convert_spec(
    spack: Spack,
    spec: str,
    channel_dir: Path,
    with_deps: bool = True,
    force: bool = False,
    jobs: int = 1,
    compression_level: int = conda.DEFAULT_COMPRESSION_LEVEL,
    relocate_rpaths: bool = True,
) -> list[Converted]:
    """Convert the single installed package matching ``spec``.

    With ``with_deps`` (default) the transitive runtime (link/run)
    dependencies are converted as well so the channel is
    self-contained.  Existing packages in the channel are skipped
    unless ``force``.  ``jobs`` packages are built in parallel (0 means
    one per CPU); ``compression_level`` is the zstd level for payloads.
    With ``relocate_rpaths`` (default) ELF RPATHs into any converted Spack
    prefix are rewritten ``$ORIGIN``-relative, so the channel links without
    the Spack store.  Returns one Converted record per visited spec, in
    discovery order.
    """
    root = spack.resolve_one(spec)
    t0 = time.perf_counter()
    plan = _discover(spack, root, with_deps)
    log.debug("phase 'discover' complete: %d package(s) in %.1fs",
              len(plan), time.perf_counter() - t0)

    workers = _resolve_jobs(jobs)
    total = len(plan)
    log.debug("converting %d package(s) with %d job(s), zstd level %d",
              total, workers, compression_level)

    # RPATHs may reference any package in the converted closure, so every
    # build gets the full set of (non-external) Spack prefixes to remap.
    relocate_prefixes = None
    if relocate_rpaths:
        prefixes: set[str] = set()
        for p in plan:
            if not _node_is_external(p.node, p.prefix):
                prefixes.add(str(p.prefix))
                prefixes.add(os.path.realpath(p.prefix))
        relocate_prefixes = sorted(prefixes)

    index_lock = Lock()
    results: list[Converted] = [None] * total  # type: ignore[list-item]
    t0 = time.perf_counter()

    # Packages fan out across the pool; the CPU-heavy zstd compression pulls
    # its threads from one shared budget sized to -j, so a lone big package in
    # the tail gets most of the cores while many small ones each get one.
    zstd_budget = conda.ZstdThreadBudget(workers)

    def build(i: int) -> None:
        results[i] = _convert_one(
            plan[i], channel_dir, force, compression_level,
            relocate_prefixes, index_lock, zstd_budget)

    if workers == 1:
        for i in range(total):
            build(i)
            log.debug("converted %d/%d %s", i + 1, total, results[i].name)
    else:
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(build, i): i for i in range(total)}
            for future in as_completed(futures):
                future.result()  # re-raise any worker exception
                done += 1
                name = results[futures[future]].name
                log.debug("converted %d/%d %s", done, total, name)

    log.debug("phase 'build' complete: %d package(s) in %.1fs",
              total, time.perf_counter() - t0)
    return results
