import io
import json
import tarfile
import zipfile

import pytest
import zstandard

from spaxi import conda


def read_inner_tar(conda_file, member):
    with zipfile.ZipFile(conda_file) as zf:
        data = zstandard.ZstdDecompressor().decompress(
            zf.read(member), max_output_size=1 << 30
        )
    return tarfile.open(fileobj=io.BytesIO(data))


def test_subdir_for():
    node = {"arch": {"platform": "linux",
                     "target": {"name": "zen4", "parents": ["x86_64_v4"]}}}
    assert conda.subdir_for(node) == "linux-64"
    node = {"arch": {"platform": "linux",
                     "target": {"name": "neoverse_v1", "parents": ["aarch64"]}}}
    assert conda.subdir_for(node) == "linux-aarch64"
    node = {"arch": {"platform": "darwin", "target": {"name": "m1", "parents": []}}}
    assert conda.subdir_for(node) == "osx-arm64"


def test_scan_prefix(fake_prefix):
    prefix, _ = fake_prefix
    entries = {e.path: e for e in conda.scan_prefix(prefix)}
    assert set(entries) == {
        "bin/frob-config", "lib/libfrob.so.1.2.3", "lib/libfrob.so", "lib/frob.h",
    }
    assert entries["bin/frob-config"].file_mode == "text"
    assert entries["bin/frob-config"].prefix_placeholder == str(prefix)
    assert entries["lib/libfrob.so.1.2.3"].file_mode == "binary"
    assert entries["lib/libfrob.so"].path_type == "softlink"
    assert entries["lib/frob.h"].file_mode is None
    # .spack metadata must not leak into the payload
    assert not any(p.startswith(".spack") for p in entries)


def test_meta_from_spec(fake_prefix):
    _, node = fake_prefix
    dep_nodes = {
        "glibchash0123456789abcdefghijklm": {
            "name": "glibc", "version": "2.36",
            "hash": "glibchash0123456789abcdefghijklm"},
        "gccrthash0123456789abcdefghijklm": {
            "name": "gcc-runtime", "version": "11.3.0",
            "hash": "gccrthash0123456789abcdefghijklm"},
    }
    meta = conda.meta_from_spec(node, dep_nodes)
    assert meta.name == "frob"
    assert meta.version == "1.2.3"
    assert meta.build == node["hash"]
    assert meta.subdir == "linux-64"
    # glibc -> virtual, gcc-runtime -> exact pin, gmake (build dep) dropped
    assert meta.depends == [
        "__glibc >=2.36",
        "gcc-runtime 11.3.0 gccrthash0123456789abcdefghijklm",
    ]


def test_meta_from_spec_missing_dep_node(fake_prefix):
    _, node = fake_prefix
    with pytest.raises(conda.CondaBuildError):
        conda.meta_from_spec(node, {})


def test_meta_from_spec_flags():
    node = {
        "name": "zstd", "version": "1.5.7", "hash": "h" * 32,
        "arch": {"platform": "linux", "target": {"name": "zen4", "parents": []}},
        "parameters": {"programs": True, "compression": ["zlib"]},
        "dependencies": [],
    }
    meta = conda.meta_from_spec(node, {})
    assert "programs:true" in meta.flags
    assert "compression:zlib" in meta.flags
    assert "compression_set:zlib" in meta.flags
    assert f"hash:{node['hash']}" in meta.flags


def test_index_json_includes_flags():
    meta = conda.PackageMeta(
        "x", "1", "b", "linux-64", flags=["programs:true", "hash:b"])
    assert meta.index_json()["flags"] == ["programs:true", "hash:b"]
    # omitted entirely when there are no flags
    assert "flags" not in conda.PackageMeta("x", "1", "b", "linux-64").index_json()


def test_build_conda_package(fake_prefix, tmp_path):
    prefix, node = fake_prefix
    meta = conda.PackageMeta(
        name="frob", version="1.2.3", build=node["hash"], subdir="linux-64",
        depends=["__glibc >=2.36"],
    )
    out = conda.build_conda_package(prefix, meta, tmp_path / "out")
    assert out.name == f"frob-1.2.3-{node['hash']}.conda"

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert json.loads(zf.read("metadata.json")) == {"conda_pkg_format_version": 2}
    assert f"info-{meta.filestem}.tar.zst" in names
    assert f"pkg-{meta.filestem}.tar.zst" in names

    info = read_inner_tar(out, f"info-{meta.filestem}.tar.zst")
    index = json.load(info.extractfile("info/index.json"))
    assert index["name"] == "frob"
    assert index["arch"] == "x86_64"
    assert index["platform"] == "linux"
    assert index["depends"] == ["__glibc >=2.36"]
    paths = json.load(info.extractfile("info/paths.json"))
    assert paths["paths_version"] == 1
    files = info.extractfile("info/files").read().decode().split()
    assert "lib/libfrob.so.1.2.3" in files
    # provenance: original spack spec preserved
    assert "info/spack-spec.json" in info.getnames()

    pkg = read_inner_tar(out, f"pkg-{meta.filestem}.tar.zst")
    link = pkg.getmember("lib/libfrob.so")
    assert link.issym() and link.linkname == "libfrob.so.1.2.3"
    payload = pkg.extractfile("lib/libfrob.so.1.2.3").read()
    assert str(prefix).encode() in payload


def test_build_conda_package_empty(tmp_path):
    (tmp_path / "empty").mkdir()
    meta = conda.PackageMeta("x", "1", "b", "linux-64")
    with pytest.raises(conda.CondaBuildError):
        conda.build_conda_package(tmp_path / "empty", meta, tmp_path / "out")
