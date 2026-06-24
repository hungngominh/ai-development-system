# Design Spec: CLI Unification (`ai-dev`)

**Date:** 2026-05-23
**Status:** Draft
**Scope:** Restructure `ai-dev` CLI from ad-hoc subcommand list to a coherent command tree with consistent ergonomics, shared options, JSON output mode, and skill-friendly interfaces.

---

## Motivation

Current CLI is growing organically:
- `ai-dev setup` — interactive wizard
- `ai-dev start` — Phase 1a entry
- `ai-dev run` — Phase B execution

After 5 new specs land:
- `ai-dev intake start|resume|show|abort`
- `ai-dev eval run|compare|show|list`
- `ai-dev eval golden init|validate|dryrun`
- `ai-dev gate1 load|parse|finalize`  (Spec D internal)
- (potential) `ai-dev debate start|resume`
- (potential) `ai-dev migrate status`

Without structure:
- Inconsistent flag names (`--run-id` vs `--run_id` vs `-r`)
- Inconsistent output format (some print human, some print JSON, some both)
- No `--help` discoverability across subcommands
- Skills hard to parse CLI output reliably
- New subcommand authors copy-paste wildly

---

## Goals

- 1 unified command tree with consistent verb-noun structure
- Global options (`--run-id`, `--project-id`, `--output`, `--quiet`, `--json`) work everywhere applicable
- `--json` mode produces single-line JSON on stdout, progress on stderr (skill-friendly)
- `--help` works at every level (`ai-dev <noun> --help`, `ai-dev <noun> <verb> --help`)
- Standard exit codes (0 success, 1 user error, 2 system error, 3 config error)
- Auto-completion support (bash/zsh/powershell)
- Plugin pattern: new subcommands register via decorator, no central registry edit

## Non-goals

- **Không** rewrite existing subcommands' core logic — only their CLI surface
- **Không** TUI / curses-based interactive UI (terminal text only)
- **Không** REST API server (CLI only)
- **Không** i18n for help text (English-only commands, Vietnamese in descriptions ok)

---

## Architecture

```
src/ai_dev_system/cli/
├── __init__.py
├── main.py                       # ai-dev entry point
├── core/
│   ├── parser.py                 # argparse helpers, global flags
│   ├── output.py                 # JSON/human renderer, exit codes
│   ├── registry.py               # @command decorator + registration
│   ├── context.py                # CLIContext (config, conn, flags)
│   └── completion.py             # shell completion generator
├── commands/                     # 1 file per noun
│   ├── setup.py
│   ├── intake.py                 # noun: intake; verbs: start|resume|show|abort
│   ├── start.py                  # legacy alias for `intake start`
│   ├── debate.py                 # noun: debate; verbs: start|resume
│   ├── gate.py                   # noun: gate; verbs: review-debate|review-verification
│   ├── eval.py
│   ├── golden.py
│   ├── run.py                    # legacy alias for `phase-b run`
│   ├── phase_b.py                # noun: phase-b; verbs: run|resume|abort
│   ├── migrate.py
│   └── info.py                   # ai-dev info <run-id>
└── docs/
    └── help_overview.md          # rendered by `ai-dev help`
```

---

## Command Tree

```
ai-dev
├── setup                          # interactive config wizard
├── info <run-id>                  # show run status, artifacts, next step
├── intake
│   ├── start --project-name=...
│   ├── resume --run-id=...
│   ├── show --run-id=...
│   └── abort --run-id=...
├── debate
│   ├── start --run-id=...         # trigger Phase 1b after intake
│   └── resume --run-id=...
├── gate
│   ├── review-debate --run-id=...      # Gate 1 CLI (skill backs this)
│   ├── review-graph --run-id=...       # Gate 2 CLI
│   └── review-verification --run-id=... # Gate 3 CLI
├── phase-b
│   ├── run --run-id=...
│   ├── resume --run-id=...
│   └── abort --run-id=...
├── eval
│   ├── run [--idea=ID] [--layer=...] [--mode=stub|real] [--tag=...]
│   ├── compare <tag-a> <tag-b>
│   ├── show <tag>
│   └── list
├── golden
│   ├── init <idea-id>
│   ├── validate <idea-id>
│   └── dryrun <idea-id>
├── migrate
│   ├── status
│   └── classify
└── help [topic]
```

### Verb-noun convention

- **Noun first** (`ai-dev intake start`, not `ai-dev start-intake`)
- Verbs: `start | resume | show | abort | run | compare | list | init | validate | dryrun | status`
- Each noun has a default verb? **No** — explicit required. Reduces foot-gun.

### Legacy aliases (deprecation path)

Per Migration Plan (Spec E), old commands continue to work:

```
ai-dev start --idea "..." --project-name "..."
  → emits DeprecationWarning to stderr
  → internally calls: ai-dev intake start --project-name "..." (no idea arg in v2)
  → at T+12w, removed

ai-dev run --run-id <id>
  → DeprecationWarning
  → calls: ai-dev phase-b run --run-id <id>
```

---

## Global Options

Available to all subcommands (parser-level):

```
--run-id ID                Run UUID. Overrides any positional --run-id.
--project-id ID            Project ID for subcommands that need it.
--json                     Output mode: single-line JSON to stdout, no human text on stdout.
                           Progress/errors → stderr. For skill/script consumption.
--quiet, -q                Suppress non-error output. JSON mode auto-implies this for stdout.
--verbose, -v              Increase verbosity (repeatable: -v, -vv, -vvv).
--config PATH              Override config file (default ~/.ai-dev-system/.env).
--no-color                 Disable ANSI colors. Auto-detected for non-TTY.
--feature KEY=VAL          Override feature flag for this invocation. Repeatable.
--dry-run                  Show what would happen, don't mutate state. For destructive ops.
--help, -h                 Show help.
--version                  Show ai-dev version.
```

Each subcommand can declare which globals it accepts (avoid `--run-id` on `ai-dev setup`).

---

## Output Modes

### Human mode (default)

```
$ ai-dev intake start --project-name "forum"

[1/3] Creating run...
[2/3] Loading template generic_v1...
[3/3] Wizard ready. Resume: /start-project abc-123-def

✅ Intake started. Run ID: abc-123-def
```

- Progress to **stderr**, final result to **stdout**
- Color if TTY, plain if piped
- Final stdout line is parseable result (single line, key=value or summary)

### JSON mode (`--json`)

```
$ ai-dev intake start --project-name "forum" --json

{"status":"ok","run_id":"abc-123-def","next":"intake_resume","cli":"ai-dev intake resume --run-id abc-123-def"}
```

- Single-line JSON to **stdout** (parseable, no logging mixed in)
- Progress / human-readable info to **stderr** (still visible to user but not in stdout)
- On error:
  ```
  {"status":"error","code":1,"message":"...","details":{...}}
  ```

### Stream mode (long-running, future)

For commands that produce ongoing output (eval run with many ideas):
```
$ ai-dev eval run --json
{"event":"start","ideas":8,"mode":"stub"}
{"event":"idea_done","id":"01_internal_forum","metrics":{...}}
{"event":"idea_done","id":"02_data_pipeline","metrics":{...}}
...
{"event":"complete","aggregate":{...},"tag":"abc123"}
```

JSON Lines, one event per line. Skill / script consumers parse incrementally.

---

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | User error (invalid args, run not found, permission) |
| 2 | System error (DB connection, LLM API down, disk full) |
| 3 | Configuration error (missing env var, invalid config file) |
| 4 | Validation error (e.g., golden validate fails) |
| 130 | Interrupted (SIGINT) |

In JSON mode, exit code mirrors `"code"` field in output.

---

## CLIContext

Shared context object passed to every command:

```python
@dataclass
class CLIContext:
    config: Config
    conn: psycopg.Connection | None      # lazy-init, only if subcommand needs DB
    flags: FeatureFlags
    output: OutputRenderer               # json | human
    quiet: bool
    verbose_level: int                   # 0-3
    dry_run: bool

    @classmethod
    def from_args(cls, args: Namespace) -> "CLIContext":
        ...
```

Subcommands receive `ctx: CLIContext` as first arg:

```python
@command(noun="intake", verb="start")
def intake_start(ctx: CLIContext, project_name: str, ...):
    if ctx.dry_run:
        ctx.output.write({"would_create": project_name})
        return 0
    ...
```

---

## Command Registration

### Decorator pattern

```python
# commands/intake.py
from ai_dev_system.cli.core.registry import command, arg

@command(noun="intake", verb="start", help="Start new intake wizard for project")
@arg("--project-name", required=True, help="Project name (slug-able)")
@arg("--template", default="generic_v1", help="Intake template version")
def intake_start(ctx: CLIContext, project_name: str, template: str):
    """Start a new intake wizard session."""
    ...
    return 0  # exit code

@command(noun="intake", verb="resume", help="Resume in-progress intake wizard")
@arg("--run-id", required=True)
def intake_resume(ctx: CLIContext, run_id: str):
    ...
```

### Auto-registration

`main.py` discovers all `@command` decorators by importing all modules in `commands/`. No central registration list.

### Plugin path (future)
External packages can register commands by entry_points:

```toml
[project.entry-points."ai_dev_system.cli_commands"]
custom_noun = "my_pkg.commands:register"
```

`my_pkg.commands.register(parser)` adds subparser. Out of scope for v1.

---

## Help System

### `ai-dev help`
Show top-level summary + link to docs.

### `ai-dev <noun> --help`
List all verbs under that noun + their short help.

### `ai-dev <noun> <verb> --help`
Full subcommand help: args, examples, related commands.

### `ai-dev help <topic>`
Pre-written long-form help pages (markdown in `cli/docs/`):
- `ai-dev help intake` → tutorial
- `ai-dev help workflow` → end-to-end how-to
- `ai-dev help concepts` → terms (run, project, brief, decision...)

Renders markdown via `rich` (already a dep candidate) or plain text fallback.

### Examples in help

Every subcommand help includes ≥1 example:

```
$ ai-dev intake start --help

Usage: ai-dev intake start [OPTIONS]

Start a new intake wizard session for a project.

Required:
  --project-name TEXT       Project name (will be slugified)

Optional:
  --template TEXT           Intake template (default: generic_v1)
  --json                    JSON output mode

Examples:
  Start with default template:
    $ ai-dev intake start --project-name "internal forum"

  Start with JSON output (for scripts):
    $ ai-dev intake start --project-name "forum" --json
    {"status":"ok","run_id":"...","next":"intake_resume"}

Related:
  ai-dev intake resume    Continue an in-progress wizard
  ai-dev intake show      View current state
  ai-dev info             Inspect any run by ID
```

---

## Shell Completion

### Generation

```
$ ai-dev completion bash > ~/.bash_completion.d/ai-dev
$ ai-dev completion zsh > ~/.zsh/_ai-dev
$ ai-dev completion powershell > Documents/PowerShell/Modules/ai-dev/ai-dev.ps1
```

Generator inspects `@command` decorators + `@arg` declarations, emits standard completion script.

### What completes
- Subcommand names (`ai-dev int<TAB>` → `intake`)
- Verb names per noun
- Flag names
- For `--run-id`: query DB for recent run IDs (limit 20)
- For `--tag`: query `.eval_runs/` dir

Run-id / tag completion: only if completion script can call `ai-dev complete-values --kind=run-id`, which queries DB without loading full system. Lazy init.

---

## Backward Compatibility Layer

### Argument compatibility

Old `ai-dev start --idea X --constraints Y --project-name Z` patterns:

```python
@command(noun="start", help="DEPRECATED: use 'ai-dev intake start'")
@arg("--idea", required=False, deprecated=True)
@arg("--constraints", required=False, deprecated=True)
@arg("--project-name", required=True)
def start_legacy(ctx: CLIContext, **kwargs):
    ctx.output.warn("'ai-dev start' is deprecated, use 'ai-dev intake start'")
    if kwargs.get("idea") or kwargs.get("constraints"):
        # Bridge to legacy path: skip intake, write directly to old schema
        return run_legacy_start(ctx, **kwargs)
    return intake_start(ctx, project_name=kwargs["project_name"])
```

Old skill (`/start-project`) continues to call `ai-dev start --idea ... --constraints ...` → bridge handles → eventually skill updated to call intake.

### Output compatibility

If `--json` not specified, output remains a single JSON line at end (existing skill parser still works for `ai-dev start`):

```
[Phase 1a/1b] Running...
{"run_id":"abc","status":"intake_complete","next":"review-debate"}
```

Skill parses last line of stdout (existing logic).

---

## Configuration Discovery

### Order of precedence
1. CLI flag (`--config /path/to/.env`)
2. Env var `AI_DEV_CONFIG`
3. `./.ai-dev.env` (project-local)
4. `~/.ai-dev-system/.env` (user-global)
5. System default (built-in)

### Feature flags
Same order + env vars (`FF_*`) + per-run override (`--feature ...`).

### Config display

```
$ ai-dev info config

Active config:
  Source: ~/.ai-dev-system/.env
  DATABASE_URL: postgresql://...@***:5432/aidev
  STORAGE_ROOT: ~/.ai-dev-system/storage
  LLM Provider: openai (model: gpt-4o)

Active feature flags:
  use_intake_wizard: false (default)
  use_question_pipeline_v2: false (default)
  ...

To change: ai-dev setup
```

---

## Logging

### Log levels (controlled by `-v` count)
- 0 (default): WARN
- 1 (-v): INFO
- 2 (-vv): DEBUG
- 3 (-vvv): TRACE (LLM payloads, SQL queries)

### Log destinations
- Console (stderr): respects level
- File: always TRACE, `~/.ai-dev-system/logs/ai-dev-<date>.log`
- DB events: existing audit trail (unchanged)

### Structured logging
Internal use `structlog` or stdlib `logging` with JSON formatter to file:

```json
{"ts":"2026-05-23T10:00:01","level":"INFO","msg":"intake.started","run_id":"abc","project":"forum"}
```

CLI stderr keeps human format. File log JSON for grep / analysis.

---

## Testing Strategy

### Unit
- `parser.py`: argument parsing edge cases (missing required, bad enum value, repeated `-v`)
- `output.py`: JSON mode strips ANSI, human mode preserves color
- `registry.py`: decorator collects metadata, dispatcher routes correctly
- Each command module: stub `CLIContext`, assert side effects + return code

### Integration
- `tests/integration/test_cli_e2e.py`:
  - `ai-dev info <run>` after intake start
  - `ai-dev intake start --json` produces parseable JSON
  - Legacy `ai-dev start` still works
  - Help output for all commands compiles without error
- `tests/integration/test_completion.py`: generator produces valid bash/zsh/pwsh scripts

### Skill compat
- Run skill `/start-project` end-to-end, verify it still parses `ai-dev start` output
- Run new skill `/intake-resume` (if exists), verify `ai-dev intake resume --json` consumable

---

## Build Order

| Slice | Đầu ra | Test |
|---|---|---|
| **C1** | `core/parser.py`, `core/output.py`, `core/context.py` | unit |
| **C2** | `core/registry.py` decorator + auto-discovery | unit + dummy command |
| **C3** | Migrate `setup` to new framework | regression manual |
| **C4** | Migrate `start` (as legacy alias) + `intake` new commands | regression skill |
| **C5** | Migrate `run` → `phase-b`, add legacy alias | regression skill |
| **C6** | `eval` + `golden` commands | unit + integration |
| **C7** | `info <run-id>` + `gate` commands | integration with golden run |
| **C8** | Help system (markdown rendering, examples) | snapshot tests |
| **C9** | Shell completion generator + lazy completers | manual test 3 shells |
| **C10** | Migration commands + dry-run support | unit |

C1-C5 = unification done, no broken users. C6-C10 = polish.

---

## Cost / Effort Estimate

| Slice | Estimated dev time |
|---|---|
| C1-C2 (core framework) | 1-2 days |
| C3-C5 (migrate existing) | 1 day |
| C6-C7 (new commands wired) | 1 day |
| C8 (help) | 0.5 day |
| C9 (completion) | 1 day |
| C10 (migration commands) | 0.5 day |

Total: ~6 days. Can parallelize C6-C8 with C3-C5 once C1-C2 done.

---

## Open Questions

1. **Library choice:** stdlib `argparse` vs `click` vs `typer` vs `cyclopts`? Recommend **typer** (modern, type-hint driven, supports decorator-style cleanly). Worth the dep cost.

2. **Output renderer:** roll own vs use `rich`? `rich` already a transitive dep via `crewai` likely. Use it for tables + markdown rendering.

3. **Run-id completion DB query:** completion script calling DB on every TAB feels heavy. Cache run IDs in `~/.ai-dev-system/recent_runs.txt` updated on each command. Refresh every N seconds.

4. **Aliases for ergonomics:** `ai-dev g` for `ai-dev gate`, `ai-dev e` for `ai-dev eval`? Conservative: don't ship aliases until users ask.

5. **Async commands:** `ai-dev eval run` takes minutes. Block CLI vs background mode? Recommend block default + `--background` flag returning job id later.

6. **Config validation:** `ai-dev setup` runs interactive wizard. Should `ai-dev info config` also detect invalid config and offer fix? Yes — call validator on every command, warn if bad.

7. **Plugin / extensibility:** entry_points pattern is overkill for solo project. Defer until external contributors emerge.

---

## Out of Scope

- TUI / interactive dashboard (`ai-dev ui`)
- Web-based admin panel
- Multi-user CLI (no auth concepts)
- Telemetry / phone-home analytics
- Auto-update mechanism
- Embedded REPL (`ai-dev shell`)
- Localized help (English only)
