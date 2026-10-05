import asyncio
import json

import httpx
import pytest

import bot

BOT = "@threadbot:example.org"
ALICE = "@alice:example.org"
CONFIG = bot.Config("https://matrix.example.org", "syt_placeholder", frozenset({ALICE}))


def root(event_id: str) -> dict:
    return {"type": "m.room.message", "sender": ALICE, "event_id": event_id,
            "content": {"msgtype": "m.text", "body": "hi"}}


def invite(inviter: str) -> dict:
    return {"invite_state": {"events": [
        {"type": "m.room.member", "sender": inviter, "state_key": BOT, "content": {"membership": "invite"}},
    ]}}


SYNCS = [
    # Startup sync: history and a pending invite.
    {"next_batch": "s1", "rooms": {
        "join": {"!old:example.org": {"timeline": {"events": [root("$history")]}}},
        "invite": {"!friend:example.org": invite(ALICE)},
    }},
    httpx.Response(429, json={"errcode": "M_LIMIT_EXCEEDED", "retry_after_ms": 1500}),
    httpx.Response(502),
    httpx.Response(503),
    {"next_batch": "s2", "rooms": {
        "join": {"!old:example.org": {"timeline": {"events": [root("$new")]}}},
        "invite": {"!stranger:example.org": invite("@mallory:example.org")},
    }},
    httpx.Response(401, json={"errcode": "M_UNKNOWN_TOKEN"}),
]


@pytest.fixture
def homeserver(monkeypatch, tmp_path):
    requests: list[httpx.Request] = []
    sleeps: list[float] = []
    syncs = iter(SYNCS)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/account/whoami"):
            return httpx.Response(200, json={"user_id": BOT})
        if request.url.path.endswith("/sync"):
            reply = next(syncs)
            return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)
        return httpx.Response(200, json={})

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(bot, "HEARTBEAT", tmp_path / "heartbeat")
    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)
    matrix = bot.Matrix(CONFIG, transport=httpx.MockTransport(handle))
    with pytest.raises(bot.TokenRejected):
        asyncio.run(bot.run(matrix, CONFIG.allowed_inviters))
    return requests, sleeps, tmp_path / "heartbeat"


def actions(requests: list[httpx.Request]) -> list[tuple[str, str]]:
    return [(r.method, r.url.path.removeprefix("/_matrix/client/v3"))
            for r in requests if not r.url.path.endswith(("/sync", "/whoami"))]


def test_history_is_ignored_and_only_new_roots_are_threaded(homeserver):
    requests, _, _ = homeserver
    sends = [r for r in requests if r.method == "PUT"]
    assert len(sends) == 1
    assert sends[0].url.path.startswith("/_matrix/client/v3/rooms/!old:example.org/send/m.room.message/thread-")
    assert sends[0].url.path.endswith(bot.txn_id("$new"))
    assert json.loads(sends[0].content)["m.relates_to"]["event_id"] == "$new"
    assert sends[0].headers["Authorization"] == "Bearer syt_placeholder"


def test_invites_are_joined_or_rejected_by_inviter(homeserver):
    requests, _, _ = homeserver
    posts = [a for a in actions(requests) if a[0] == "POST"]
    assert posts == [("POST", "/join/!friend:example.org"), ("POST", "/rooms/!stranger:example.org/leave")]


def test_rate_limit_and_server_errors_are_retried(homeserver):
    requests, sleeps, heartbeat = homeserver
    assert sleeps == [1.5, 1, 2]
    since = [r.url.params.get("since") for r in requests if r.url.path.endswith("/sync")]
    assert since == [None, "s1", "s1", "s1", "s1", "s2"]
    assert heartbeat.exists()
