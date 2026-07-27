"""The spaxi command line interface.

This module contains no data processing logic; it parses options,
applies configuration layering and delegates to the other modules.
"""

import sys
from pathlib import Path

import click

from . import addspec, conda, convert
from .conda import CondaBuildError
from .config import Config
from .flags import FlagCollisionError
from .log import LogError, setup_logging
from .project import Project, ProjectError, find_project
from .spack import AmbiguousSpecError, Spack, SpackError, locate_spack

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


class Main:
    """Shared state resolved from general options and configuration."""

    def __init__(self, config_file, spack_exe, channel):
        self.config = Config(config_file)
        # Command line options are the last, winning layer.
        self.config.set("spack", "exe", spack_exe)
        self.config.set("conda", "channel", channel)

    @property
    def spack_exe(self) -> Path:
        return locate_spack(self.config.get("spack", "exe"))

    @property
    def channel(self) -> Path:
        return Path(self.config.get("conda", "channel", "channel"))

    def spack(self, env_dir=None) -> Spack:
        return Spack(self.spack_exe, env_dir=env_dir)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("-c", "--config", "config_file", type=click.Path(path_type=Path),
              default=None, help="Path to a spaxi config.toml.")
@click.option("--spack-exe", type=click.Path(path_type=Path), default=None,
              help="Path to the spack executable.")
@click.option("--channel", type=click.Path(path_type=Path), default=None,
              help="Local conda channel directory (for 'spaxi conda').")
@click.option("-l", "--log-sink", default="stderr", show_default=True,
              help="Where to send log records: stderr, stdout, or a file path.")
@click.option("-L", "--log-level", default="info", show_default=True,
              help="Minimum log level: debug, info, warning, error.")
@click.pass_context
def cli(ctx, config_file, spack_exe, channel, log_sink, log_level):
    """spaxi: a pixi-like end-user experience for Spack packages."""
    try:
        setup_logging(log_sink, log_level)
    except LogError as err:
        _fail(str(err))
    ctx.obj = Main(config_file, spack_exe, channel)


def _fail(msg: str):
    click.secho(f"spaxi: {msg}", fg="red", err=True)
    sys.exit(1)


def _help_if_bare(ctx, args) -> None:
    """Commands with required arguments show help when given none."""
    if not args:
        click.echo(ctx.get_help())
        ctx.exit(0)


# ----------------------------------------------------------------------
# Strategy 1: convert spack installs to conda packages

@cli.command("conda")
@click.argument("spec", nargs=-1)
@click.option("--deps/--no-deps", default=True, show_default=True,
              help="Also convert transitive runtime dependencies.")
@click.option("--force", is_flag=True, help="Rebuild packages already in the channel.")
@click.option("-j", "--jobs", type=int, default=1, show_default=True,
              help="Convert this many packages in parallel (0 = one per CPU).")
@click.option("-z", "--compression-level", type=int,
              default=conda.DEFAULT_COMPRESSION_LEVEL, show_default=True,
              help="zstd compression level for package payloads (1-22).")
@click.pass_context
def conda_cmd(ctx, spec, deps, force, jobs, compression_level):
    """Convert an installed Spack package to a conda package.

    SPEC must resolve to exactly one installed Spack package (qualify
    with /<hash> if needed).  The resulting .conda file lands in the
    channel directory (--channel, [conda] channel, or ./channel) along
    with updated repodata.json, ready for direct use with pixi.
    """
    _help_if_bare(ctx, spec)
    if not 1 <= compression_level <= conda.MAX_COMPRESSION_LEVEL:
        _fail(f"compression level must be between 1 and {conda.MAX_COMPRESSION_LEVEL}")
    main = ctx.obj
    try:
        results = convert.convert_spec(
            main.spack(), " ".join(spec), main.channel,
            with_deps=deps, force=force, jobs=jobs,
            compression_level=compression_level)
    except AmbiguousSpecError as err:
        # Show the competing builds and their variants so the user can pick.
        click.secho(f"spaxi: {err}", fg="red", err=True)
        try:
            click.echo(main.spack().find_verbose(err.spec).rstrip(), err=True)
        except SpackError:
            pass
        sys.exit(1)
    except (SpackError, CondaBuildError, FlagCollisionError) as err:
        _fail(str(err))
    for res in results:
        where = res.path if res.path else ""
        note = f" ({res.note})" if res.note else ""
        click.echo(f"{res.name}@{res.version}/{res.hash[:7]} {where}{note}")
    # Binary prefix relocation can only shrink the embedded Spack prefix, so
    # the channel's usable install prefix is capped by its tightest package.
    limits = [(r.prefix_limit, r.name) for r in results
              if r.prefix_limit is not None]
    if limits:
        limit, tightest = min(limits)
        click.echo(
            f"note: some packages embed the Spack prefix in binaries; install "
            f"this channel only into an environment whose prefix is at most "
            f"{limit} characters (tightest: {tightest})."
        )


@cli.command("add-spec")
@click.argument("spec", nargs=-1)
@click.option("--exact", is_flag=True,
              help="Pin the exact concretized Spack hash as a hash:<hash> flag.")
@click.option("-c", "--config", "config_file", type=click.Path(path_type=Path),
              default=None,
              help="pixi.toml to create or update (default: ./pixi.toml).")
@click.pass_context
def add_spec_cmd(ctx, spec, exact, config_file):
    """Add a Spack SPEC to a pixi.toml as a flag-based dependency.

    SPEC is concretized with Spack and its variants are rendered as conda
    'flags' (e.g. +programs -> programs:true, compression=zlib ->
    compression:zlib).  With --exact the concretized DAG hash is added as a
    hash:<hash> flag, pinning the whole transitive closure.
    """
    _help_if_bare(ctx, spec)
    main = ctx.obj
    path = config_file or Path("pixi.toml")
    try:
        result = addspec.add_spec(main.spack(), " ".join(spec), path, exact=exact)
    except (SpackError, FlagCollisionError) as err:
        _fail(str(err))
    verb = "created" if result.created else "updated"
    click.echo(f"{verb} {result.path}")
    click.echo(f"  {result.name} {{ flags = {result.flags} }}")
    if result.created:
        click.echo("  note: fill in [workspace] channels and platforms before "
                   "'pixi install'")


# ----------------------------------------------------------------------
# Strategy 2: pixi-like project commands

@cli.command()
@click.argument("directory", required=False,
                type=click.Path(path_type=Path))
@click.option("--name", default=None, help="Project name (default: directory name).")
@click.pass_context
def init(ctx, directory, name):
    """Create a new spaxi project in DIRECTORY (default: current)."""
    proj = Project(directory or Path.cwd())
    try:
        proj.init(name=name)
    except ProjectError as err:
        _fail(str(err))
    click.echo(f"initialized spaxi project at {proj.manifest_path}")


def _project_and_spack(main):
    proj = find_project()
    return proj, main.spack(env_dir=proj.env_dir)


@cli.command()
@click.pass_context
def install(ctx):
    """Install all dependencies from spaxi.toml into the environment."""
    try:
        proj, spack = _project_and_spack(ctx.obj)
        proj.install(spack)
    except (ProjectError, SpackError) as err:
        _fail(str(err))
    click.echo(f"environment view at {proj.view_dir}")


@cli.command()
@click.argument("specs", nargs=-1)
@click.pass_context
def add(ctx, specs):
    """Add Spack SPECS to the project and install them."""
    _help_if_bare(ctx, specs)
    try:
        proj, spack = _project_and_spack(ctx.obj)
        proj.add(spack, list(specs))
    except (ProjectError, SpackError) as err:
        _fail(str(err))
    click.echo(f"added: {' '.join(specs)}")


@cli.command()
@click.argument("names", nargs=-1)
@click.pass_context
def remove(ctx, names):
    """Remove packages NAMES from the project and its environment."""
    _help_if_bare(ctx, names)
    try:
        proj, spack = _project_and_spack(ctx.obj)
        proj.remove(spack, list(names))
    except (ProjectError, SpackError) as err:
        _fail(str(err))
    click.echo(f"removed: {' '.join(names)}")


@cli.command("list")
@click.pass_context
def list_cmd(ctx):
    """List packages installed in the project environment."""
    try:
        proj, spack = _project_and_spack(ctx.obj)
        nodes = proj.installed(spack)
    except (ProjectError, SpackError) as err:
        _fail(str(err))
    if not nodes:
        click.echo("nothing installed; run 'spaxi install'")
        return
    width = max(len(n["name"]) for n in nodes) + 2
    for node in sorted(nodes, key=lambda n: n["name"]):
        click.echo(f"{node['name']:<{width}}{node['version']:<12}/{node['hash'][:7]}")


@cli.command()
@click.pass_context
def tree(ctx):
    """Show the dependency tree of the project environment."""
    try:
        proj, spack = _project_and_spack(ctx.obj)
        proj.load()
        out = spack.run("find", "--deps", "--no-groups")
    except (ProjectError, SpackError) as err:
        _fail(str(err))
    click.echo(out.rstrip())


@cli.command()
@click.pass_context
def info(ctx):
    """Show information about the current project."""
    try:
        proj = find_project()
        data = proj.info()
    except ProjectError as err:
        _fail(str(err))
    click.echo(f"name        : {data['name']}")
    click.echo(f"manifest    : {data['manifest']}")
    click.echo(f"environment : {data['environment']}")
    click.echo(f"view        : {data['view']}")
    deps = data["dependencies"]
    click.echo(f"dependencies: {len(deps)}")
    for name, constraint in deps.items():
        click.echo(f"  {name}{constraint}")


def main():
    cli(prog_name="spaxi")


if __name__ == "__main__":
    main()
