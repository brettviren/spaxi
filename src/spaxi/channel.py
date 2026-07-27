"""Maintain a local conda channel directory.

A channel is a directory with one subdirectory per platform (subdir)
each holding packages and a ``repodata.json`` index.  A ``noarch``
subdir must exist (possibly empty) for conda/pixi clients to accept
the channel.
"""

import hashlib
import json
from pathlib import Path

REPODATA_VERSION = 1


def _empty_repodata(subdir: str) -> dict:
    return {
        "info": {"subdir": subdir},
        "packages": {},
        "packages.conda": {},
        "repodata_version": REPODATA_VERSION,
    }


def _load_repodata(subdir_path: Path, subdir: str) -> dict:
    repofile = subdir_path / "repodata.json"
    if repofile.is_file():
        return json.loads(repofile.read_text())
    return _empty_repodata(subdir)


def _write_repodata(subdir_path: Path, repodata: dict) -> None:
    subdir_path.mkdir(parents=True, exist_ok=True)
    (subdir_path / "repodata.json").write_text(json.dumps(repodata, indent=2, sort_keys=True))


def _file_digests(path: Path) -> tuple[str, str, int]:
    sha, md5 = hashlib.sha256(), hashlib.md5()
    size = 0
    with open(path, "rb") as fp:
        while chunk := fp.read(1 << 20):
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    return sha.hexdigest(), md5.hexdigest(), size


def ensure_channel(channel: Path) -> None:
    """Make sure the channel skeleton (noarch index) exists."""
    noarch = Path(channel) / "noarch"
    if not (noarch / "repodata.json").is_file():
        _write_repodata(noarch, _empty_repodata("noarch"))


def add_package(channel: Path, package: Path, index: dict) -> Path:
    """Add a built .conda package to the channel and index it.

    ``index`` is the package's info/index.json content.  The package
    file is moved into ``<channel>/<subdir>/`` if not already there.
    Returns the final package path.
    """
    channel = Path(channel)
    subdir = index["subdir"]
    subdir_path = channel / subdir
    subdir_path.mkdir(parents=True, exist_ok=True)
    ensure_channel(channel)

    dest = subdir_path / package.name
    if package.resolve() != dest.resolve():
        package.replace(dest)

    sha, md5, size = _file_digests(dest)
    record = {
        "name": index["name"],
        "version": index["version"],
        "build": index["build"],
        "build_number": index.get("build_number", 0),
        "depends": index.get("depends", []),
        "license": index.get("license"),
        "subdir": subdir,
        "timestamp": index.get("timestamp"),
        "sha256": sha,
        "md5": md5,
        "size": size,
    }
    # Variant flags (CEP-45) must live on the repodata record for the solver
    # to filter on them.
    if index.get("flags"):
        record["flags"] = index["flags"]
    repodata = _load_repodata(subdir_path, subdir)
    repodata["packages.conda"][dest.name] = record
    _write_repodata(subdir_path, repodata)
    return dest
