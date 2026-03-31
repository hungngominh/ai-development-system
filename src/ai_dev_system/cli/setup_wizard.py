"""Interactive setup wizard: ai-dev setup."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


CONFIG_DIR = Path.home() / ".ai-dev-system"
ENV_FILE = CONFIG_DIR / ".env"
CLAUDE_COMMANDS_DIR = Path.home() / ".claude" / "commands"


def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    """Prompt user for input. Returns default if empty."""
    suffix = f" [{default}]" if default else ""
    try:
        if secret:
            import getpass
            value = getpass.getpass(f"{label}{suffix}: ").strip()
        else:
            value = input(f"{label}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.")
        sys.exit(1)
    return value or default


def _collect_config() -> dict[str, str]:
    """Collect config values interactively."""
    print("\n=== AI Dev System Setup ===\n")

    existing = {}
    if ENV_FILE.exists():
        print(f"(Found existing config at {ENV_FILE})")
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
        print()

    config = {}

    # Database
    config["DATABASE_URL"] = _prompt(
        "DATABASE_URL (postgresql://user:pass@host/db)",
        existing.get("DATABASE_URL", ""),
    )

    # Storage
    default_storage = existing.get("STORAGE_ROOT", str(CONFIG_DIR / "storage"))
    config["STORAGE_ROOT"] = _prompt("STORAGE_ROOT", default_storage)

    # LLM Provider
    print("\nLLM Provider:")
    print("  1. anthropic")
    print("  2. openai")
    print("  3. azure")
    choice = _prompt("Choose [1/2/3]", existing.get("_provider_choice", "1"))
    provider_map = {"1": "anthropic", "2": "openai", "3": "azure"}
    provider = provider_map.get(choice, choice)
    if provider not in ("anthropic", "openai", "azure"):
        provider = "anthropic"
    config["LLM_PROVIDER"] = provider

    # Model
    model_defaults = {"anthropic": "claude-sonnet-4-6", "openai": "gpt-4o", "azure": ""}
    config["LLM_MODEL"] = _prompt(
        "LLM_MODEL" + (" (deployment name)" if provider == "azure" else ""),
        existing.get("LLM_MODEL", model_defaults.get(provider, "")),
    )

    # API keys per provider
    if provider == "anthropic":
        config["ANTHROPIC_API_KEY"] = _prompt(
            "ANTHROPIC_API_KEY",
            existing.get("ANTHROPIC_API_KEY", ""),
            secret=True,
        )
    elif provider == "openai":
        config["OPENAI_API_KEY"] = _prompt(
            "OPENAI_API_KEY",
            existing.get("OPENAI_API_KEY", ""),
            secret=True,
        )
    elif provider == "azure":
        config["AZURE_OPENAI_API_KEY"] = _prompt(
            "AZURE_OPENAI_API_KEY",
            existing.get("AZURE_OPENAI_API_KEY", ""),
            secret=True,
        )
        config["AZURE_OPENAI_ENDPOINT"] = _prompt(
            "AZURE_OPENAI_ENDPOINT (https://xxx.openai.azure.com/)",
            existing.get("AZURE_OPENAI_ENDPOINT", ""),
        )
        config["AZURE_OPENAI_API_VERSION"] = _prompt(
            "AZURE_OPENAI_API_VERSION",
            existing.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )

    # Stub mode
    stub = _prompt("Dung stub LLM? (khong goi API that) [y/N]", "N")
    if stub.lower() in ("y", "yes"):
        config["AI_DEV_STUB_LLM"] = "1"

    return config


def _write_env(config: dict[str, str]) -> None:
    """Write .env file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in config.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nConfig saved to {ENV_FILE}")


def _run_migrations(database_url: str) -> bool:
    """Apply DB migrations. Returns True on success."""
    print("\nApplying database migrations...")

    # Find SQL files relative to this package
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    sql_files = [
        repo_root / "docs" / "schema" / "control-layer-schema.sql",
        repo_root / "docs" / "schema" / "migrations" / "v2-execution-runner.sql",
        repo_root / "docs" / "schema" / "migrations" / "v3-debate-engine.sql",
        repo_root / "docs" / "schema" / "migrations" / "v4-verification.sql",
    ]

    try:
        import psycopg
        conn = psycopg.connect(database_url, autocommit=True)
        for sql_file in sql_files:
            if not sql_file.exists():
                print(f"  SKIP {sql_file.name} (not found)")
                continue
            conn.execute(sql_file.read_text())
            print(f"  OK   {sql_file.name}")
        conn.close()
        print("Database migrations complete.")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        print("Database migration failed. You can re-run 'ai-dev setup' to retry.")
        return False


def _install_skills() -> None:
    """Copy skill files to ~/.claude/commands/ for global access."""
    print("\nInstalling Claude Code skills...")

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    skills_dir = repo_root / "skills"

    skill_files = ["start-project.md", "review-debate.md", "review-verification.md"]

    CLAUDE_COMMANDS_DIR.mkdir(parents=True, exist_ok=True)

    for fname in skill_files:
        src = skills_dir / fname
        dst = CLAUDE_COMMANDS_DIR / fname
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  OK   {fname} -> {dst}")
        else:
            print(f"  SKIP {fname} (not found)")

    print("Skills installed. /start-project, /review-debate, /review-verification now work globally.")


def _install_package() -> bool:
    """Install the ai-dev-system package in editable mode."""
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    print("Installing ai-dev-system package...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", f"{repo_root}[dev]"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("  OK   package installed")
        return True
    else:
        # Show last 3 lines of error
        err_lines = result.stderr.strip().splitlines()[-3:]
        for line in err_lines:
            print(f"  {line}")
        print("Package install failed. Check Python version (need 3.12 or 3.13).")
        return False


def run_setup() -> None:
    """Main setup entry point."""
    # Step 0: Install package itself
    _install_package()

    config = _collect_config()
    _write_env(config)

    # Load into current process env so migrations can use it
    for k, v in config.items():
        os.environ[k] = v

    # Create storage directory
    storage = Path(config["STORAGE_ROOT"])
    storage.mkdir(parents=True, exist_ok=True)
    print(f"Storage directory: {storage}")

    # Run DB migrations
    _run_migrations(config["DATABASE_URL"])

    # Install global skills
    _install_skills()

    print("\n=== Setup complete ===")
    print("Dung 'ai-dev start --project-name NAME --idea IDEA' de bat dau.")
    print("Hoac mo Claude Code va go /start-project")
