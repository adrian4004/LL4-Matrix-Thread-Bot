# LL4 Matrix Thread Bot

A small, rule-based Matrix bot: whenever someone writes a new message in the main timeline of a room the bot is in, it opens a thread on that message. No LLM, no AI — just rules, one Python module (`bot.py`) and one dependency (`httpx`).

## What it does

For every new event in a room it has joined:

- **Its own messages** → ignored (no loops).
- **Messages already in a thread, edits, reactions** (anything with a `rel_type`) → ignored.
- **Replies** to another message → ignored; a reply belongs to the conversation it answers.
- **Notices** (`m.notice`, what bots send) → ignored.
- **Everything else** — text, emotes, images, files, video, audio — is a root message: the bot replies in a new thread with `🧵`.
- A text message that is **bold in its entirety** (`**Title**`) gets its title echoed: `🧵 **Title**`. A message that merely contains a bold word is not a title.

The thread reply is an `m.notice` with `rel_type: m.thread`, `is_falling_back` and an `m.in_reply_to` fallback, so older clients show it as a reply.

**Invites:** the bot joins a room only when invited by a user listed in `ALLOWED_INVITERS`; every other invite is rejected.

**Restarts:** the bot keeps no state. Messages written while it was offline do not get a thread.

### Encrypted rooms

The bot has no encryption keys and needs none: Matrix keeps a message's relation (thread, reply, edit) in the unencrypted part of an encrypted event, so all ignore-rules above still work. Limits:

- The bot cannot read encrypted messages, so it always answers with a plain `🧵` (no title) and cannot tell an encrypted notice from other messages.
- Its own `🧵` is sent unencrypted; clients may show a small "not encrypted" marker next to it.

## Configuration

Environment variables only:

| Variable | Required | Meaning |
|---|---|---|
| `MATRIX_HOMESERVER` | yes | Base URL, e.g. `https://matrix.example.org` |
| `MATRIX_ACCESS_TOKEN` | yes | Access token of the bot account (secret) |
| `ALLOWED_INVITERS` | yes | Comma-separated Matrix IDs allowed to invite the bot, e.g. `@alice:example.org,@bob:example.org`. The bot refuses to start when empty. |
| `LOG_LEVEL` | no | Default `INFO` |

The bot logs room ids, event ids and inviter ids — never message contents or the token.

## Deploy on Coolify

1. **Create a bot user** on your homeserver, e.g. `@threadbot:example.org` (registration, or your server's admin tooling).
2. **Get an access token** with a one-time password login:

   ```sh
   curl -s -X POST https://matrix.example.org/_matrix/client/v3/login \
     -H 'Content-Type: application/json' \
     -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"threadbot"},"password":"…","initial_device_display_name":"thread-bot"}'
   ```

   Copy `access_token` from the response. Do not log this device out — that invalidates the token.
3. **Create a resource** in Coolify: *Docker Compose* from this GitHub repository, compose file `compose.yaml`.
4. **Set the environment variables** `MATRIX_HOMESERVER`, `MATRIX_ACCESS_TOKEN` and `ALLOWED_INVITERS` in the resource's environment settings (Coolify lists them from the compose file).
5. **Deploy.** The logs should show `logged in as @threadbot:example.org`. Invite the bot to a room from an allowed account.

The container exposes no ports (it only connects outward), runs as a non-root user on a read-only filesystem with all capabilities dropped, and reports unhealthy when it has not completed a sync for two minutes. An invalid token makes it exit with an error instead of retrying.

## Run locally

```sh
cp .env.example .env   # fill in the values
docker compose up --build
```

Tests: `uv run pytest`

## License

[AGPL-3.0](LICENSE)
