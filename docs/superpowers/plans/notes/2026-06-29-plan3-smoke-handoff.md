# Plan 3 — Task 12 live Telegram smoke (OPERATOR-RUN handoff)

**Status:** NOT yet run. Plan 3 is merged to master @ `3a686cc` (1702 passed). This is the
one step the assistant cannot run — it needs a real BotFather token and your Telegram chat id.

## What you're verifying
The `ai-dev gateway` daemon polls Telegram, routes your message to the conversational
Assistant (the same one behind `ai-dev assistant`), and replies in-chat — reply-only,
single-user, allowlist-gated.

## One-time setup

1. **Create a bot** — in Telegram, message **@BotFather** → `/newbot` → follow prompts →
   copy the **HTTP API token** it gives you (looks like `123456789:AAH...`).

2. **Get your numeric chat id** — message **@userinfobot** (or @RawDataBot) in Telegram;
   it replies with your `id` (an integer, e.g. `987654321`). This is the allowlist entry.
   The allowlist is DENY-ALL when empty, so this step is required — without it the daemon
   ignores every message by design.

3. **Set the two env vars.** Put them in the project `.env` (or `~/.ai-dev-system/.env`):
   ```
   AI_DEV_TELEGRAM_TOKEN=123456789:AAH...your-token...
   AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS=987654321
   ```
   (Multiple ids: comma- or space-separated. Keep `LLM_PROVIDER=claude_code` and an empty
   `ANTHROPIC_API_KEY=` so it runs on your Max subscription at $0 API.)

## Run the smoke

A single-batch poll (recommended first run — polls once then exits):
```
ai-dev gateway --once
```
If `ai-dev` isn't on PATH, either `pip install -e .` first, or run:
```
python -c "import sys; sys.argv=['ai-dev','gateway','--once']; from ai_dev_system.cli.main import main; main()"
```

Sequence:
1. Before running, send your bot a message in Telegram (e.g. "xin chào, bạn là ai?").
2. Run `ai-dev gateway --once`. It long-polls once, routes your queued message to the
   Assistant, and sends the reply back to your chat.
3. Check Telegram for the reply.

To run it continuously (Ctrl-C to stop — that's a graceful exit, writes the clean-shutdown
marker; a crash leaves no marker so the next start flags resume):
```
ai-dev gateway
```

## Pass criteria
- [ ] Your message gets a coherent reply in Telegram (Vietnamese is fine — stdout is forced UTF-8).
- [ ] A message from a chat id NOT in the allowlist gets NO reply (deny-all works). Optional:
      have someone else (or a second account) message the bot and confirm silence.
- [ ] Multi-turn: send a follow-up that depends on the previous turn; the reply shows it
      remembered (history window from SQLite).
- [ ] After Ctrl-C and a restart, it resumes without re-replying to already-handled messages
      (offset persisted server-side via Telegram's update offset).

## If something's off
- No reply + no error: check the chat id is in `AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS` (deny-all otherwise).
- `TelegramError`: usually a bad/extra-spaced token.
- The daemon survives transient Telegram 5xx/timeouts (returns None → idle backoff), so a brief
  outage won't crash it; a persistent bad token will log `TelegramError` each poll.

## After running
Record the result in a sibling `2026-06-29-plan3-smoke.md` (PASS/FAIL + any bug found),
mirroring the Plan 1/2 smoke notes, and tell the assistant so it can update project memory.
