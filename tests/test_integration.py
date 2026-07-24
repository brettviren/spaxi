"""Integration tests against the bundled spack installation.

These exercise the real spack executable and its installed zstd.
They are skipped when ./spack is not present.
"""

import json

from spaxi import convert
from spaxi.project import Project
from spaxi.spack import Spack

from conftest import needs_spack

pytestmark = needs_spack


def test_resolve_one(spack_sandbox):
    spack = Spack(spack_sandbox)
    node = spack.resolve_one("zstd")
    assert node["name"] == "zstd"
    prefix = spack.prefix(node["hash"])
    assert prefix.is_dir()
    assert (prefix / ".spack" / "spec.json").is_file()


def test_find_no_match(spack_sandbox):
    spack = Spack(spack_sandbox)
    assert spack.find("zstd@999.999") == []


def test_convert_zstd(spack_sandbox, tmp_path):
    spack = Spack(spack_sandbox)
    chan = tmp_path / "channel"
    results = convert.convert_spec(spack, "zstd", chan)
    byname = {r.name: r for r in results}
    assert byname["zstd"].path is not None
    assert byname["zstd"].path.is_file()
    # runtime dep converted too, so the channel is self-contained
    assert "gcc-runtime" in byname
    repodata = json.loads((chan / "linux-64" / "repodata.json").read_text())
    assert any(k.startswith("zstd-") for k in repodata["packages.conda"])
    assert (chan / "noarch" / "repodata.json").is_file()

    # converting again skips work
    again = convert.convert_spec(spack, "zstd", chan)
    assert all("skipped" in r.note for r in again if r.path)


def test_project_lifecycle(spack_sandbox, tmp_path, monkeypatch):
    proj = Project(tmp_path / "demo")
    proj.init(name="demo")
    spack = proj.spack_env(spack_sandbox)
    proj.add(spack, ["zstd"])
    assert proj.load()["dependencies"] == {"zstd": ""}
    names = {n["name"] for n in proj.installed(spack)}
    assert "zstd" in names
    assert (proj.view_dir / "lib").is_dir()

    proj.remove(spack, ["zstd"])
    assert proj.load()["dependencies"] == {}
