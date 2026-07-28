"""Rewrite ELF RPATH/RUNPATH entries to be ``$ORIGIN``-relative.

Spack builds binaries with *absolute* RPATHs into per-package install
prefixes -- both the package's own prefix and those of its dependencies.
Conda instead merges every package of an environment into a single
prefix, so a converted package must not carry absolute Spack paths: they
would only resolve while the original Spack store exists.

This module rewrites each RPATH component that points into a known Spack
prefix into an ``$ORIGIN``-relative path.  ``$ORIGIN`` is expanded by the
dynamic loader to the directory holding the binary, so once every package
lands under one environment prefix the relative path finds the merged
``lib`` (etc.) directory regardless of where that prefix is.

The rewrite only ever *shrinks* the RPATH string (``$ORIGIN/../lib`` is far
shorter than an absolute Spack path), so it is done in place -- the string
is overwritten and the remainder zeroed, leaving all ELF section and
segment layout untouched.  Anything unexpected (unparseable header, a
component that would grow) leaves the file unchanged; callers then fall
back to the original absolute paths rather than a corrupt binary.

``$ORIGIN`` only helps the dynamic loader.  Absolute Spack paths also hide
in read-only data as compiled-in program/data paths (e.g. GNU tar's
``libexec/rmt`` and its hard-coded compressor helpers) and in text files
(pkg-config ``.pc``, libtool ``.la``, shebangs).  :func:`unify_prefix_refs`
handles those: it folds every reference to a *converted-closure* prefix onto
one common placeholder, since conda merges every package into one prefix so
``<pkgprefix>/<sub>`` always lands at ``<env>/<sub>``.  Recording that one
placeholder lets conda's install-time replacement remap them all at once.
"""

import os
import posixpath
import struct

ELF_MAGIC = b"\x7fELF"

# Dynamic-section tags and program-header types we care about.
DT_NULL = 0
DT_STRTAB = 5
DT_RPATH = 15
DT_RUNPATH = 29
PT_LOAD = 1
PT_DYNAMIC = 2


def is_elf(data: bytes) -> bool:
    return data[:4] == ELF_MAGIC


def _origin_component(component: str, file_dir: str, prefixes: list[str]) -> str:
    """Map one RPATH component to ``$ORIGIN``-relative, or keep it as is.

    ``file_dir`` is the binary's directory relative to its install prefix
    (e.g. ``lib`` or ``bin``).  A component under a known Spack ``prefix``
    keeps its sub-path (``prefix/lib`` -> ``lib``), which after the conda
    merge lives at ``<env>/lib``; the result is ``$ORIGIN`` plus the path
    from ``file_dir`` to that sub-path.  System paths and already-relative
    entries are returned unchanged.
    """
    if component.startswith("$ORIGIN") or component.startswith("${ORIGIN}"):
        return component
    if not component.startswith("/"):
        return component
    for prefix in prefixes:
        prefix = prefix.rstrip("/")
        if component == prefix or component.startswith(prefix + "/"):
            subdir = component[len(prefix):].lstrip("/")
            rel = posixpath.relpath(subdir or ".", file_dir or ".")
            return "$ORIGIN" if rel == "." else posixpath.join("$ORIGIN", rel)
    return component


def origin_rpath(rpath: str, file_relpath: str, prefixes: list[str]) -> str | None:
    """Rewrite a colon-separated RPATH, or None if nothing changed.

    Duplicate components (common once several Spack prefixes collapse onto
    the same ``$ORIGIN`` path) are removed while preserving order.
    """
    file_dir = posixpath.dirname(file_relpath)
    out: list[str] = []
    changed = False
    for comp in rpath.split(":"):
        if not comp:
            continue
        new = _origin_component(comp, file_dir, prefixes)
        if new != comp:
            changed = True
        if new not in out:
            out.append(new)
    if not changed:
        return None
    return ":".join(out)


def rewrite_elf_rpaths(data: bytes, file_relpath: str,
                       prefixes: list[str]) -> bytes | None:
    """Return ELF ``data`` with ``$ORIGIN``-relative RPATHs, or None.

    None means "leave the file as it is": not an ELF object, no RPATH to
    change, or anything we could not safely parse.
    """
    if not is_elf(data):
        return None
    try:
        return _rewrite(bytearray(data), file_relpath, prefixes)
    except Exception:
        # A parsing surprise must never yield a corrupt binary.
        return None


def _rewrite(buf: bytearray, file_relpath: str,
             prefixes: list[str]) -> bytes | None:
    ei_class, ei_data = buf[4], buf[5]
    endian = "<" if ei_data == 1 else ">"

    if ei_class == 1:            # ELFCLASS32
        e_phoff = struct.unpack_from(endian + "I", buf, 0x1C)[0]
        e_phnum = struct.unpack_from(endian + "H", buf, 0x2C)[0]
        e_phentsize = struct.unpack_from(endian + "H", buf, 0x2A)[0]
        addr = endian + "I"      # pointer-sized field
        dyn_fmt, dyn_size = endian + "iI", 8
        ph_off_field, ph_vaddr_field, ph_filesz_field = 0x04, 0x08, 0x10
    elif ei_class == 2:          # ELFCLASS64
        e_phoff = struct.unpack_from(endian + "Q", buf, 0x20)[0]
        e_phnum = struct.unpack_from(endian + "H", buf, 0x38)[0]
        e_phentsize = struct.unpack_from(endian + "H", buf, 0x36)[0]
        addr = endian + "Q"
        dyn_fmt, dyn_size = endian + "qQ", 16
        ph_off_field, ph_vaddr_field, ph_filesz_field = 0x08, 0x10, 0x20
    else:
        return None

    # Walk program headers: locate PT_DYNAMIC and the PT_LOAD segments used
    # to translate the string-table virtual address to a file offset.
    loads: list[tuple[int, int, int]] = []  # (vaddr, offset, filesz)
    dynamic: tuple[int, int] | None = None  # (offset, filesz)
    for i in range(e_phnum):
        base = e_phoff + i * e_phentsize
        p_type = struct.unpack_from(endian + "I", buf, base)[0]
        p_offset = struct.unpack_from(addr, buf, base + ph_off_field)[0]
        p_vaddr = struct.unpack_from(addr, buf, base + ph_vaddr_field)[0]
        p_filesz = struct.unpack_from(addr, buf, base + ph_filesz_field)[0]
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC:
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        return None

    def vaddr_to_off(vaddr: int) -> int | None:
        for va, off, filesz in loads:
            if va <= vaddr < va + filesz:
                return vaddr - va + off
        return None

    # Scan the dynamic section for the string table and RPATH/RUNPATH tags.
    dyn_off, dyn_size_total = dynamic
    strtab_vaddr = None
    rpath_vals: list[int] = []
    pos = dyn_off
    end = dyn_off + dyn_size_total
    while pos + dyn_size <= end:
        d_tag, d_val = struct.unpack_from(dyn_fmt, buf, pos)
        pos += dyn_size
        if d_tag == DT_NULL:
            break
        if d_tag == DT_STRTAB:
            strtab_vaddr = d_val
        elif d_tag in (DT_RPATH, DT_RUNPATH):
            rpath_vals.append(d_val)
    if strtab_vaddr is None or not rpath_vals:
        return None
    strtab_off = vaddr_to_off(strtab_vaddr)
    if strtab_off is None:
        return None

    changed = False
    for d_val in rpath_vals:
        start = strtab_off + d_val
        stop = buf.index(0, start)
        old = bytes(buf[start:stop]).decode("utf-8", "surrogateescape")
        new = origin_rpath(old, file_relpath, prefixes)
        if new is None:
            continue
        new_bytes = new.encode("utf-8", "surrogateescape")
        if len(new_bytes) > len(old.encode()):
            continue  # would need to grow the string table; skip safely
        buf[start:start + len(new_bytes)] = new_bytes
        # Zero the tail so no stale absolute Spack path lingers in .dynstr.
        for j in range(start + len(new_bytes), stop):
            buf[j] = 0
        changed = True

    return bytes(buf) if changed else None


def _referenced_prefixes(data: bytes, prefixes: list[str]) -> list[str]:
    """Which of ``prefixes`` actually appear in ``data``.

    Every closure prefix shares a common leading string, so we scan for that
    anchor once and test the (few) candidate prefixes at each hit -- instead
    of scanning the whole file once per prefix, which for a big binary and a
    large closure is the dominant, GIL-held cost of relocation.
    """
    encoded = [(p, p.encode()) for p in prefixes]
    anchor = os.path.commonprefix(list(prefixes)).encode()
    if not anchor:
        return [p for p, pb in encoded if pb in data]
    present: list[str] = []
    seen: set[str] = set()
    i = data.find(anchor)
    while i >= 0:
        for p, pb in encoded:
            if p not in seen and data.startswith(pb, i):
                seen.add(p)
                present.append(p)
        if len(seen) == len(encoded):
            break
        i = data.find(anchor, i + 1)
    return present


def unify_prefix_refs(data: bytes, prefixes: list[str]) -> tuple[bytes, str | None]:
    """Fold every embedded closure-prefix reference onto one placeholder.

    Any absolute path into a converted-closure Spack ``prefix`` is rewritten
    so all of them share a single placeholder string -- the *shortest*
    referenced prefix.  Conda then remaps that one placeholder to the install
    prefix, turning ``<placeholder>/<sub>`` into ``<env>/<sub>``, which is
    exactly where each package's ``<sub>`` lands in the merged environment.

    Returns ``(data_out, placeholder)``.  ``placeholder`` is the common
    prefix string, or None when no prefix is referenced.  ``data_out`` is a
    new ``bytes`` when a rewrite happened and the original ``data`` otherwise.

    For binary data (any NUL byte present) the rewrite is length-preserving:
    each reference is shrunk within its enclosing NUL-delimited segment and
    the freed tail zeroed, so no file offset moves.  Text data is rewritten
    freely.  Because the placeholder is the shortest prefix, every other
    reference is at least as long and thus always fits.
    """
    # Order by (length, string) so the chosen placeholder -- and thus the
    # produced bytes -- are deterministic even when prefixes tie in length
    # (reproducible builds); the shortest is the fold target so every other
    # reference is at least as long and fits when replaced in place.
    referenced = sorted(_referenced_prefixes(data, prefixes),
                        key=lambda p: (len(p), p))
    if not referenced:
        return data, None
    placeholder = referenced[0]
    targets = referenced[1:]  # longer prefixes to fold onto the placeholder
    if not targets:
        return data, placeholder  # only the placeholder prefix is present

    common = placeholder.encode()
    # Replace longer strings first so a shorter prefix can never chew into a
    # longer one that contains it as a leading substring.
    target_bytes = sorted((p.encode() for p in targets),
                          key=lambda b: (len(b), b), reverse=True)

    if b"\0" in data:
        buf = bytearray(data)
        _binary_unify(buf, target_bytes, common)
        return bytes(buf), placeholder

    out = data
    for tb in target_bytes:
        out = out.replace(tb, common)
    return out, placeholder


def _binary_unify(buf: bytearray, targets: list[bytes], common: bytes) -> None:
    """Replace each target prefix with ``common`` inside a binary buffer.

    Works one NUL-delimited segment at a time so byte offsets outside the
    segment never move: the segment is rewritten (all targets -> common) and
    its freed tail zeroed, keeping the segment's total length.
    """
    for target in targets:
        pos = 0
        while (i := buf.find(target, pos)) >= 0:
            seg_end = buf.find(b"\0", i)
            if seg_end < 0:
                seg_end = len(buf)
            seg_start = buf.rfind(b"\0", 0, i) + 1  # byte after the prior NUL
            segment = bytes(buf[seg_start:seg_end])
            new_seg = segment
            for other in targets:
                new_seg = new_seg.replace(other, common)
            buf[seg_start:seg_start + len(new_seg)] = new_seg
            for j in range(seg_start + len(new_seg), seg_end):
                buf[j] = 0
            pos = seg_end  # this segment no longer holds any target
