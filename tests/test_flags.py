import pytest

from spaxi import flags


def test_squash():
    assert flags.squash("zlib") == "zlib"
    assert flags.squash("LZ4") == "lz4"
    assert flags.squash("1.2.3") == "1_2_3"
    assert flags.squash("c++") == "c"
    assert flags.squash("a  b--c") == "a_b_c"
    assert flags.squash("--x--") == "x"
    # never empty
    assert flags.squash("+++") == "_"


def test_variant_flags_kinds():
    node = {
        "hash": "abcdefghijklmnopqrstuvwxyz012345",
        "parameters": {
            "programs": True,
            "shared": False,
            "compression": ["lz4", "lzma", "zlib"],
            "build_system": "makefile",
            "cxxstd": 17,
            # compiler-flag params are ignored
            "cflags": ["-O2"],
            "ldflags": [],
        },
    }
    got = flags.variant_flags(node)
    assert got == sorted([
        "programs:true",
        "shared:false",
        "compression:lz4",
        "compression:lzma",
        "compression:zlib",
        "compression_set:lz4_lzma_zlib",
        "build_system:makefile",
        "cxxstd:17",
        "hash:abcdefghijklmnopqrstuvwxyz012345",
    ])
    # compiler-flag holders never appear
    assert not any(f.startswith(("cflags", "ldflags")) for f in got)


def test_set_flag_is_sorted_and_deduped():
    # unsorted, duplicate, mixed-case values -> canonical lexical join
    node = {"parameters": {"compression": ["Zlib", "lz4", "zlib", "LZ4"]}}
    got = flags.variant_flags(node, include_hash=False)
    assert "compression_set:lz4_zlib" in got
    # membership flags for each distinct squashed value
    assert "compression:lz4" in got and "compression:zlib" in got


def test_singleton_multivalue_gets_set_flag():
    # a one-element multi-valued variant still gets an atomic _set flag, so it
    # is distinguishable from a superset that also contains that value
    node = {"parameters": {"compression": ["zlib"]}}
    got = flags.variant_flags(node, include_hash=False)
    assert "compression:zlib" in got
    assert "compression_set:zlib" in got


def test_empty_multivalue_has_no_flags():
    node = {"parameters": {"compression": []}}
    assert flags.variant_flags(node, include_hash=False) == []


def test_scalar_and_bool_have_no_set_flag():
    node = {"parameters": {"build_system": "makefile", "programs": True}}
    got = flags.variant_flags(node, include_hash=False)
    assert not any(f.startswith(("build_system_set", "programs_set")) for f in got)


def test_collision_with_hash_key_raises():
    node = {"hash": "h", "parameters": {"hash": True}}
    with pytest.raises(flags.FlagCollisionError):
        flags.variant_flags(node)


def test_collision_with_set_key_raises():
    # a multi-valued 'foo' synthesizes 'foo_set'; a real 'foo_set' variant clashes
    node = {"parameters": {"foo": ["a", "b"], "foo_set": True}}
    with pytest.raises(flags.FlagCollisionError):
        flags.variant_flags(node, include_hash=False)


def test_no_false_collision_when_no_set_synthesized():
    # 'foo' is scalar (no _set synthesized), so a 'foo_set' variant is fine
    node = {"parameters": {"foo": "x", "foo_set": True}}
    got = flags.variant_flags(node, include_hash=False)
    assert "foo:x" in got and "foo_set:true" in got


def test_variant_flags_hash_toggle():
    node = {"hash": "deadbeef", "parameters": {"programs": False}}
    assert "hash:deadbeef" in flags.variant_flags(node, include_hash=True)
    assert flags.variant_flags(node, include_hash=False) == ["programs:false"]


def test_variant_flags_no_parameters():
    node = {"hash": "cafef00d"}
    assert flags.variant_flags(node) == ["hash:cafef00d"]
    assert flags.variant_flags(node, include_hash=False) == []


def test_flag_charset_is_valid():
    import re
    pat = re.compile(r"^[a-z0-9_]+(:[a-z0-9_]+)?$")
    node = {
        "hash": "h1",
        "parameters": {"Weird Name": "Value 1.0!", "multi": ["A/B", "c d"]},
    }
    for flag in flags.variant_flags(node):
        assert pat.match(flag), flag
