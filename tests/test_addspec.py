import tomllib

from spaxi import addspec

from conftest import needs_spack


class FakeSpack:
    """Minimal Spack stand-in exposing only concretize_one."""

    def __init__(self, node):
        self._node = node

    def concretize_one(self, spec):
        return self._node


NODE = {
    "name": "zstd",
    "version": "1.5.7",
    "hash": "abcdefghijklmnopqrstuvwxyz012345",
    "parameters": {"programs": True, "compression": ["zlib"]},
}


def test_add_spec_creates_file(tmp_path):
    path = tmp_path / "pixi.toml"
    result = addspec.add_spec(FakeSpack(NODE), "zstd +programs", path)
    assert result.created is True
    data = tomllib.loads(path.read_text())
    # a usable workspace scaffold is written
    assert data["workspace"]["name"] == tmp_path.name
    dep = data["dependencies"]["zstd"]
    assert dep["version"] == "*"
    assert "programs:true" in dep["flags"]
    assert "compression:zlib" in dep["flags"]
    # non-exact must NOT pin the hash
    assert not any(f.startswith("hash:") for f in dep["flags"])


def test_add_spec_exact_adds_hash(tmp_path):
    path = tmp_path / "pixi.toml"
    result = addspec.add_spec(FakeSpack(NODE), "zstd +programs", path, exact=True)
    assert f"hash:{NODE['hash']}" in result.flags
    dep = tomllib.loads(path.read_text())["dependencies"]["zstd"]
    assert f"hash:{NODE['hash']}" in dep["flags"]


def test_add_spec_updates_existing(tmp_path):
    path = tmp_path / "pixi.toml"
    path.write_text(
        '[workspace]\n'
        'name = "mine"\n'
        'channels = ["file:///chan"]\n'
        'platforms = [{ platform = "linux-64", glibc = "2.36" }]\n'
        'version = "0.1.0"\n\n'
        '[dependencies]\n'
        'cowsay = { version = "*" }\n'
    )
    result = addspec.add_spec(FakeSpack(NODE), "zstd +programs", path)
    assert result.created is False
    data = tomllib.loads(path.read_text())
    # existing workspace + dependency preserved, new dependency merged in
    assert data["workspace"]["name"] == "mine"
    assert data["workspace"]["channels"] == ["file:///chan"]
    assert "cowsay" in data["dependencies"]
    assert "zstd" in data["dependencies"]


def test_add_spec_custom_config_path(tmp_path):
    path = tmp_path / "custom.toml"
    addspec.add_spec(FakeSpack(NODE), "zstd", path)
    assert path.is_file()
    assert not (tmp_path / "pixi.toml").exists()


@needs_spack
def test_add_spec_real_concretization(spack_sandbox, tmp_path):
    from spaxi.spack import Spack
    spack = Spack(spack_sandbox)
    path = tmp_path / "pixi.toml"
    result = addspec.add_spec(
        spack, "zstd +programs compression=zlib", path, exact=True)
    assert result.name == "zstd"
    assert "programs:true" in result.flags
    assert "compression:zlib" in result.flags
    assert any(f.startswith("hash:") for f in result.flags)
