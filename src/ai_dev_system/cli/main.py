"""CLI router: ai-dev <command> [args]."""
from __future__ import annotations

import sys


def main():
    if len(sys.argv) < 2:
        _print_usage()
        sys.exit(1)

    command = sys.argv[1]
    # Remove the subcommand from argv so argparse in submodules sees clean args
    sub_argv = sys.argv[2:]

    if command == "setup":
        from ai_dev_system.cli.setup_wizard import run_setup
        run_setup()

    elif command == "start":
        from ai_dev_system.cli.start_project import main as start_main
        sys.exit(start_main(sub_argv))

    elif command == "run":
        from ai_dev_system.cli.run_phase_b import main as run_main
        sys.exit(run_main(sub_argv))

    elif command in ("help", "--help", "-h"):
        _print_usage()

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        _print_usage()
        sys.exit(1)


def _print_usage():
    print("""ai-dev — AI Development System CLI

Usage:
  ai-dev setup                          Interactive setup (config + DB + skills)
  ai-dev start --project-name NAME      Start a new project (Phase 1a)
    --idea IDEA --constraints CONSTRAINTS
  ai-dev run --run-id RUN_ID            Run Phase B pipeline

Run 'ai-dev setup' first to configure the system.""")


if __name__ == "__main__":
    main()
