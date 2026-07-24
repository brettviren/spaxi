import json
import shutil
from pathlib import Path

import pytest

SPACK_EXE = Path(__file__).parent.parent / "spack" / "bin" / "spack"

needs_spack = pytest.mark.skipif(
    not SPACK_EXE.is_file(), reason="no bundled spack installation"
)


@pytest.fixture
def spack_sandbox(tmp_path, monkeypatch):
    """Point spack's config and cache paths at local writable dirs."""
    cache = Path(__file__).parent.parent / ".cache" / "spack-tests"
    monkeypatch.setenv("SPACK_USER_CONFIG_PATH", str(cache / "user-config"))
    monkeypatch.setenv("SPACK_USER_CACHE_PATH", str(cache / "user-cache"))
    monkeypatch.setenv("SPACK_SYSTEM_CONFIG_PATH", str(cache / "system-config"))
    return SPACK_EXE


@pytest.fixture
def fake_prefix(tmp_path):
    """A fabricated Spack-like install prefix with a spec.json."""
    prefix = tmp_path / "opt" / "frob-1.2.3-abcdefghijklmnopqrstuvwxyz012345"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "lib").mkdir()
    (prefix / ".spack").mkdir()

    # Text file embedding the prefix (relocatable, text mode).
    (prefix / "bin" / "frob-config").write_text(
        f"#!/bin/sh\necho {prefix}/lib\n"
    )
    # Binary file embedding the prefix (relocatable, binary mode).
    (prefix / "lib" / "libfrob.so.1.2.3").write_bytes(
        b"\x7fELF" + b"\0" * 8 + str(prefix).encode() + b"\0" * 8
    )
    # Symlink and a plain non-relocatable file.
    (prefix / "lib" / "libfrob.so").symlink_to("libfrob.so.1.2.3")
    (prefix / "lib" / "frob.h").write_text("#define FROB 1\n")

    spec = {
        "name": "frob",
        "version": "1.2.3",
        "hash": "abcdefghijklmnopqrstuvwxyz012345",
        "arch": {
            "platform": "linux",
            "platform_os": "debian12",
            "target": {"name": "zen4", "parents": ["zen3", "x86_64_v4"]},
        },
        "dependencies": [
            {
                "name": "glibc",
                "hash": "glibchash0123456789abcdefghijklm",
                "parameters": {"deptypes": ["link"], "virtuals": ["libc"]},
            },
            {
                "name": "gcc-runtime",
                "hash": "gccrthash0123456789abcdefghijklm",
                "parameters": {"deptypes": ["link"], "virtuals": []},
            },
            {
                "name": "gmake",
                "hash": "gmakehash0123456789abcdefghijklm",
                "parameters": {"deptypes": ["build"], "virtuals": []},
            },
        ],
    }
    (prefix / ".spack" / "spec.json").write_text(
        json.dumps({"spec": {"_meta": {"version": 5}, "nodes": [spec]}})
    )
    return prefix, spec
