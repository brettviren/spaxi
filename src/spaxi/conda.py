"""Build conda packages from Spack install trees.

A Spack package install area (prefix) is converted into a package in
the current conda format (``.conda``, ``conda_pkg_format_version`` 2):
a ZIP archive holding zstd-compressed ``info-*`` and ``pkg-*``
tarballs.  Such packages can be served from a plain conda channel and
installed with pixi/conda/rattler.

Relocation is expressed the conda way: every file that embeds the
Spack install prefix is recorded in ``info/paths.json`` with a
``prefix_placeholder`` so the installer rewrites it to the target
environment prefix at install time.  Spack's long hash-qualified
prefixes make good placeholders since binary replacement requires the
target prefix to be no longer than the placeholder.
"""

import hashlib
import io
import json
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import zstandard

# Spack-internal metadata directory inside every install prefix.
SPACK_METADIR = ".spack"

# Spack build/runtime deptypes that translate to conda "depends".
RUNTIME_DEPTYPES = {"link", "run"}

# Spack packages that map to conda virtual packages instead of being
# converted: name -> conda virtual package name.
VIRTUAL_PACKAGES = {
    "glibc": "__glibc",
}


class CondaBuildError(Exception):
    """Conversion to a conda package failed."""


def subdir_for(node: dict) -> str:
    """Map a spack spec node's arch to a conda subdir like linux-64."""
    arch = node.get("arch", {})
    platform = arch.get("platform", "linux")
    target = arch.get("target", {})
    if isinstance(target, str):
        names = [target]
    else:
        names = [target.get("name", "")] + list(target.get("parents", []))
    machine = "x86_64"
    for name in names:
        if name.startswith("x86_64") or name in ("zen", "zen2", "zen3", "zen4"):
            machine = "x86_64"
            break
        if name.startswith(("aarch64", "arm", "neoverse")) or name in ("m1", "m2"):
            machine = "aarch64"
            break
        if name.startswith("ppc64le"):
            machine = "ppc64le"
            break
        if name.startswith("riscv"):
            machine = "riscv64"
            break
    if platform == "darwin":
        return "osx-arm64" if machine == "aarch64" else "osx-64"
    if platform == "windows":
        return "win-64"
    return {"x86_64": "linux-64", "aarch64": "linux-aarch64",
            "ppc64le": "linux-ppc64le", "riscv64": "linux-riscv64"}[machine]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while chunk := fp.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _is_binary(data: bytes) -> bool:
    return b"\0" in data


@dataclass
class PathEntry:
    """One file in the package payload, as recorded in paths.json."""

    path: str
    path_type: str  # hardlink | softlink
    sha256: str | None
    size_in_bytes: int
    file_mode: str | None = None  # text | binary, when prefix embedded
    prefix_placeholder: str | None = None

    def to_json(self) -> dict:
        d = {
            "_path": self.path,
            "path_type": self.path_type,
            "size_in_bytes": self.size_in_bytes,
        }
        if self.sha256 is not None:
            d["sha256"] = self.sha256
        if self.file_mode is not None:
            d["file_mode"] = self.file_mode
            d["prefix_placeholder"] = self.prefix_placeholder
        return d


def scan_prefix(prefix: Path) -> list[PathEntry]:
    """Walk a Spack install prefix and classify its payload files.

    The Spack-internal ``.spack`` directory is excluded.  Files that
    contain the install prefix string are marked for text or binary
    prefix replacement.
    """
    prefix = Path(prefix).resolve()
    prefix_bytes = str(prefix).encode()
    entries: list[PathEntry] = []
    for path in sorted(prefix.rglob("*")):
        rel = path.relative_to(prefix)
        if rel.parts[0] == SPACK_METADIR:
            continue
        if path.is_symlink():
            target = path.resolve()
            sha = _sha256_file(target) if target.is_file() else None
            size = target.stat().st_size if target.is_file() else 0
            entries.append(PathEntry(str(rel), "softlink", sha, size))
        elif path.is_file():
            data = path.read_bytes()
            entry = PathEntry(
                str(rel), "hardlink",
                hashlib.sha256(data).hexdigest(), len(data),
            )
            if prefix_bytes in data:
                entry.file_mode = "binary" if _is_binary(data) else "text"
                entry.prefix_placeholder = str(prefix)
            entries.append(entry)
        # bare directories need no paths.json entry; tar keeps them
    return entries


@dataclass
class PackageMeta:
    """Identity and metadata of the conda package being built."""

    name: str
    version: str
    build: str
    subdir: str
    depends: list[str] = field(default_factory=list)
    build_number: int = 0
    license: str | None = None

    @property
    def filestem(self) -> str:
        return f"{self.name}-{self.version}-{self.build}"

    def index_json(self) -> dict:
        platform, arch = self.subdir.split("-", 1)
        if platform == "osx":
            platform = "darwin"
        return {
            "name": self.name,
            "version": self.version,
            "build": self.build,
            "build_number": self.build_number,
            "depends": self.depends,
            "subdir": self.subdir,
            "platform": platform,
            "arch": {"64": "x86_64", "arm64": "arm64"}.get(arch, arch),
            "license": self.license,
            "timestamp": int(time.time() * 1000),
        }


def meta_from_spec(node: dict, dep_nodes: dict[str, dict]) -> PackageMeta:
    """Derive conda package metadata from a spack spec node.

    ``dep_nodes`` maps dependency hash to its full spec node, for the
    runtime (link/run) dependencies of ``node``.  The conda build
    string is the spack DAG hash, so distinct spack builds coexist in
    one channel.  Runtime deps become exact conda pins; glibc becomes
    the ``__glibc`` virtual package.
    """
    depends = []
    for dep in node.get("dependencies", []):
        deptypes = set(dep.get("parameters", {}).get("deptypes", []))
        if not deptypes & RUNTIME_DEPTYPES:
            continue
        dep_node = dep_nodes.get(dep["hash"])
        if dep_node is None:
            raise CondaBuildError(
                f"missing spec node for runtime dependency {dep['name']}/{dep['hash'][:7]}"
            )
        virtual = VIRTUAL_PACKAGES.get(dep["name"])
        if virtual:
            depends.append(f"{virtual} >={dep_node['version']}")
        else:
            depends.append(
                f"{dep['name']} {dep_node['version']} {dep_node['hash']}"
            )
    return PackageMeta(
        name=node["name"],
        version=str(node["version"]),
        build=node["hash"],
        subdir=subdir_for(node),
        depends=sorted(depends),
    )


def _tar_add(tar: tarfile.TarFile, source: Path, arcname: str) -> None:
    """Add a filesystem entry to a tar with normalized ownership."""
    info = tar.gettarinfo(source, arcname=arcname)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if info.isfile():
        with open(source, "rb") as fp:
            tar.addfile(info, fp)
    else:
        tar.addfile(info)


def _tar_add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def _compress_into_zip(zf: zipfile.ZipFile, arcname: str, tarpath: Path) -> None:
    """zstd-compress a tar file and store it in the .conda zip."""
    cctx = zstandard.ZstdCompressor(level=19)
    with tempfile.NamedTemporaryFile() as ztmp:
        with open(tarpath, "rb") as fin:
            cctx.copy_stream(fin, ztmp)
        ztmp.flush()
        zf.write(ztmp.name, arcname)


def build_conda_package(
    prefix: Path,
    meta: PackageMeta,
    outdir: Path,
    about: dict | None = None,
) -> Path:
    """Convert a Spack install prefix into a .conda package file.

    Returns the path of the package written under ``outdir``.
    """
    prefix = Path(prefix)
    if not prefix.is_dir():
        raise CondaBuildError(f"install prefix is not a directory: {prefix}")
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    entries = scan_prefix(prefix)
    if not entries:
        raise CondaBuildError(f"no payload files found under {prefix}")

    paths_json = {
        "paths": [e.to_json() for e in entries],
        "paths_version": 1,
    }
    files_txt = "\n".join(e.path for e in entries) + "\n"
    spec_json = prefix / SPACK_METADIR / "spec.json"

    outpath = outdir / f"{meta.filestem}.conda"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        pkg_tar = tmp / "pkg.tar"
        with tarfile.open(pkg_tar, "w") as tar:
            for entry in entries:
                _tar_add(tar, prefix / entry.path, entry.path)

        info_tar = tmp / "info.tar"
        with tarfile.open(info_tar, "w") as tar:
            _tar_add_bytes(
                tar, "info/index.json", json.dumps(meta.index_json(), indent=2).encode()
            )
            _tar_add_bytes(
                tar, "info/paths.json", json.dumps(paths_json, indent=2).encode()
            )
            _tar_add_bytes(tar, "info/files", files_txt.encode())
            _tar_add_bytes(
                tar, "info/about.json", json.dumps(about or {}, indent=2).encode()
            )
            if spec_json.is_file():
                _tar_add(tar, spec_json, "info/spack-spec.json")

        with zipfile.ZipFile(outpath, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("metadata.json", json.dumps({"conda_pkg_format_version": 2}))
            _compress_into_zip(zf, f"info-{meta.filestem}.tar.zst", info_tar)
            _compress_into_zip(zf, f"pkg-{meta.filestem}.tar.zst", pkg_tar)

    return outpath
