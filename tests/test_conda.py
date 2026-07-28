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


def test_human_bytes():
    assert conda._human_bytes(0) == "0 B"
    assert conda._human_bytes(512) == "512 B"
    assert conda._human_bytes(1536) == "1.5 KiB"
    assert conda._human_bytes(5 * 1024**3) == "5.0 GiB"


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
    result = conda.build_conda_package(prefix, meta, tmp_path / "out")
    out = result.path
    assert out.name == f"frob-1.2.3-{node['hash']}.conda"
    # fake_prefix has a binary file embedding the prefix -> relocation cap.
    assert result.prefix_limit == len(str(prefix.resolve()))

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


def test_build_conda_package_compression_level(fake_prefix, tmp_path):
    prefix, node = fake_prefix
    meta = conda.PackageMeta(
        name="frob", version="1.2.3", build=node["hash"], subdir="linux-64",
    )
    out = conda.build_conda_package(
        prefix, meta, tmp_path / "out", compression_level=1).path
    # A valid .conda is produced at a non-default level and stays readable.
    info = read_inner_tar(out, f"info-{meta.filestem}.tar.zst")
    assert json.load(info.extractfile("info/index.json"))["name"] == "frob"


def test_build_conda_package_text_only_unconstrained(tmp_path):
    # Text prefix replacement can grow, so a package that embeds the prefix
    # only in text files imposes no install-prefix length cap.
    prefix = tmp_path / "scriptpkg-1.0-deadbeef"
    (prefix / "bin").mkdir(parents=True)
    (prefix / ".spack").mkdir()
    (prefix / "bin" / "run").write_text(f"#!/bin/sh\nexec {prefix}/libexec/x\n")
    meta = conda.PackageMeta("scriptpkg", "1.0", "deadbeef", "linux-64")
    result = conda.build_conda_package(prefix, meta, tmp_path / "out")
    assert result.prefix_limit is None


def test_scan_prefix_unifies_dependency_prefixes(tmp_path):
    # A binary embedding both its own prefix and a (shorter) dependency
    # prefix in NUL-delimited rodata; both must fold onto one placeholder.
    own = "/opt/spack/linux/tarpkg-1.35-" + "a" * 32
    dep = "/opt/spack/linux/pigz-2.8-" + "b" * 32
    prefix = tmp_path / ("tarpkg-1.35-" + "a" * 32)
    (prefix / "bin").mkdir(parents=True)
    (prefix / ".spack").mkdir()
    payload = (b"\x7fELF\x00\x00"
               + own.encode() + b"/libexec/rmt\x00"
               + dep.encode() + b"/bin/pigz\x00tail")
    (prefix / "bin" / "app").write_bytes(payload)
    (prefix / "bin" / "app").chmod(0o755)

    entries = conda.scan_prefix(prefix, [own, dep], tmp_path / "stage")
    app = next(e for e in entries if e.path == "bin/app")
    assert app.file_mode == "binary"
    assert app.prefix_placeholder == dep          # the shorter, shared one
    staged = app.source.read_bytes()
    assert len(staged) == len(payload)            # length preserved
    assert own.encode() not in staged
    assert dep.encode() + b"/libexec/rmt" in staged
    assert dep.encode() + b"/bin/pigz" in staged
    assert app.source.stat().st_mode & 0o111      # exec bit preserved


def test_scan_prefix_unifies_in_text(tmp_path):
    own = "/opt/spack/linux/foopkg-1.0-" + "a" * 32
    dep = "/opt/spack/linux/bar-2.0-" + "b" * 32
    prefix = tmp_path / ("foopkg-1.0-" + "a" * 32)
    (prefix / "lib" / "pkgconfig").mkdir(parents=True)
    (prefix / ".spack").mkdir()
    pc = f"prefix={own}\nCflags: -I{dep}/include\n"
    (prefix / "lib" / "pkgconfig" / "foo.pc").write_text(pc)

    entries = conda.scan_prefix(prefix, [own, dep], tmp_path / "stage")
    e = next(x for x in entries if x.path == "lib/pkgconfig/foo.pc")
    assert e.file_mode == "text"
    assert e.prefix_placeholder == dep
    staged = e.source.read_text()
    assert own not in staged
    assert f"prefix={dep}" in staged
    assert f"-I{dep}/include" in staged


def test_scan_prefix_executor_matches_serial(tmp_path):
    # Parallel per-file scanning must produce exactly the same entries
    # (same order, digests, relocation) as the serial path.
    from concurrent.futures import ThreadPoolExecutor

    own = "/opt/spack/linux/many-1.0-" + "a" * 32
    dep = "/opt/spack/linux/d-1.0-" + "b" * 32
    prefix = tmp_path / ("many-1.0-" + "a" * 32)
    (prefix / "bin").mkdir(parents=True)
    (prefix / ".spack").mkdir()
    for i in range(25):
        (prefix / "bin" / f"f{i:02d}").write_bytes(
            b"\x7fELF\x00" + own.encode() + f"/x{i}\x00".encode()
            + dep.encode() + b"/bin/tool\x00")

    def digests(entries):
        return [(e.path, e.sha256, e.prefix_placeholder) for e in entries]

    serial = conda.scan_prefix(prefix, [own, dep], tmp_path / "s1")
    with ThreadPoolExecutor(max_workers=8) as ex:
        par = conda.scan_prefix(prefix, [own, dep], tmp_path / "s2", executor=ex)
    assert digests(serial) == digests(par)
    assert all(e.prefix_placeholder == dep for e in par)


def test_build_conda_package_missing_prefix(tmp_path):
    meta = conda.PackageMeta("x", "1", "b", "linux-64")
    with pytest.raises(conda.CondaBuildError):
        conda.build_conda_package(tmp_path / "nonexistent", meta, tmp_path / "out")


def test_build_conda_package_empty_metapackage(tmp_path):
    # Spack "bundle" packages (e.g. glx) install only a .spack metadir and
    # no payload; they must still convert to a valid empty metapackage so
    # dependents can resolve them from the channel.
    prefix = tmp_path / "glx-1.4-deadbeef"
    (prefix / ".spack").mkdir(parents=True)
    meta = conda.PackageMeta("glx", "1.4", "deadbeef", "linux-64",
                             depends=["mesa 23.0 abc123"])
    result = conda.build_conda_package(prefix, meta, tmp_path / "out")
    out = result.path
    assert out.is_file()
    # No files at all, so no binary-relocation constraint.
    assert result.prefix_limit is None

    info = read_inner_tar(out, f"info-{meta.filestem}.tar.zst")
    index = json.load(info.extractfile("info/index.json"))
    assert index["depends"] == ["mesa 23.0 abc123"]
    paths = json.load(info.extractfile("info/paths.json"))
    assert paths["paths"] == []
    assert info.extractfile("info/files").read() == b""

    pkg = read_inner_tar(out, f"pkg-{meta.filestem}.tar.zst")
    assert pkg.getnames() == []
