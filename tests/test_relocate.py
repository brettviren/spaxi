"""Unit tests for ELF RPATH -> $ORIGIN rewriting (pure logic)."""

from spaxi import relocate

# Two Spack package prefixes as they would appear in an RPATH.
SELF = "/opt/spack/linux/zstd-1.5.7-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
DEP = "/opt/spack/linux/gcc-runtime-11-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PREFIXES = [SELF, DEP]


def test_is_elf():
    assert relocate.is_elf(b"\x7fELF\x02\x01")
    assert not relocate.is_elf(b"#!/bin/sh\n")
    assert not relocate.is_elf(b"")


def test_lib_file_maps_to_origin():
    # A library in <prefix>/lib referencing its own and a dep's lib dir:
    # both collapse to the merged <env>/lib, i.e. $ORIGIN for a lib/ file.
    rpath = f"{SELF}/lib:{DEP}/lib"
    got = relocate.origin_rpath(rpath, "lib/libzstd.so.1.5.7", PREFIXES)
    assert got == "$ORIGIN"


def test_bin_file_maps_up_to_lib():
    rpath = f"{SELF}/lib:{DEP}/lib"
    got = relocate.origin_rpath(rpath, "bin/zstd", PREFIXES)
    assert got == "$ORIGIN/../lib"


def test_lib64_subdir_preserved():
    rpath = f"{DEP}/lib64"
    got = relocate.origin_rpath(rpath, "bin/tool", PREFIXES)
    assert got == "$ORIGIN/../lib64"


def test_system_and_relative_paths_are_kept():
    rpath = f"{SELF}/lib:/usr/lib:$ORIGIN/../lib"
    got = relocate.origin_rpath(rpath, "lib/x.so", PREFIXES)
    # self -> $ORIGIN; /usr/lib untouched; existing $ORIGIN kept; deduped.
    assert got == "$ORIGIN:/usr/lib:$ORIGIN/../lib"


def test_no_change_returns_none():
    assert relocate.origin_rpath("/usr/lib:/lib", "bin/x", PREFIXES) is None
    assert relocate.origin_rpath("$ORIGIN/../lib", "bin/x", PREFIXES) is None


def test_rewrite_non_elf_returns_none():
    assert relocate.rewrite_elf_rpaths(b"not elf", "bin/x", PREFIXES) is None
