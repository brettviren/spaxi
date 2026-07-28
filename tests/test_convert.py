"""Unit tests for convert orchestration helpers (no spack required)."""

import os
from collections import Counter

from click.testing import CliRunner

from spaxi import convert
from spaxi.cli import cli


class FakeSpack:
    """A minimal Spack stand-in over an in-memory DAG, counting lookups.

    ``batch_hashes`` limits which nodes the batch ``dag_*`` calls return, so
    a test can force the per-node ``resolve_one``/``prefix`` fallback.
    """

    def __init__(self, nodes, prefixes, batch_hashes=None):
        self.nodes = nodes           # hash -> node dict
        self.prefixes = prefixes     # hash -> Path
        self.batch_hashes = set(nodes if batch_hashes is None else batch_hashes)
        self.resolve_calls = Counter()
        self.prefix_calls = Counter()
        self.dag_calls = 0

    def resolve_one(self, spec):
        h = spec.lstrip("/")
        self.resolve_calls[h] += 1
        return self.nodes[h]

    def prefix(self, spec_hash):
        self.prefix_calls[spec_hash] += 1
        return self.prefixes[spec_hash]

    def _reachable(self, root_hash):
        seen, todo = set(), [root_hash]
        while todo:
            h = todo.pop()
            if h in seen:
                continue
            seen.add(h)
            todo += [d["hash"] for d in self.nodes[h].get("dependencies", [])]
        return seen & self.batch_hashes

    def dag_nodes(self, spec_hash):
        self.dag_calls += 1
        return [self.nodes[h] for h in self._reachable(spec_hash)]

    def dag_prefixes(self, spec_hash):
        return {h: self.prefixes[h] for h in self._reachable(spec_hash)}


def _node(h, name, deps=()):
    return {
        "hash": h, "name": name, "version": "1.0",
        "dependencies": [
            {"name": d, "hash": d, "parameters": {"deptypes": ["link"]}}
            for d in deps
        ],
    }


def _diamond(tmp_path):
    # root -> a, b ; a -> c ; b -> c  (c is shared by a and b)
    nodes = {
        "root": _node("root", "root", ["a", "b"]),
        "a": _node("a", "a", ["c"]),
        "b": _node("b", "b", ["c"]),
        "c": _node("c", "c"),
    }
    prefixes = {}
    for h in nodes:
        p = tmp_path / h
        (p / ".spack").mkdir(parents=True)   # a real install (not external)
        prefixes[h] = p
    return nodes, prefixes


def test_discover_uses_batch_no_per_node_calls(tmp_path):
    nodes, prefixes = _diamond(tmp_path)
    spack = FakeSpack(nodes, prefixes)

    plan = convert._discover(spack, nodes["root"], with_deps=True)

    assert {p.node["hash"] for p in plan} == {"root", "a", "b", "c"}
    # The whole DAG comes from one batch call; nothing is resolved per node.
    assert spack.dag_calls == 1
    assert sum(spack.resolve_calls.values()) == 0
    assert sum(spack.prefix_calls.values()) == 0


def test_discover_falls_back_for_missing_batch_node(tmp_path):
    nodes, prefixes = _diamond(tmp_path)
    # 'c' is absent from the batch, forcing a single per-node fallback.
    spack = FakeSpack(nodes, prefixes,
                      batch_hashes={"root", "a", "b"})

    plan = convert._discover(spack, nodes["root"], with_deps=True)

    assert {p.node["hash"] for p in plan} == {"root", "a", "b", "c"}
    assert spack.resolve_calls["c"] == 1        # resolved once, then memoized
    assert spack.prefix_calls["c"] == 1


def test_resolve_jobs_explicit():
    assert convert._resolve_jobs(1) == 1
    assert convert._resolve_jobs(4) == 4
    # negative is clamped to a single worker
    assert convert._resolve_jobs(-3) == 1


def test_resolve_jobs_zero_is_cpu_count():
    assert convert._resolve_jobs(0) == (os.cpu_count() or 1)


def test_conda_rejects_bad_compression_level(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["conda", "--compression-level", "99", "somepkg"])
    assert result.exit_code == 1
    assert "compression level must be between 1 and 22" in result.output
