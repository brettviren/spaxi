"""pixi-like project management on top of Spack (strategy 2).

A spaxi project is a directory holding:

- ``spaxi.toml``   -- the user-facing manifest (like pixi.toml)
- ``.spaxi/env/``  -- a Spack environment realizing the manifest
- ``.spaxi/envs/default/`` -- the environment view (bin/, lib/, ...)

Dependencies in the manifest are Spack specs, keyed by package name:

    [dependencies]
    zstd = "@1.5:"     # constraint part of the spec, may be ""

Spack does the solving (concretization), installing (from source or a
binary build cache, per the Spack configuration) and view building.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from .spack import Spack, SpackError

MANIFEST_NAME = "spaxi.toml"
DOTDIR = ".spaxi"
ENV_SUBDIR = "env"
VIEW_SUBDIR = "envs/default"


class ProjectError(Exception):
    """A spaxi project operation failed."""


@dataclass
class Project:
    root: Path

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def env_dir(self) -> Path:
        return self.root / DOTDIR / ENV_SUBDIR

    @property
    def view_dir(self) -> Path:
        return self.root / DOTDIR / VIEW_SUBDIR

    # ------------------------------------------------------------------
    # Manifest

    def load(self) -> dict:
        try:
            with open(self.manifest_path, "rb") as fp:
                return tomllib.load(fp)
        except FileNotFoundError:
            raise ProjectError(
                f"no {MANIFEST_NAME} found at {self.root}; run 'spaxi init' first"
            )

    def save(self, manifest: dict) -> None:
        with open(self.manifest_path, "wb") as fp:
            tomli_w.dump(manifest, fp)

    def specs(self) -> list[str]:
        """Manifest dependencies as full spack specs."""
        deps = self.load().get("dependencies", {})
        return [f"{name}{constraint}" for name, constraint in deps.items()]

    # ------------------------------------------------------------------
    # Operations

    def spack_env(self, spack_exe: Path) -> Spack:
        return Spack(spack_exe, env_dir=self.env_dir)

    def init(self, name: str | None = None) -> None:
        """Create a new project: manifest and backing spack environment."""
        if self.manifest_path.exists():
            raise ProjectError(f"{self.manifest_path} already exists")
        self.root.mkdir(parents=True, exist_ok=True)
        self.save(
            {
                "project": {"name": name or self.root.resolve().name},
                "dependencies": {},
            }
        )

    def sync_env(self, spack: Spack) -> None:
        """Make the spack environment match the manifest specs."""
        spack.env_create(view_dir=self.view_dir)
        current = set(spack.env_roots())
        wanted = set(self.specs())
        for spec in sorted(current - wanted):
            spack.env_remove(spec)
        for spec in sorted(wanted - current):
            spack.env_add(spec)

    def install(self, spack: Spack) -> None:
        """Concretize and install the environment, updating the view."""
        self.load()  # raise early if there is no manifest
        self.sync_env(spack)
        spack.env_concretize()
        spack.env_install()

    def add(self, spack: Spack, specs: list[str]) -> None:
        """Add specs to the manifest and install them."""
        manifest = self.load()
        deps = manifest.setdefault("dependencies", {})
        for spec in specs:
            name, constraint = split_spec(spec)
            deps[name] = constraint
        self.save(manifest)
        self.install(spack)

    def remove(self, spack: Spack, names: list[str]) -> None:
        """Remove packages from the manifest and the environment."""
        manifest = self.load()
        deps = manifest.setdefault("dependencies", {})
        missing = [n for n in names if split_spec(n)[0] not in deps]
        if missing:
            raise ProjectError(f"not in {MANIFEST_NAME}: {', '.join(missing)}")
        for name in names:
            del deps[split_spec(name)[0]]
        self.save(manifest)
        self.install(spack)

    def installed(self, spack: Spack) -> list[dict]:
        """Concrete installed specs of the environment (root-first)."""
        return spack.find()

    def info(self) -> dict:
        """Project information for 'spaxi info'."""
        manifest = self.load()
        return {
            "name": manifest.get("project", {}).get("name", ""),
            "manifest": str(self.manifest_path),
            "environment": str(self.env_dir),
            "view": str(self.view_dir),
            "dependencies": manifest.get("dependencies", {}),
        }


def split_spec(spec: str) -> tuple[str, str]:
    """Split a spack spec into (package-name, constraint-remainder)."""
    for i, ch in enumerate(spec):
        if ch in "@+~%^ =:/":
            return spec[:i], spec[i:]
    return spec, ""


def find_project(start: Path | None = None) -> Project:
    """Locate the project by searching upward for spaxi.toml."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / MANIFEST_NAME).is_file():
            return Project(candidate)
    raise ProjectError(
        f"no {MANIFEST_NAME} found in {here} or any parent; run 'spaxi init'"
    )
