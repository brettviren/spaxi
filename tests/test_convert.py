"""Unit tests for convert orchestration helpers (no spack required)."""

import os
from collections import Counter

from click.testing import CliRunner

from spaxi import convert
from spaxi.cli import cli


class FakeSpack:
    """A minimal Spack stand-in over an in-memory DAG, counting lookups."""

    def __init__(self, nodes, prefixes):
        self.nodes = nodes           # hash -> node dict
        self.prefixes = prefixes     # hash -> Path
        self.resolve_calls = Counter()
        self.prefix_calls = Counter()

    def resolve_one(self, spec):
        h = spec.lstrip("/")
        self.resolve_calls[h] += 1
        return self.nodes[h]

    def prefix(self, spec_hash):
        self.prefix_calls[spec_hash] += 1
        return self.prefixes[spec_hash]


def _node(h, name, deps=()):
    return {
        "hash": h, "name": name, "version": "1.0",
        "dependencies": [
            {"name": d, "hash": d, "parameters": {"deptypes": ["link"]}}
            for d in deps
        ],
    }


def test_discover_memoizes_shared_dependencies(tmp_path):
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
    spack = FakeSpack(nodes, prefixes)

    plan = convert._discover(spack, nodes["root"], with_deps=True)

    assert {p.node["hash"] for p in plan} == {"root", "a", "b", "c"}
    # The shared dep 'c' is resolved from spack exactly once, not per edge.
    assert spack.resolve_calls["c"] == 1
    assert all(v == 1 for v in spack.resolve_calls.values())
    # Each unique node's prefix is looked up exactly once.
    assert all(v == 1 for v in spack.prefix_calls.values())


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
