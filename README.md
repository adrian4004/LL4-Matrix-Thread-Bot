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

**History:** the bot keeps no state and only acts on messages sent while it is running and in the room. Messages written while it was offline, or before it joined a room, do not get a thread.

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

1. **Create a bot user** on your homeserver, e.g. `@threadbot:example.org` (registration, or your server's admin tooling). On Synapse, inside the Synapse container:

   ```sh
   register_new_matrix_user -c /data/homeserver.yaml -u threadbot --no-admin http://localhost:8008
   ```

   It prompts for a password. A non-admin account is enough; the password is only needed once, for step 2.
2. **Get an access token** with a one-time password login:

   ```sh
   curl -s -X POST https://matrix.example.org/_matrix/client/v3/login \
     -H 'Content-Type: application/json' \
     -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"threadbot"},"password":"…","initial_device_display_name":"thread-bot"}'
   ```

   Copy `access_token` from the response and keep it only in Coolify (step 5). Do not log this device out — that invalidates the token.
3. **Create the resource:** *New Resource → Public Repository* (the repository is public, no GitHub App needed), URL of this repository, branch `main`, build pack **Docker Compose**, compose file location `/compose.yaml`.
4. **Ignore the domain.** Coolify assigns an automatic `sslip.io` domain. The bot exposes no port and needs no domain; leave it unused or remove it.
5. **Set the environment variables** `MATRIX_HOMESERVER`, `MATRIX_ACCESS_TOKEN` and `ALLOWED_INVITERS` — **overwrite all three.** Coolify pre-fills each with the error message from `compose.yaml` (literally `set MATRIX_HOMESERVER`, `set MATRIX_ACCESS_TOKEN`, `set ALLOWED_INVITERS`), so a forgotten variable does not stop the deploy: the bot starts with that text as its value. The "preview" copies Coolify creates only matter for preview deployments.
6. **`MATRIX_HOMESERVER` is seen from inside the container.** If the homeserver has a public URL, use it. If it runs on the same host and is only reachable there:
   - `localhost` is the bot's own container, not the host.
   - `host.docker.internal` does not resolve in Coolify Docker Compose deployments.
   - Use the host's Docker bridge gateway (`docker network inspect bridge` → `Gateway`, often `172.17.0.1`) plus the homeserver's published port, e.g. `http://172.17.0.1:8008`.
7. **Deploy and read the logs.** Success: `logged in as @threadbot:example.org`.

   | Log | Cause |
   |---|---|
   | `cannot verify the access token at startup (ConnectError)`, repeating | `MATRIX_HOMESERVER` not reachable from the container (step 6), or still the `set …` placeholder |
   | `access token rejected by the homeserver (HTTP 401)`, bot exits | Invalid `MATRIX_ACCESS_TOKEN` |

8. **Invite the bot** from an account in `ALLOWED_INVITERS`, typing its full Matrix ID (`@threadbot:example.org`) — a client's autocomplete may pick a different user with a shorter ID. Expect `joining <room> (invited by <user>)` in the log; an invite from anyone else logs `rejecting invite …`. Write a message: it gets a `🧵` thread. Messages from before the bot joined stay untouched.

The container exposes no ports (it only connects outward), runs as a non-root user on a read-only filesystem with all capabilities dropped, and reports unhealthy when it has not completed a sync for two minutes.

### Other homeservers

The bot can only be invited into rooms its homeserver federates with; a homeserver that does not federate (e.g. a local test server) cannot reach rooms elsewhere. For a second homeserver, create a bot account *on that server* and deploy a second Coolify resource from this repository with that server's three variables — one bot instance per homeserver.

## Run locally

```sh
cp .env.example .env   # fill in the values
docker compose up --build
```

If the homeserver runs on the same machine, step 6 applies: use the Docker bridge gateway, or attach the container to the homeserver's Docker network with a compose override file kept out of git.

Tests: `uv run pytest`

## License

[AGPL-3.0](LICENSE)
