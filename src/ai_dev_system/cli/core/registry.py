"""Command registry: @command decorator + typer app auto-wiring.

Usage:
    @command(noun="intake", verb="start", help="Start intake wizard")
    def intake_start(ctx: CLIContext, project_name: str = typer.Option(...)):
        ...

The decorator registers the function on a noun-scoped typer sub-app, which is
added to the root app at import time. `main.py` only needs to import command
modules to populate the tree.
"""
from __future__ import annotations

import functools
from typing import Callable

import typer


_root_app: typer.Typer | None = None
_noun_apps: dict[str, typer.Typer] = {}


def get_app() -> typer.Typer:
    """Return the root typer app, lazily created."""
    global _root_app
    if _root_app is None:
        _root_app = typer.Typer(
            name="ai-dev",
            help="AI Development System CLI — orchestrate spec-driven AI workflows.",
            no_args_is_help=True,
            pretty_exceptions_enable=False,  # we render errors ourselves
            rich_markup_mode="rich",
        )
    return _root_app


def _get_noun_app(noun: str, help_text: str | None = None) -> typer.Typer:
    """Get-or-create a sub-app for a noun (e.g., 'intake', 'eval')."""
    if noun in _noun_apps:
        return _noun_apps[noun]
    sub = typer.Typer(name=noun, help=help_text or f"{noun} commands", no_args_is_help=True)
    get_app().add_typer(sub, name=noun)
    _noun_apps[noun] = sub
    return sub


def command(
    *,
    noun: str | None = None,
    verb: str | None = None,
    help: str | None = None,
    deprecated: bool = False,
    noun_help: str | None = None,
) -> Callable:
    """Register a function as a CLI command.

    Args:
        noun: top-level grouping (e.g., 'intake'). If None, command is top-level.
        verb: subcommand name. If None, defaults to function name.
        help: short help text shown in --help.
        deprecated: if True, warn on use.
        noun_help: help text for the noun group (only used first time noun is seen).

    The decorated function receives a CLIContext as first arg, injected by typer
    callback. Remaining args are typer.Option / typer.Argument as usual.
    """

    def decorator(func: Callable) -> Callable:
        cmd_name = verb or func.__name__.replace("_", "-")

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if deprecated:
                # Best-effort deprecation notice — printed via CLIContext if available
                ctx = kwargs.get("ctx")
                if ctx is not None and hasattr(ctx, "output"):
                    ctx.output.warn(
                        f"Command '{noun + ' ' if noun else ''}{cmd_name}' is deprecated."
                    )
            return func(*args, **kwargs)

        # Register
        if noun is None:
            get_app().command(cmd_name, help=help, deprecated=deprecated)(wrapper)
        else:
            sub = _get_noun_app(noun, noun_help)
            sub.command(cmd_name, help=help, deprecated=deprecated)(wrapper)

        return wrapper

    return decorator


def reset_registry() -> None:
    """Reset registry state — for tests only."""
    global _root_app, _noun_apps
    _root_app = None
    _noun_apps = {}
