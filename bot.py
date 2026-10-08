"""LL4 Matrix Thread Bot: opens a thread on every new root message in its rooms.

Rule-based, no LLM. The rules live in the pure functions `thread_reply`,
`should_join` and `after_own_join`; everything below them is the Matrix
Client-Server API plumbing.
Behaviour spec: .agent/plans/thread-bot-mvp.md
"""

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger("thread-bot")

THREAD = "🧵"
HEARTBEAT = Path("/tmp/heartbeat")
SYNC_TIMEOUT_MS = 30_000
MAX_BACKOFF_S = 60
ROOM_EVENT_TYPES = ("m.room.message", "m.room.encrypted")
SYNC_FILTER = json.dumps({
    "presence": {"types": []},
    "account_data": {"types": []},
    "room": {
        "timeline": {"types": [*ROOM_EVENT_TYPES, "m.room.member"]},
        "state": {"lazy_load_members": True},
        "ephemeral": {"types": []},
        "account_data": {"types": []},
    },
})

BOLD_BODY = re.compile(r"\*\*((?:(?!\*\*).)+)\*\*")
BOLD_HTML = re.compile(r"<(strong|b)>([^<]+)</\1>")


# --- Rules (pure) -----------------------------------------------------------

def title(content: dict) -> str | None:
    """The title of an `m.text` message that is bold in its entirety, else None."""
    if content.get("msgtype") != "m.text":
        return None
    if content.get("format") == "org.matrix.custom.html":
        match = BOLD_HTML.fullmatch(str(content.get("formatted_body", "")).strip())
        if match:
            return html.unescape(match[2]).strip() or None
    match = BOLD_BODY.fullmatch(str(content.get("body", "")).strip())
    return (match[1].strip() or None) if match else None


def thread_reply(event: dict, own_user_id: str) -> dict | None:
    """The content of the thread reply to send for `event`, or None to do nothing."""
    content = event.get("content") or {}
    if event.get("sender") == own_user_id or event.get("type") not in ROOM_EVENT_TYPES:
        return None
    if not content:  # redacted
        return None
    relation = content.get("m.relates_to") or {}
    if "rel_type" in relation or "m.in_reply_to" in relation:
        return None
    if content.get("msgtype") == "m.notice":
        return None

    root = event["event_id"]
    reply = {
        "msgtype": "m.notice",
        "body": THREAD,
        "m.relates_to": {
            "rel_type": "m.thread",
            "event_id": root,
            "is_falling_back": True,
            "m.in_reply_to": {"event_id": root},
        },
    }
    if event["type"] == "m.room.message" and (name := title(content)):
        reply["body"] = f"{THREAD} **{name}**"
        reply["format"] = "org.matrix.custom.html"
        reply["formatted_body"] = f"{THREAD} <strong>{html.escape(name)}</strong>"
    return reply


def inviter(invite_state: list[dict], own_user_id: str) -> str | None:
    """Who invited `own_user_id`, read from a room's stripped invite state."""
    for event in invite_state:
        if (
            event.get("type") == "m.room.member"
            and event.get("state_key") == own_user_id
            and (event.get("content") or {}).get("membership") == "invite"
        ):
            return event.get("sender")
    return None


def should_join(invite_state: list[dict], own_user_id: str, allowed: frozenset[str]) -> bool:
    return inviter(invite_state, own_user_id) in allowed


def is_own_join(event: dict, own_user_id: str) -> bool:
    """True for the bot's own join; a profile change is also a `join` member event."""
    previous = ((event.get("unsigned") or {}).get("prev_content") or {}).get("membership")
    return (
        event.get("type") == "m.room.member"
        and event.get("state_key") == own_user_id
        and (event.get("content") or {}).get("membership") == "join"
        and previous != "join"
    )


def after_own_join(timeline: list[dict], own_user_id: str) -> list[dict]:
    """The events of a timeline batch sent after the bot joined; history before it is ignored."""
    for index in range(len(timeline) - 1, -1, -1):
        if is_own_join(timeline[index], own_user_id):
            return timeline[index + 1:]
    return timeline


def txn_id(root_event_id: str) -> str:
    """Deterministic per root event, so the homeserver deduplicates a retried send."""
    return "thread-" + hashlib.sha256(root_event_id.encode()).hexdigest()[:32]


# --- Configuration ----------------------------------------------------------

@dataclass(frozen=True)
class Config:
    homeserver: str
    token: str
    allowed_inviters: frozenset[str]


class ConfigError(Exception):
    pass


def load_config(env: dict[str, str]) -> Config:
    missing = [k for k in ("MATRIX_HOMESERVER", "MATRIX_ACCESS_TOKEN") if not env.get(k, "").strip()]
    if missing:
        raise ConfigError(f"missing environment variable(s): {', '.join(missing)}")
    allowed = frozenset(m.strip() for m in env.get("ALLOWED_INVITERS", "").split(",") if m.strip())
    if not allowed:
        raise ConfigError(
            "ALLOWED_INVITERS is empty: set it to the comma-separated Matrix IDs "
            "allowed to invite the bot (e.g. @ludwig:example.org)"
        )
    return Config(
        homeserver=env["MATRIX_HOMESERVER"].strip().rstrip("/"),
        token=env["MATRIX_ACCESS_TOKEN"].strip(),
        allowed_inviters=allowed,
    )


# --- Matrix I/O -------------------------------------------------------------

class TokenRejected(Exception):
    pass


def retry_after_s(response: httpx.Response) -> float:
    try:
        return float(response.json()["retry_after_ms"]) / 1000
    except (ValueError, KeyError, TypeError):
        pass
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return 5.0


class Matrix:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(
            base_url=config.homeserver + "/_matrix/client/v3",
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=httpx.Timeout(10, read=SYNC_TIMEOUT_MS / 1000 + 30),
            transport=transport,
        )

    async def call(self, method: str, path: str, **kwargs) -> dict:
        while True:
            response = await self.http.request(method, path, **kwargs)
            if response.status_code == 429:
                await asyncio.sleep(retry_after_s(response))
                continue
            if response.status_code == 401:
                raise TokenRejected()
            response.raise_for_status()
            return response.json()

    async def whoami(self) -> str:
        return (await self.call("GET", "/account/whoami"))["user_id"]

    async def sync(self, since: str | None) -> dict:
        params = {"filter": SYNC_FILTER, "timeout": SYNC_TIMEOUT_MS if since else 0}
        if since:
            params["since"] = since
        return await self.call("GET", "/sync", params=params)

    async def send(self, room_id: str, content: dict, txn: str) -> None:
        await self.call("PUT", f"/rooms/{quote(room_id)}/send/m.room.message/{txn}", json=content)

    async def join(self, room_id: str) -> None:
        await self.call("POST", f"/join/{quote(room_id)}", json={})

    async def leave(self, room_id: str) -> None:
        await self.call("POST", f"/rooms/{quote(room_id)}/leave", json={})


async def tolerate_client_error(action, what: str) -> None:
    """Log a 4xx for one action instead of letting it stall the sync loop.

    Network errors and 5xx propagate: the loop then backs off and re-syncs
    from the same token, retrying the action (sends are idempotent via txn_id).
    """
    try:
        await action
    except httpx.HTTPStatusError as error:
        if error.response.status_code >= 500:
            raise
        log.warning("%s failed: HTTP %s", what, error.response.status_code)


# --- Bot --------------------------------------------------------------------

async def handle_invites(matrix: Matrix, sync: dict, own: str, allowed: frozenset[str]) -> None:
    for room_id, room in sync.get("rooms", {}).get("invite", {}).items():
        state = room.get("invite_state", {}).get("events", [])
        if should_join(state, own, allowed):
            log.info("joining %s (invited by %s)", room_id, inviter(state, own))
            await tolerate_client_error(matrix.join(room_id), f"join {room_id}")
        else:
            log.info("rejecting invite to %s from %s", room_id, inviter(state, own))
            await tolerate_client_error(matrix.leave(room_id), f"reject {room_id}")


async def handle_timeline(matrix: Matrix, sync: dict, own: str) -> None:
    for room_id, room in sync.get("rooms", {}).get("join", {}).items():
        for event in after_own_join(room.get("timeline", {}).get("events", []), own):
            reply = thread_reply(event, own)
            if reply is None:
                continue
            log.info("threading %s in %s", event["event_id"], room_id)
            await tolerate_client_error(
                matrix.send(room_id, reply, txn_id(event["event_id"])),
                f"thread on {event['event_id']}",
            )


async def run(matrix: Matrix, allowed_inviters: frozenset[str]) -> None:
    own = await matrix.whoami()
    log.info("logged in as %s", own)

    # History is ignored: the first sync only yields a token and pending invites.
    since, first, backoff = None, True, 1
    while True:
        try:
            sync = await matrix.sync(since)
            await handle_invites(matrix, sync, own, allowed_inviters)
            if not first:
                await handle_timeline(matrix, sync, own)
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            log.warning("sync failed (%s), retrying in %ss", type(error).__name__, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
            continue
        since, first, backoff = sync["next_batch"], False, 1
        HEARTBEAT.touch()


async def main() -> int:
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        config = load_config(dict(os.environ))
        await run(Matrix(config), config.allowed_inviters)
    except ConfigError as error:
        log.error("configuration error: %s", error)
        return 2
    except TokenRejected:
        log.error("access token rejected by the homeserver (HTTP 401); set a valid MATRIX_ACCESS_TOKEN")
        return 1
    except httpx.HTTPError as error:
        log.error("cannot verify the access token at startup (%s)", type(error).__name__)
        return 1
    except asyncio.CancelledError:
        log.info("stopping")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))
