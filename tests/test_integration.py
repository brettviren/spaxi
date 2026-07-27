"""Integration tests against the bundled spack installation.

These exercise the real spack executable and its installed zstd.
They are skipped when ./spack is not present.
"""

import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest
import zstandard

from spaxi import convert
from spaxi.project import Project
from spaxi.spack import Spack

from conftest import needs_pixi, needs_spack

pytestmark = needs_spack

needs_readelf = pytest.mark.skipif(
    shutil.which("readelf") is None, reason="readelf not available"
)


def _extract_payload_member(conda_path, predicate):
    """Return (name, bytes) of the first payload file matching predicate."""
    stem = conda_path.name[: -len(".conda")]
    with zipfile.ZipFile(conda_path) as zf:
        raw = zf.read(f"pkg-{stem}.tar.zst")
    data = zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 30)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        for member in tf.getmembers():
            if member.isfile() and predicate(member.name):
                return member.name, tf.extractfile(member).read()
    raise AssertionError("no matching payload member found")


def test_resolve_one(spack_sandbox):
    spack = Spack(spack_sandbox)
    # Several zstd variants may be installed; qualify to one.
    node = spack.resolve_one("zstd ~programs")
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
    results = convert.convert_spec(spack, "zstd ~programs", chan)
    byname = {r.name: r for r in results}
    assert byname["zstd"].path is not None
    assert byname["zstd"].path.is_file()
    # runtime dep converted too, so the channel is self-contained
    assert "gcc-runtime" in byname
    repodata = json.loads((chan / "linux-64" / "repodata.json").read_text())
    zstd_rec = next(v for k, v in repodata["packages.conda"].items()
                    if k.startswith("zstd-"))
    # variant flags (CEP-45) are recorded, including the exact-pin hash flag
    assert "programs:false" in zstd_rec["flags"]
    assert any(f.startswith("hash:") for f in zstd_rec["flags"])
    assert (chan / "noarch" / "repodata.json").is_file()

    # converting again skips work
    again = convert.convert_spec(spack, "zstd ~programs", chan)
    assert all("skipped" in r.note for r in again if r.path)


def test_convert_zstd_parallel_matches_serial(spack_sandbox, tmp_path):
    spack = Spack(spack_sandbox)
    serial = convert.convert_spec(spack, "zstd ~programs", tmp_path / "serial")
    parallel = convert.convert_spec(
        spack, "zstd ~programs", tmp_path / "parallel", jobs=0)
    # Same package set regardless of worker count, and discovery order is
    # preserved so results line up one-to-one.
    assert [r.hash for r in serial] == [r.hash for r in parallel]
    # Both channels are self-contained and valid.
    for base in ("serial", "parallel"):
        repodata = json.loads(
            (tmp_path / base / "linux-64" / "repodata.json").read_text())
        assert any(k.startswith("zstd-") for k in repodata["packages.conda"])


@needs_readelf
def test_convert_rewrites_rpaths_to_origin(spack_sandbox, tmp_path):
    spack = Spack(spack_sandbox)
    chan = tmp_path / "channel"
    results = convert.convert_spec(spack, "zstd ~programs", chan)
    zstd = next(r for r in results if r.name == "zstd")

    name, data = _extract_payload_member(
        zstd.path, lambda n: "libzstd.so" in n and not n.endswith(".so"))
    lib = tmp_path / "libzstd.extracted.so"
    lib.write_bytes(data)

    out = subprocess.run(["readelf", "-d", str(lib)],
                         capture_output=True, text=True)
    assert out.returncode == 0, "rewritten binary is not a valid ELF"
    rpath_lines = [ln for ln in out.stdout.splitlines()
                   if "RPATH" in ln or "RUNPATH" in ln]
    assert rpath_lines, f"no RPATH in {name}"
    rpaths = " ".join(rpath_lines)
    # Rewritten to $ORIGIN-relative, with no absolute Spack store path left.
    assert "$ORIGIN" in rpaths
    assert "spack/opt/spack" not in rpaths


def test_no_origin_rpaths_keeps_absolute(spack_sandbox, tmp_path):
    # With rewriting disabled the original absolute Spack RPATH survives, so
    # the embedded prefix still imposes a relocation limit.
    spack = Spack(spack_sandbox)
    results = convert.convert_spec(
        spack, "zstd ~programs", tmp_path / "channel", relocate_rpaths=False)
    zstd = next(r for r in results if r.name == "zstd")
    _, data = _extract_payload_member(
        zstd.path, lambda n: "libzstd.so" in n and not n.endswith(".so"))
    assert b"$ORIGIN" not in data
    assert zstd.prefix_limit is not None


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


def _glibc_floor(rec):
    """Extract the glibc version from a record's __glibc depends."""
    for dep in rec.get("depends", []):
        if dep.startswith("__glibc"):
            return dep.split(">=")[-1].strip()
    return "2.17"


@needs_pixi
def test_variant_flag_selection(spack_sandbox, pixi_env, tmp_path):
    """All installed zstd variants coexist in one channel and each is
    individually selectable from a pixi.toml by its conda flags."""
    spack = Spack(spack_sandbox)
    builds = spack.find("zstd")
    if len(builds) < 2:
        pytest.skip("needs >=2 installed zstd variants")

    chan = tmp_path / "channel"
    for b in builds:
        convert.convert_spec(spack, f"/{b['hash']}", chan)

    repodata = json.loads((chan / "linux-64" / "repodata.json").read_text())
    zstd_recs = {k: v for k, v in repodata["packages.conda"].items()
                 if v["name"] == "zstd"}
    # every variant coexists, each with distinct flags including a hash pin
    assert len(zstd_recs) == len(builds)
    for rec in zstd_recs.values():
        assert any(f.startswith("hash:") for f in rec["flags"])
    glibc = _glibc_floor(next(iter(zstd_recs.values())))

    def pixi_pick(flags):
        # short project path: conda relocation needs env prefix <= placeholder
        proj = Path(tempfile.mkdtemp(prefix="spx"))
        try:
            (proj / "pixi.toml").write_text(
                "[workspace]\n"
                f'channels = ["{chan}"]\n'
                'name = "t"\n'
                f'platforms = [{{ platform = "linux-64", glibc = "{glibc}" }}]\n'
                'version = "0.1.0"\n'
                "[dependencies]\n"
                f"zstd = {{ version = \"*\", flags = {json.dumps(flags)} }}\n"
            )
            subprocess.run(["pixi", "install"], cwd=proj, check=True,
                           capture_output=True, text=True)
            metas = list((proj / ".pixi/envs/default/conda-meta").glob("zstd-*.json"))
            build = json.loads(metas[0].read_text())["build"]
            has_bin = (proj / ".pixi/envs/default/bin/zstd").exists()
            return build, has_bin
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    # 1) each build is individually addressable by its unique hash flag
    for rec in zstd_recs.values():
        hflag = next(f for f in rec["flags"] if f.startswith("hash:"))
        picked, _ = pixi_pick([hflag])
        assert picked == rec["build"]

    # 2) boolean variant: ~programs has no CLI, +programs does
    if any("programs:false" in r["flags"] for r in zstd_recs.values()):
        _, has_bin = pixi_pick(["programs:false"])
        assert has_bin is False
    if any("programs:true" in r["flags"] for r in zstd_recs.values()):
        _, has_bin = pixi_pick(["programs:true"])
        assert has_bin is True

    # 3) multi-valued variant, membership flags: the build with all three
    #    compression methods is a candidate when all three are required
    triple = {"compression:lz4", "compression:lzma", "compression:zlib"}
    matches = [r for r in zstd_recs.values() if triple <= set(r["flags"])]
    if matches:
        picked, _ = pixi_pick(sorted(triple))
        assert picked in {r["build"] for r in matches}

    # 4) atomic set flag: a build's own compression_set flag selects exactly
    #    that build -- including a zlib-only set that must NOT match the
    #    superset that also contains zlib (subset-vs-superset disambiguation)
    set_of = {r["build"]: next((f for f in r["flags"]
                                if f.startswith("compression_set:")), None)
              for r in zstd_recs.values()}
    set_of = {b: f for b, f in set_of.items() if f}
    for build, setflag in set_of.items():
        picked, _ = pixi_pick([setflag])
        assert picked == build


def test_resolve_one_ambiguous(spack_sandbox):
    from spaxi.spack import AmbiguousSpecError
    spack = Spack(spack_sandbox)
    builds = spack.find("zstd")
    if len(builds) < 2:
        pytest.skip("needs >=2 installed zstd variants")
    with pytest.raises(AmbiguousSpecError) as excinfo:
        spack.resolve_one("zstd")
    assert excinfo.value.spec == "zstd"
    assert len(excinfo.value.matches) == len(builds)


def test_find_verbose_shows_variants(spack_sandbox):
    spack = Spack(spack_sandbox)
    out = spack.find_verbose("zstd")
    assert "zstd@" in out
    assert "programs" in out          # -v exposes variants


def test_conda_command_ambiguous_shows_variant_table(spack_sandbox, tmp_path):
    from click.testing import CliRunner
    from spaxi.cli import cli
    spack = Spack(spack_sandbox)
    if len(spack.find("zstd")) < 2:
        pytest.skip("needs >=2 installed zstd variants")
    result = CliRunner().invoke(
        cli, ["--spack-exe", str(spack_sandbox),
              "--channel", str(tmp_path / "ch"), "conda", "zstd"])
    assert result.exit_code == 1
    combined = result.output or ""
    try:
        combined += result.stderr or ""
    except (ValueError, AttributeError):
        pass
    assert "is ambiguous" in combined
    # the `spack find -lv` table is shown, with hashes and variant detail
    assert "+programs" in combined and "compression" in combined
