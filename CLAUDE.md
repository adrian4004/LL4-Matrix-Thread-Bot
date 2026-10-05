# LL4 Matrix Thread Bot — Project Instructions

A rule-based Matrix bot (no LLM) that opens a thread on every new root message in the rooms it is invited to. Public repo `adrian4004/LL4-Matrix-Thread-Bot`, AGPL-3.0, deployed via Coolify. Spun out of AdrianI (`~/AI-Second-Brain/second-brain-starter`, `.claude/chat/`) on 2026-10-05.

**Source of truth:** `.agent/plans/thread-bot-mvp.md` — behaviour rules and Ludwig's decisions. Change the plan first, then the code.

## Rules

- **Public repo: no secrets, no real hostnames, user ids or room ids.** Config comes only from environment variables (set in Coolify); `.env` is gitignored, `.env.example` holds names only. Use `example.org` placeholders.
- **No LLM, no AI calls, no extra services.** Pure rules against the Matrix Client-Server API.
- **Lean:** one module `bot.py`, one runtime dependency (`httpx`). A new dependency or a second module needs a reason in the PR.
- **Never log message bodies or the token** — room and event ids only.
- **Matrix conformity:** thread replies carry `rel_type: m.thread` + `is_falling_back` + `m.in_reply_to`; bot messages are `m.notice`; any markup is sent as `body` **and** `format` + `formatted_body` (FluffyChat/Element render nothing otherwise).
- **Docker conformity:** non-root (UID 10001), pinned base image, no secrets in image layers, read-only root fs, `cap_drop: ALL`.
- Tests cover every rule; a test that proves something is *not* done must fail against a wrong implementation.

## Asking Ludwig

One question per turn via `AskUserQuestion` with clickable options, recommended option first with a one-line why. Explain in plain German first, technical names only as supporting detail.

## Archon first

Before any task check `archon workflow list` for a fitting workflow; default is the sdlc pack in `.archon/workflows/sdlc/` (`archon-plan`, `archon-deliver`, `archon-review`, `archon-validate`, `archon-ship`). If none fits, say so before working directly. Never `archon-piv-loop` or `defaults/legacy/`. Resume runs (`archon workflow runs` → `archon workflow resume <id>`), never restart. Model tiers are pinned in `.archon/config.yaml`. Hand-spawned agents pass `model` explicitly (`sonnet` / `haiku`).

## Commands

- Tests: `uv run pytest`
- Run locally: `cp .env.example .env` → fill in → `docker compose up --build`
- Build image: `docker build -t ll4-matrix-thread-bot .`
- Commit working states; on `main`, branch first.
