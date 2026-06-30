# Plan 7 — Per-project Telegram bots (multi-bot gateway) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let the operator run **one Telegram bot per project** (each its own BotFather token + allowlist) so projects are managed in separate chats. A run started from bot A's chat sends all its gate/terminal pushes + replies back through bot A. *(This replaces the dropped Discord plan.)*

**Architecture:** The gateway's whole routing layer already keys on the platform **`name`** as the "surface" (`TelegramAdapter` emits `Inbound(surface=self.name)` and replies via its own token; `AssistantFactory.for_chat(surface, chat_id)`, `run_links(run_id→surface, chat_id)`, and the notifier `platforms_by_name.get(link.surface)` all route by it). So per-project bots = (1) make `TelegramAdapter.name` a per-instance value, (2) config a **list** of bots, (3) `PlatformRegistry.from_config` builds one adapter per bot. The daemon, notifier, and run-links need **no change** — they already iterate `self._platforms` / route by surface.

**Tech Stack:** Python stdlib (`json`), the existing gateway (`gateway/platforms/telegram.py`, `gateway/registry.py`, `cli/commands/gateway.py`), `config.py`, pytest.

## Global Constraints

- **Back-compat is mandatory:** the existing single-bot setup (`AI_DEV_TELEGRAM_TOKEN` + `AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS`) must keep working unchanged, including the surface name staying exactly `"telegram"` (existing run_links rows, the live Monitor bridge, and all current gateway tests depend on it).
- **Binding model:** "the bot you talk to IS that project's channel" — the surface (= bot label) namespaces everything. No project_id→bot table.
- **stdlib only** (`json` for the bot list); no new dependency.
- **Routing layer untouched:** do NOT modify `gateway/daemon.py`, `gateway/notifier.py`, `assistant/run_links.py`, or `assistant/factory.py` — they already work per-surface. (If a change there seems needed, STOP and reconsider — it almost certainly isn't.)
- **Allowlist per bot:** each bot enforces its OWN `allowed_chat_ids` (deny-all on empty, as today).
- **README test-count chore**; **UTF-8**.

## Caveat (documented, not fixed here)
The daemon polls platforms **sequentially**; with long-poll and N bots a loop iteration can take up to N×poll_timeout worst-case. For a handful of projects this is fine. Concurrent polling is a future enhancement — note it; do not build it in this plan.

---

### Task 1: Config — `TelegramBotConfig` + `Config.telegram_bots` (with single-token back-compat)

**Files:**
- Modify: `src/ai_dev_system/config.py`
- Test: `tests/unit/test_config_telegram_bots.py`

**Interfaces:**
- `TelegramBotConfig` dataclass (frozen): `label: str`, `token: str`, `allowed_chat_ids: tuple[int, ...]`.
- `Config.telegram_bots: tuple[TelegramBotConfig, ...]` — parsed from `AI_DEV_TELEGRAM_BOTS` (a JSON list of `{"label","token","chat_ids":[int]}`). If that env is empty/absent/malformed AND `AI_DEV_TELEGRAM_TOKEN` is set, synthesize ONE bot `TelegramBotConfig(label="telegram", token=<AI_DEV_TELEGRAM_TOKEN>, allowed_chat_ids=<AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS>)`. Keep the existing `telegram_token` / `telegram_allowed_chat_ids` fields unchanged (other readers/tests use them).

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_config_telegram_bots.py`:

```python
from __future__ import annotations

import json

import pytest

from ai_dev_system.config import Config, TelegramBotConfig


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for k in ("AI_DEV_TELEGRAM_BOTS", "AI_DEV_TELEGRAM_TOKEN", "AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS"):
        monkeypatch.delenv(k, raising=False)


def test_no_telegram_config_no_bots(monkeypatch):
    assert Config.from_env().telegram_bots == ()


def test_single_token_backcompat_one_bot(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_TOKEN", "TOK1")
    monkeypatch.setenv("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", "111, 222")
    bots = Config.from_env().telegram_bots
    assert bots == (TelegramBotConfig(label="telegram", token="TOK1",
                                      allowed_chat_ids=(111, 222)),)


def test_multi_bot_json(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS", json.dumps([
        {"label": "projA", "token": "TA", "chat_ids": [111]},
        {"label": "projB", "token": "TB", "chat_ids": [222, 333]},
    ]))
    bots = Config.from_env().telegram_bots
    assert [b.label for b in bots] == ["projA", "projB"]
    assert bots[0] == TelegramBotConfig(label="projA", token="TA", allowed_chat_ids=(111,))
    assert bots[1].allowed_chat_ids == (222, 333)


def test_malformed_bots_json_falls_back_to_single_token(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS", "{ not json")
    monkeypatch.setenv("AI_DEV_TELEGRAM_TOKEN", "TOK1")
    bots = Config.from_env().telegram_bots
    assert len(bots) == 1 and bots[0].label == "telegram" and bots[0].token == "TOK1"


def test_bots_json_takes_precedence_over_single_token(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_TOKEN", "LEGACY")
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS", json.dumps([{"label": "p", "token": "T", "chat_ids": []}]))
    bots = Config.from_env().telegram_bots
    assert [b.label for b in bots] == ["p"]


def test_entry_missing_token_or_label_skipped(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS", json.dumps([
        {"label": "ok", "token": "T", "chat_ids": [1]},
        {"label": "", "token": "T2"},          # no label → skipped
        {"label": "x"},                          # no token → skipped
    ]))
    bots = Config.from_env().telegram_bots
    assert [b.label for b in bots] == ["ok"]
```

- [ ] **Step 2: Run → RED** (`ImportError: TelegramBotConfig` / attribute missing).

- [ ] **Step 3: Implement** in `src/ai_dev_system/config.py`:
  - Add near the top (with the other imports): `import json`, and `from dataclasses import dataclass` (already present for Config).
  - Add the dataclass (module level, before `Config`):
```python
@dataclass(frozen=True)
class TelegramBotConfig:
    label: str
    token: str
    allowed_chat_ids: tuple[int, ...] = ()
```
  - Add field to `Config`: `telegram_bots: tuple["TelegramBotConfig", ...] = ()`.
  - In `from_env`, after the existing `_tg_token` / `_tg_ids` parsing (lines ~55-57), add:
```python
        _bots_raw = os.environ.get("AI_DEV_TELEGRAM_BOTS", "").strip()
        _bots: list[TelegramBotConfig] = []
        if _bots_raw:
            try:
                for b in json.loads(_bots_raw):
                    label = str(b.get("label") or "").strip()
                    token = str(b.get("token") or "").strip()
                    ids = tuple(int(x) for x in (b.get("chat_ids") or []))
                    if label and token:
                        _bots.append(TelegramBotConfig(label=label, token=token, allowed_chat_ids=ids))
            except Exception:  # noqa: BLE001 - malformed JSON → fall back to single-token
                _bots = []
        if not _bots and _tg_token:
            _bots.append(TelegramBotConfig(label="telegram", token=_tg_token,
                                           allowed_chat_ids=_tg_ids))
```
  - Pass `telegram_bots=tuple(_bots)` into the `Config(...)` constructor call.

- [ ] **Step 4: Run → GREEN** (6 passed). **Step 5:** README bump; full suite; commit:
```bash
git add src/ai_dev_system/config.py tests/unit/test_config_telegram_bots.py README.md
git commit -m "feat(config): AI_DEV_TELEGRAM_BOTS multi-bot config (single-token back-compat)"
```

---

### Task 2: `TelegramAdapter.name` becomes a per-instance value

**Files:**
- Modify: `src/ai_dev_system/gateway/platforms/telegram.py`
- Test: `tests/unit/gateway/test_telegram_adapter.py` (extend)

**Interface:** `TelegramAdapter(*, token, allowed_chat_ids, name="telegram", transport=None, sender=None)`. `self.name = name`; `poll()` emits `Inbound(surface=self.name, ...)` (already uses `self.name`); `reply()` uses `self._token` (already). Default `"telegram"` preserves back-compat.

- [ ] **Step 1: Write failing tests** — extend `tests/unit/gateway/test_telegram_adapter.py`:
  - default name is `"telegram"` (no `name=` passed).
  - `TelegramAdapter(name="projA", token="T", allowed_chat_ids=(1,))` → `adapter.name == "projA"`, and an inbound it produces has `surface == "projA"` (drive `poll` with a canned transport returning one update from chat 1; assert the `Inbound.surface`).
  - two adapters with names `"projA"` / `"projB"` produce inbounds with the respective surfaces.
- [ ] **Step 2: RED. Step 3: Implement** — change the class so `name` is set in `__init__` (param default `"telegram"`); remove or keep the class-level `name = "telegram"` (instance assignment wins; cleanest is to set `self.name = name` in `__init__`). Everything else unchanged.
- [ ] **Step 4: GREEN.** **Step 5:** README bump; full suite (existing telegram_adapter tests must stay green — surface defaults to `"telegram"`); commit:
```bash
git add src/ai_dev_system/gateway/platforms/telegram.py tests/unit/gateway/test_telegram_adapter.py README.md
git commit -m "feat(gateway): TelegramAdapter name is per-instance (enables per-bot surfaces)"
```

---

### Task 3: `PlatformRegistry.from_config` builds one adapter per bot

**Files:**
- Modify: `src/ai_dev_system/gateway/registry.py`
- Test: `tests/unit/gateway/test_registry.py` (extend)

**Interface:** `from_config` iterates `cfg.telegram_bots` and appends one `TelegramAdapter(name=bot.label, token=bot.token, allowed_chat_ids=bot.allowed_chat_ids, transport=transport, sender=sender)` per bot. Back-compat fallback: if `cfg.telegram_bots` is empty but `cfg.telegram_token` is set, append the single legacy adapter (name defaults to `"telegram"`). (With Task 1, `telegram_bots` already contains the synthesized single bot when only the token is set — the fallback just guards a `Config` constructed without `telegram_bots`.)

- [ ] **Step 1: Write failing tests** — extend `tests/unit/gateway/test_registry.py`:
  - a cfg with `telegram_bots=(TelegramBotConfig("projA","TA",(1,)), TelegramBotConfig("projB","TB",(2,)))` → `registry.adapters()` has 2 adapters with names `{"projA","projB"}`, `enabled()` True.
  - a cfg with only `telegram_token="T"` (no telegram_bots / empty) → 1 adapter named `"telegram"` (back-compat).
  - a cfg with neither → 0 adapters, `enabled()` False.
  (Use a small fake cfg object or the real `Config` with monkeypatched env; match the existing test style in the file.)
- [ ] **Step 2: RED. Step 3: Implement:**
```python
    @classmethod
    def from_config(cls, cfg, *, transport=None, sender=None) -> "PlatformRegistry":
        from ai_dev_system.gateway.platforms.telegram import TelegramAdapter
        adapters = []
        bots = getattr(cfg, "telegram_bots", ()) or ()
        for bot in bots:
            adapters.append(TelegramAdapter(
                name=bot.label, token=bot.token, allowed_chat_ids=bot.allowed_chat_ids,
                transport=transport, sender=sender,
            ))
        if not adapters and getattr(cfg, "telegram_token", None):
            adapters.append(TelegramAdapter(
                token=cfg.telegram_token,
                allowed_chat_ids=getattr(cfg, "telegram_allowed_chat_ids", ()),
                transport=transport, sender=sender,
            ))
        return cls(adapters)
```
- [ ] **Step 4: GREEN.** **Step 5:** README bump; full suite (existing `test_registry.py` single-token test must stay green); commit:
```bash
git add src/ai_dev_system/gateway/registry.py tests/unit/gateway/test_registry.py README.md
git commit -m "feat(gateway): PlatformRegistry builds one Telegram adapter per configured bot"
```

---

### Task 4: Multi-bot routing integration test (start on bot A → notify back via bot A, not bot B)

**Files:**
- Test: `tests/unit/gateway/test_multibot_routing.py`

**Goal:** prove end-to-end (via fakes) that two bots route independently and the notifier pushes a run back through the correct bot.

- [ ] **Step 1: Write the test** — build a `PlatformRegistry.from_config` (or two `TelegramAdapter`s directly) for two bots `A`/`B` with distinct injected `sender` recorders + canned transports. Build `platforms_by_name = {p.name: p for p in registry.adapters()}` (mirroring `build_gateway`). Seed a `run_links` row linking a run to surface `"A"` and a `runs` row at `PAUSED_AT_GATE_1`; run `RunStatusWatcher(conn_factory, link_store, platforms_by_name).check_once()`; assert bot A's sender recorded exactly one push and bot B's sender recorded **zero**. (Reuse the notifier test harness + RunLinkStore + file_db_url + apply_schema.) Optionally also assert two inbounds (one per bot transport) carry surfaces `"A"` / `"B"`.
- [ ] **Step 2: RED→GREEN** (this is mostly assembly of existing pieces; if it passes first try, that's the point — the routing already works). **Step 3:** README bump; full suite; commit:
```bash
git add tests/unit/gateway/test_multibot_routing.py README.md
git commit -m "test(gateway): multi-bot routing — run on bot A notifies via bot A only"
```

---

## Acceptance (whole plan)
- `AI_DEV_TELEGRAM_BOTS` (JSON list) configures N bots; each becomes a `TelegramAdapter` with `name = label`.
- Single-token (`AI_DEV_TELEGRAM_TOKEN`) setup still works, surface still `"telegram"` (back-compat; the live bridge + all existing tests green).
- A run started from bot A's chat links to surface `=A`; its gate/terminal pushes go back through bot A only (Task 4).
- Daemon / notifier / run_links / factory **unchanged**.

## Risk + Self-Review (plan author)
- **Lowest-risk plan in the series** — the routing layer was already surface-keyed; this is config + a per-instance name + a registry loop. Tasks 1-3 each preserve the single-token path explicitly (verified by keeping/extending existing tests). ✓
- **Back-compat is the main hazard:** surface MUST stay `"telegram"` for the legacy single-bot path (run_links, the live Monitor bridge, existing tests). Both the config synth (label `"telegram"`) and the adapter default (`name="telegram"`) guarantee it. ✓
- **No routing-layer edits:** daemon/notifier/run_links/factory untouched — Task 4 proves they already handle N surfaces. ✓
- **Operator setup:** each bot = a separate BotFather token; documented. Sequential-poll latency caveat noted (concurrency deferred). ✓
