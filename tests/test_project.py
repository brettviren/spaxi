import pytest

from spaxi.project import Project, ProjectError, find_project, split_spec


def test_split_spec():
    assert split_spec("zstd") == ("zstd", "")
    assert split_spec("zstd@1.5.7") == ("zstd", "@1.5.7")
    assert split_spec("zstd@1.5:~programs") == ("zstd", "@1.5:~programs")
    assert split_spec("zstd+programs") == ("zstd", "+programs")
    assert split_spec("hdf5%gcc") == ("hdf5", "%gcc")
    assert split_spec("zstd/wray76q") == ("zstd", "/wray76q")


def test_init_and_load(tmp_path):
    proj = Project(tmp_path / "demo")
    proj.init(name="demo")
    manifest = proj.load()
    assert manifest["project"]["name"] == "demo"
    assert manifest["dependencies"] == {}
    with pytest.raises(ProjectError):
        proj.init()  # already exists


def test_specs_roundtrip(tmp_path):
    proj = Project(tmp_path)
    proj.init(name="x")
    manifest = proj.load()
    manifest["dependencies"] = {"zstd": "@1.5:", "cmake": ""}
    proj.save(manifest)
    assert sorted(proj.specs()) == ["cmake", "zstd@1.5:"]


def test_find_project_upward(tmp_path, monkeypatch):
    proj = Project(tmp_path)
    proj.init(name="x")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    found = find_project()
    assert found.manifest_path == proj.manifest_path


def test_find_project_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ProjectError):
        find_project()


def test_load_missing_manifest(tmp_path):
    with pytest.raises(ProjectError):
        Project(tmp_path / "nothere").load()
