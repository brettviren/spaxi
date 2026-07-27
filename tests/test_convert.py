"""Unit tests for convert orchestration helpers (no spack required)."""

import os

from click.testing import CliRunner

from spaxi import convert
from spaxi.cli import cli


def test_resolve_jobs_explicit():
    assert convert._resolve_jobs(1) == 1
    assert convert._resolve_jobs(4) == 4
    # negative is clamped to a single worker
    assert convert._resolve_jobs(-3) == 1


def test_resolve_jobs_zero_is_cpu_count():
    assert convert._resolve_jobs(0) == (os.cpu_count() or 1)


def test_conda_rejects_bad_compression_level(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["conda", "--compression-level", "99", "somepkg"])
    assert result.exit_code == 1
    assert "compression level must be between 1 and 22" in result.output
