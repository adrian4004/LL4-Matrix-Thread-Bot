# Plan: LL4 Matrix Thread Bot — MVP

All decisions below were made by Ludwig on 2026-10-05. Do not reopen them; build exactly this.

## Goal

A Matrix bot that opens a thread on every new message written in the main timeline of a room it is in. Pure rule-based logic — **no LLM, no AI, no external service besides the homeserver**. Lean, compact, readable code. Runs as one Docker container on Coolify.

## Behaviour (the rules)

For every event in a joined room's timeline:

1. **Own events** (`sender == own user id`) → ignore. (Never answer yourself → no loop.)
2. **Event types considered:** `m.room.message` and `m.room.encrypted`. Everything else → ignore.
3. **Any `m.relates_to` with a `rel_type`** (`m.thread`, `m.replace`, `m.annotation`, any other) → ignore. A message already inside a thread needs no thread; edits and reactions are not new messages.
4. **Rich reply** (`m.relates_to.m.in_reply_to` present, no `rel_type`) → ignore. A reply belongs to the conversation it answers and must not open a second thread next to it.
5. **`m.notice`** → ignore. Matrix convention: notices come from bots; answering them risks bot-to-bot loops.
6. **Everything else is a root message → open a thread on it.** This includes text, emotes, images, files, video, audio (Ludwig: "Ja, alles").
7. **Thread text:**
   - Unencrypted `m.text` whose *entire* message is bold (`**Titel**` in `body`, or `formatted_body` that is exactly one `<strong>`/`<b>` element) → `🧵 **Titel**`, sent with `body` = `🧵 **Titel**`, `format` = `org.matrix.custom.html`, `formatted_body` = `🧵 <strong>Titel</strong>` (title HTML-escaped). A message that merely *contains* a bold word is not a title.
   - Everything else → plain `🧵` (body only, no formatted_body).
8. **Encrypted rooms (`m.room.encrypted`)**: the bot has no keys and needs none. Per the Matrix spec, `m.relates_to` stays in the *cleartext* part of an `m.room.encrypted` event, so rules 3–4 work unchanged on the encrypted event's content. The body is unreadable, so rule 5 and the title rule do not apply → always `🧵`. The bot's own `🧵` is sent unencrypted (clients may show a small "not encrypted" shield; accepted).

### How the thread reply is sent (spec-conform)

`PUT /_matrix/client/v3/rooms/{roomId}/send/m.room.message/{txnId}` with

```json
{
  "msgtype": "m.notice",
  "body": "🧵",
  "m.relates_to": {
    "rel_type": "m.thread",
    "event_id": "<root event id>",
    "is_falling_back": true,
    "m.in_reply_to": { "event_id": "<root event id>" }
  }
}
```

- `msgtype: m.notice` — the spec's type for automated messages.
- `txnId` derived deterministically from the root event id (e.g. a short hash) → a retried send is deduplicated by the homeserver instead of producing a second thread reply.
- Always send `body`; add `format` + `formatted_body` whenever there is markup (FluffyChat/Element render nothing otherwise).

### Rooms: invite + allowlist

- On invite (`rooms.invite` in `/sync`): read the `m.room.member` invite event for the bot's own user id; its `sender` is the inviter.
  - Inviter in `ALLOWED_INVITERS` → `POST /join/{roomId}`.
  - Otherwise → `POST /rooms/{roomId}/leave` (reject the invite). Log room id + inviter only.
- `ALLOWED_INVITERS`: comma-separated full Matrix IDs (`@ludwig:example.org,@anna:example.org`). Empty or unset → refuse to start with a clear error (never silently accept everyone).

### Restarts: ignore history

No persistent state, no volume. On start, do one `/sync` and **discard its timeline** (keep only `next_batch` and process its invites). Only events from later syncs are acted on. Messages written while the bot was down get no thread — Ludwig's decision.

### Login

`MATRIX_ACCESS_TOKEN` (no password). On start, `GET /account/whoami` to verify the token and learn the own user id; fail fast with a clear error if it does not work.

## Configuration (environment only)

| Variable | Required | Meaning |
|---|---|---|
| `MATRIX_HOMESERVER` | yes | Base URL, e.g. `https://matrix.example.org` |
| `MATRIX_ACCESS_TOKEN` | yes | Bot account access token (secret — set in Coolify, never committed) |
| `ALLOWED_INVITERS` | yes | Comma-separated MXIDs allowed to invite the bot |
| `LOG_LEVEL` | no | Default `INFO` |

Ship a `.env.example` with the names and empty/placeholder values only.

## Robustness

- Long-poll `/sync?timeout=30000` with a filter that only requests room timeline events of the two types above plus invite state (no presence, no account data, `lazy_load_members`).
- HTTP 429 / `M_LIMIT_EXCEEDED` → wait `retry_after_ms` (Retry-After header or body), then retry.
- Network errors / 5xx in the sync loop → exponential backoff capped at ~60 s, never crash-loop on a transient outage.
- 401 `M_UNKNOWN_TOKEN` → log clearly and exit non-zero (Coolify shows it; retrying cannot fix it).
- SIGTERM/SIGINT → stop cleanly (Docker stop).
- **Privacy:** never log message bodies or the token. Log room ids and event ids only.
- Healthcheck: touch a heartbeat file (e.g. `/tmp/heartbeat`) after every successful sync; the container healthcheck fails if it is older than ~120 s.

## Code shape

- Python 3.12, **one module** `bot.py` (target ≈150–250 lines incl. docstrings), one runtime dependency: `httpx` (async). No Matrix SDK — the bot uses five endpoints and the code should show exactly what it does.
- Keep the decision logic pure and separate from I/O: e.g. `thread_text(event, own_user_id) -> str | None` (None = do nothing) and `should_join(invite_events, own_user_id, allowed) -> bool`. These carry the rules and are what the tests exercise.
- `pyproject.toml` + committed `uv.lock`; dev dependency `pytest` (+ `pytest-asyncio`/`respx` only if a test really needs I/O).
- Tests in `tests/` covering every rule above: own message, thread message, edit, reaction, rich reply, notice, plain text, emote, image, encrypted root, encrypted thread message, encrypted reply, bold title (plain body and formatted_body variants), partially bold text (no title), HTML escaping of the title, invite from allowed / foreign inviter, empty `ALLOWED_INVITERS` refuses to start. Each test must fail against a wrong implementation (populate the wrong value, don't just omit it).

## Docker / Coolify

- `Dockerfile`: multi-stage, `python:3.12-slim` (pinned minor tag), deps via `uv sync --frozen --no-dev`, runtime stage copies only the venv + `bot.py`, non-root user with fixed UID (10001), `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `HEALTHCHECK` on the heartbeat file, exec-form `CMD`. No secrets in `ENV`/`ARG`.
- `.dockerignore`: `.git`, `.env*` (except nothing needed), `.venv`, `tests`, `.agent`, `.archon`, `.claude`, caches.
- `compose.yaml` (what Coolify deploys): one service, `build: .`, `restart: unless-stopped`, env vars via `${MATRIX_HOMESERVER:?}` etc. so Coolify lists them in its UI, `read_only: true`, `tmpfs: /tmp`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, no ports (the bot only connects outward), modest `mem_limit`.
- GitHub Actions workflow `.github/workflows/ci.yml`: `uv run pytest` and `docker build` on push/PR.

## Documentation

- `README.md` (English, public): what it does (the rules in a short list), encrypted-room behaviour and its limits, Coolify deployment step by step (create a bot user on the homeserver, obtain an access token via one-time password login with `curl`, set the env vars, deploy from the GitHub repo with the compose file), local run with `docker compose`, license AGPL-3.0.
- No real hostnames, user ids, room ids or tokens anywhere in the repo — use `example.org` placeholders.

## Acceptance

1. `uv run pytest` green; `docker build .` succeeds; the image runs as UID 10001.
2. `docker compose config` resolves with a filled `.env`.
3. `grep` for tokens/real hostnames in the repo finds nothing.
4. Live (Ludwig, after merge, on Coolify): bot joins on invite from an allowed user and rejects others; root message → `🧵` thread; `**Titel**` → `🧵 **Titel**`; reply inside the thread → nothing; works in an encrypted room with `🧵`; in FluffyChat and Element.
