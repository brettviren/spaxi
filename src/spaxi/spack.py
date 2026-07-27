"""Thin wrapper around the ``spack`` command line program.

All interaction with Spack goes through this module.  It knows how to
locate the ``spack`` executable, resolve specs against the installed
packages and drive Spack environments (used by the pixi-like
subcommands).
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class SpackError(Exception):
    """A spack invocation or resolution failed."""


class AmbiguousSpecError(SpackError):
    """A spec matched more than one installed package.

    Carries the original ``spec`` and the matching nodes so callers can show
    richer disambiguation help (e.g. ``spack find -lv <spec>``).
    """

    def __init__(self, spec: str, matches: list[dict]):
        self.spec = spec
        self.matches = matches
        names = ", ".join(
            f"{m['name']}@{m['version']}/{m['hash'][:7]}" for m in matches
        )
        super().__init__(
            f"spec '{spec}' is ambiguous, matches: {names}; "
            "qualify it, e.g. with /<hash>"
        )


def locate_spack(explicit: str | None = None) -> Path:
    """Find the spack executable.

    Search order: explicit value (CLI/config/env layering), a
    ``spack/bin/spack`` directory under the current directory, then
    ``spack`` on PATH.
    """
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(Path.cwd() / "spack" / "bin" / "spack")
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    onpath = shutil.which("spack")
    if onpath:
        return Path(onpath)
    raise SpackError(
        "cannot locate the 'spack' executable; "
        "set [spack] exe in config.toml, SPAXI_SPACK_EXE, or --spack-exe"
    )


class Spack:
    """Run spack commands, optionally against a Spack environment."""

    def __init__(self, exe: str | Path, env_dir: Path | None = None):
        self.exe = Path(exe)
        self.env_dir = Path(env_dir) if env_dir else None

    def command(self, *args: str) -> list[str]:
        cmd = [str(self.exe)]
        if self.env_dir:
            cmd += ["-e", str(self.env_dir)]
        cmd += list(args)
        return cmd

    def run(self, *args: str, capture: bool = True) -> str:
        """Run a spack command and return its stdout."""
        cmd = self.command(*args)
        log.debug("running: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip() if capture else ""
            raise SpackError(
                f"command failed: {' '.join(cmd)}" + (f"\n{detail}" if detail else "")
            )
        return proc.stdout if capture else ""

    # ------------------------------------------------------------------
    # Queries against the install area

    def find(self, spec: str | None = None, installed: bool = True) -> list[dict]:
        """Resolve a spec against installed packages.

        Return the list of matching spec nodes as dicts (the elements
        of ``spack find --json`` output).  An unmatched spec returns an
        empty list rather than raising.
        """
        args = ["find", "--json"]
        if spec:
            args.append(spec)
        try:
            out = self.run(*args)
        except SpackError as err:
            # "spack find" exits non-zero when nothing matches.
            if "does not match any installed packages" in str(err) or "No package matches" in str(err):
                return []
            raise
        return json.loads(out)

    def resolve_one(self, spec: str) -> dict:
        """Resolve a spec to exactly one installed package or raise."""
        matches = self.find(spec)
        if not matches:
            raise SpackError(f"spec '{spec}' matches no installed packages")
        if len(matches) > 1:
            raise AmbiguousSpecError(spec, matches)
        return matches[0]

    def find_verbose(self, spec: str) -> str:
        """Return ``spack find -lv <spec>`` output (hashes + variants).

        Used to show the competing builds when a spec is ambiguous.
        """
        return self.run("find", "-lv", spec)

    def concretize_one(self, spec: str) -> dict:
        """Concretize ``spec`` (without installing) and return its root node.

        Uses ``spack spec --json``, so it works for specs that are not yet
        installed -- useful for authoring a pixi.toml.  The first node in the
        result is the root of the concretized DAG.
        """
        out = self.run("spec", "--json", spec)
        try:
            nodes = json.loads(out)["spec"]["nodes"]
        except (json.JSONDecodeError, KeyError, IndexError) as err:
            raise SpackError(f"could not concretize spec '{spec}': {err}")
        if not nodes:
            raise SpackError(f"spec '{spec}' concretized to no packages")
        return nodes[0]

    def prefix(self, spec_hash: str) -> Path:
        """Return the install prefix of an installed package by hash."""
        out = self.run("find", "--format", "{prefix}", f"/{spec_hash}")
        return Path(out.strip().splitlines()[0])

    # ------------------------------------------------------------------
    # Spack environment operations (pixi-like subcommands)

    def env_create(self, view_dir: Path | None = None) -> None:
        """Create the wrapped environment directory with a spack.yaml."""
        assert self.env_dir is not None
        self.env_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.env_dir / "spack.yaml"
        if manifest.exists():
            return
        view = f"'{view_dir}'" if view_dir else "false"
        manifest.write_text(
            "spack:\n"
            "  specs: []\n"
            f"  view: {view}\n"
            "  concretizer:\n"
            "    unify: true\n"
        )

    def env_add(self, *specs: str) -> None:
        self.run("add", *specs)

    def env_remove(self, *specs: str) -> None:
        self.run("remove", *specs)

    def env_concretize(self) -> None:
        self.run("concretize")

    def env_install(self) -> None:
        self.run("install", capture=False)

    def env_roots(self) -> list[str]:
        """Root specs (abstract) of the environment manifest."""
        import re

        manifest = (self.env_dir / "spack.yaml").read_text()
        # Parse the simple "specs: [a, b]" or block list spack writes.
        m = re.search(r"specs:\s*\[(.*?)\]", manifest, re.S)
        if m:
            inner = m.group(1).strip()
            return [s.strip() for s in inner.split(",") if s.strip()] if inner else []
        specs = []
        in_specs = False
        for line in manifest.splitlines():
            if re.match(r"\s*specs:\s*$", line):
                in_specs = True
                continue
            if in_specs:
                m = re.match(r"\s*-\s*(.+?)\s*$", line)
                if m:
                    specs.append(m.group(1))
                else:
                    break
        return specs
