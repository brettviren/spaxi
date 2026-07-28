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


# --- unify_prefix_refs (rodata / text absolute paths) --------------------

# A longer own prefix and a genuinely shorter dependency prefix.
ROD_OWN = "/opt/spack/linux/tarball-1.35-" + "a" * 32
ROD_DEP = "/opt/spack/linux/pz-2.8-" + "b" * 32
ROD = [ROD_OWN, ROD_DEP]
assert len(ROD_DEP) < len(ROD_OWN)


def test_unify_no_reference_returns_none():
    assert relocate.unify_prefix_refs(b"nothing here", ROD) == (b"nothing here", None)


def test_unify_only_placeholder_prefix_unchanged():
    data = (ROD_DEP + "/bin/pigz").encode()
    out, ph = relocate.unify_prefix_refs(data, ROD)
    assert ph == ROD_DEP and out == data  # nothing longer to fold


def test_unify_binary_is_length_preserving():
    # NUL-delimited segments, as in .rodata.  Own is longer than dep, so the
    # shorter dep becomes the shared placeholder and own is folded onto it.
    data = (b"\x00" + ROD_OWN.encode() + b"/libexec/rmt\x00"
            + ROD_DEP.encode() + b"/bin/pigz\x00tail")
    out, ph = relocate.unify_prefix_refs(data, ROD)
    assert ph == ROD_DEP
    assert len(out) == len(data)                 # offsets preserved
    assert ROD_OWN.encode() not in out           # own folded away
    assert ROD_DEP.encode() + b"/libexec/rmt\x00" in out
    assert ROD_DEP.encode() + b"/bin/pigz\x00" in out


def test_unify_text_may_change_length():
    data = (ROD_OWN + " and " + ROD_DEP).encode()  # no NUL -> text
    out, ph = relocate.unify_prefix_refs(data, ROD)
    assert ph == ROD_DEP
    assert out == (ROD_DEP + " and " + ROD_DEP).encode()


def test_referenced_prefixes_matches_naive():
    root = "/opt/spack/linux/"
    prefixes = [root + name + "-" + str(i) + "-" + c * 32
                for i, (name, c) in enumerate(
                    [("aa", "a"), ("bb", "b"), ("cc", "c"), ("dd", "d")])]
    # Only two of the four appear (one at a path start, one mid-string).
    data = (b"\x00" + prefixes[1].encode() + b"/lib/x.so\x00"
            + b"PATH=" + prefixes[3].encode() + b"/bin\x00")
    got = set(relocate._referenced_prefixes(data, prefixes))
    naive = {p for p in prefixes if p.encode() in data}
    assert got == naive == {prefixes[1], prefixes[3]}


def test_referenced_prefixes_no_common_anchor_fallback():
    # Prefixes with no shared leading string still resolve correctly.
    prefixes = ["/a/tool-1-" + "x" * 32, "/b/lib-2-" + "y" * 32]
    data = prefixes[0].encode() + b"/bin\x00"
    assert relocate._referenced_prefixes(data, prefixes) == [prefixes[0]]


def test_unify_maps_to_env_prefix_on_replacement():
    # Simulate conda's install-time step: placeholder -> env prefix.
    data = (b"\x00" + ROD_OWN.encode() + b"/libexec/rmt\x00"
            + ROD_DEP.encode() + b"/bin/pigz\x00")
    out, ph = relocate.unify_prefix_refs(data, ROD)
    env = b"/home/u/.pixi/envs/default"
    installed = out.replace(ph.encode(), env)
    assert env + b"/libexec/rmt\x00" in installed
    assert env + b"/bin/pigz\x00" in installed
