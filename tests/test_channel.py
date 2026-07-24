import json

from spaxi import channel


def test_add_package(tmp_path):
    pkg = tmp_path / "frob-1.2.3-abc.conda"
    pkg.write_bytes(b"not really a conda package but fine for indexing")
    index = {
        "name": "frob", "version": "1.2.3", "build": "abc",
        "build_number": 0, "depends": ["__glibc >=2.36"],
        "subdir": "linux-64", "timestamp": 1234567890123, "license": None,
    }
    chan = tmp_path / "channel"
    dest = channel.add_package(chan, pkg, index)

    assert dest == chan / "linux-64" / "frob-1.2.3-abc.conda"
    assert dest.is_file() and not pkg.exists()

    repodata = json.loads((chan / "linux-64" / "repodata.json").read_text())
    rec = repodata["packages.conda"]["frob-1.2.3-abc.conda"]
    assert rec["name"] == "frob"
    assert rec["depends"] == ["__glibc >=2.36"]
    assert rec["size"] == dest.stat().st_size
    assert len(rec["sha256"]) == 64 and len(rec["md5"]) == 32

    # channels require a noarch index, even if empty
    noarch = json.loads((chan / "noarch" / "repodata.json").read_text())
    assert noarch["info"]["subdir"] == "noarch"
    assert noarch["packages.conda"] == {}


def test_add_package_updates_existing_repodata(tmp_path):
    chan = tmp_path / "channel"
    for build in ("aaa", "bbb"):
        pkg = tmp_path / f"frob-1.2.3-{build}.conda"
        pkg.write_bytes(build.encode())
        index = {"name": "frob", "version": "1.2.3", "build": build,
                 "subdir": "linux-64"}
        channel.add_package(chan, pkg, index)
    repodata = json.loads((chan / "linux-64" / "repodata.json").read_text())
    assert len(repodata["packages.conda"]) == 2
