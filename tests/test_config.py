from pathlib import Path

from spaxi.config import Config


def test_defaults(tmp_path):
    cfg = Config(tmp_path / "nonexistent.toml", environ={})
    assert cfg.get("spack", "exe") is None
    assert cfg.get("conda", "channel", "fallback") == "fallback"


def test_env_layer(tmp_path):
    cfg = Config(tmp_path / "nonexistent.toml",
                 environ={"SPAXI_SPACK_EXE": "/env/spack"})
    assert cfg.get("spack", "exe") == "/env/spack"


def test_file_overrides_env(tmp_path):
    conf = tmp_path / "config.toml"
    conf.write_text('[spack]\nexe = "/file/spack"\n')
    cfg = Config(conf, environ={"SPAXI_SPACK_EXE": "/env/spack"})
    assert cfg.get("spack", "exe") == "/file/spack"


def test_cli_overrides_file(tmp_path):
    conf = tmp_path / "config.toml"
    conf.write_text('[spack]\nexe = "/file/spack"\n')
    cfg = Config(conf, environ={})
    cfg.set("spack", "exe", "/cli/spack")
    assert cfg.get("spack", "exe") == "/cli/spack"


def test_cli_none_does_not_override(tmp_path):
    conf = tmp_path / "config.toml"
    conf.write_text('[conda]\nchannel = "/file/channel"\n')
    cfg = Config(conf, environ={})
    cfg.set("conda", "channel", None)
    assert cfg.get("conda", "channel") == "/file/channel"
