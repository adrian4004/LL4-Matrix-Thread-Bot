import pytest

from bot import ConfigError, after_own_join, load_config, should_join, thread_reply, txn_id

BOT = "@threadbot:example.org"
ALICE = "@alice:example.org"
ROOT = "$root:example.org"


def message(content: dict, sender: str = ALICE, type_: str = "m.room.message") -> dict:
    return {"type": type_, "sender": sender, "event_id": ROOT, "content": content}


def text(body: str, **extra) -> dict:
    return message({"msgtype": "m.text", "body": body, **extra})


def html(body: str, formatted: str) -> dict:
    return text(body, format="org.matrix.custom.html", formatted_body=formatted)


def encrypted(relation: dict | None = None) -> dict:
    content = {"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "AwgA...", "session_id": "s"}
    if relation is not None:
        content["m.relates_to"] = relation
    return message(content, type_="m.room.encrypted")


THREAD_REPLY = {
    "msgtype": "m.notice",
    "body": "💬 Hier geht's weiter",
    "m.relates_to": {
        "rel_type": "m.thread",
        "event_id": ROOT,
        "is_falling_back": True,
        "m.in_reply_to": {"event_id": ROOT},
    },
}


# --- ignored events ---------------------------------------------------------

@pytest.mark.parametrize(
    "event",
    [
        pytest.param(text("hello") | {"sender": BOT}, id="own message"),
        pytest.param(
            text("in thread", **{"m.relates_to": {"rel_type": "m.thread", "event_id": "$other"}}),
            id="thread message",
        ),
        pytest.param(
            text("* fixed", **{"m.new_content": {"msgtype": "m.text", "body": "fixed"},
                                "m.relates_to": {"rel_type": "m.replace", "event_id": "$other"}}),
            id="edit",
        ),
        pytest.param(
            message({"m.relates_to": {"rel_type": "m.annotation", "event_id": "$other", "key": "👍"}},
                    type_="m.reaction"),
            id="reaction event type",
        ),
        pytest.param(
            message({"m.relates_to": {"rel_type": "m.annotation", "event_id": "$other", "key": "👍"}}),
            id="annotation relation on a message",
        ),
        pytest.param(
            text("unknown relation", **{"m.relates_to": {"rel_type": "org.example.custom", "event_id": "$o"}}),
            id="any other rel_type",
        ),
        pytest.param(
            text("> quoted\n\nanswer", **{"m.relates_to": {"m.in_reply_to": {"event_id": "$other"}}}),
            id="rich reply",
        ),
        pytest.param(message({"msgtype": "m.notice", "body": "beep"}), id="notice"),
        pytest.param(message({"membership": "join"}, type_="m.room.member"), id="other event type"),
        pytest.param(message({}), id="redacted message"),
        pytest.param(encrypted({"rel_type": "m.thread", "event_id": "$other"}), id="encrypted thread message"),
        pytest.param(encrypted({"m.in_reply_to": {"event_id": "$other"}}), id="encrypted rich reply"),
        pytest.param(encrypted({"rel_type": "m.replace", "event_id": "$other"}), id="encrypted edit"),
    ],
)
def test_ignored(event):
    assert thread_reply(event, BOT) is None


# --- every root message gets the same thread reply ---------------------------

@pytest.mark.parametrize(
    "event",
    [
        pytest.param(text("hello"), id="plain text"),
        pytest.param(message({"msgtype": "m.emote", "body": "**waves**"}), id="bold emote"),
        pytest.param(message({"msgtype": "m.image", "body": "cat.png", "url": "mxc://example.org/a"}), id="image"),
        pytest.param(message({"msgtype": "m.file", "body": "a.pdf", "url": "mxc://example.org/b"}), id="file"),
        pytest.param(message({"msgtype": "m.video", "body": "v.mp4", "url": "mxc://example.org/c"}), id="video"),
        pytest.param(message({"msgtype": "m.audio", "body": "a.ogg", "url": "mxc://example.org/d"}), id="audio"),
        pytest.param(encrypted(), id="encrypted root"),
        pytest.param(text("this is **partly** bold"), id="partially bold body"),
    ],
)
def test_root_message_gets_thread_reply(event):
    assert thread_reply(event, BOT) == THREAD_REPLY


def test_root_without_relation_object_is_threaded():
    event = text("hello", **{"m.relates_to": {}})
    assert thread_reply(event, BOT) == THREAD_REPLY


# --- a bold title is not echoed ----------------------------------------------

@pytest.mark.parametrize(
    "event",
    [
        pytest.param(text("**Titel**"), id="bold markdown body"),
        pytest.param(html("**Titel**", "<strong>Titel</strong>"), id="bold html"),
    ],
)
def test_bold_title_gets_plain_reply(event):
    reply = thread_reply(event, BOT)
    assert reply == THREAD_REPLY
    assert "format" not in reply and "formatted_body" not in reply


# --- invites ----------------------------------------------------------------

def invite_state(inviter: str, invitee: str = BOT) -> list[dict]:
    return [
        {"type": "m.room.name", "sender": inviter, "state_key": "", "content": {"name": "Room"}},
        {"type": "m.room.member", "sender": inviter, "state_key": inviter, "content": {"membership": "join"}},
        {"type": "m.room.member", "sender": inviter, "state_key": invitee, "content": {"membership": "invite"}},
    ]


ALLOWED = frozenset({ALICE, "@anna:example.org"})


def test_invite_from_allowed_inviter_is_joined():
    assert should_join(invite_state(ALICE), BOT, ALLOWED) is True


def test_invite_from_foreign_inviter_is_rejected():
    assert should_join(invite_state("@mallory:example.org"), BOT, ALLOWED) is False


def test_invite_is_read_from_own_member_event_only():
    # An allowed user's membership event for someone else must not count as the bot's invite.
    state = invite_state("@mallory:example.org")
    state.insert(0, {"type": "m.room.member", "sender": ALICE, "state_key": "@other:example.org",
                     "content": {"membership": "invite"}})
    assert should_join(state, BOT, ALLOWED) is False


# --- history on join -------------------------------------------------------

def member(state_key: str, membership: str, prev: str | None = None) -> dict:
    event = {"type": "m.room.member", "sender": state_key, "state_key": state_key,
             "event_id": f"${state_key}-{membership}", "content": {"membership": membership}}
    if prev:
        event["unsigned"] = {"prev_content": {"membership": prev}}
    return event


def root(event_id: str) -> dict:
    return {**text("hi"), "event_id": event_id}


def test_backlog_before_own_join_is_dropped_and_later_messages_kept():
    timeline = [root("$old1"), root("$old2"), member(BOT, "join", prev="invite"), root("$new")]
    assert after_own_join(timeline, BOT) == [root("$new")]


def test_own_join_without_prev_content_still_cuts_the_backlog():
    assert after_own_join([root("$old"), member(BOT, "join")], BOT) == []


def test_timeline_without_own_join_is_kept_whole():
    timeline = [root("$a"), member(ALICE, "join", prev="invite"), root("$b")]
    assert after_own_join(timeline, BOT) == timeline


def test_own_profile_change_is_not_a_join():
    timeline = [root("$a"), member(BOT, "join", prev="join"), root("$b")]
    assert after_own_join(timeline, BOT) == timeline


def test_own_leave_or_invite_does_not_cut():
    timeline = [root("$a"), member(BOT, "invite"), member(BOT, "leave"), root("$b")]
    assert after_own_join(timeline, BOT) == timeline


# --- configuration ----------------------------------------------------------

ENV = {
    "MATRIX_HOMESERVER": "https://matrix.example.org/",
    "MATRIX_ACCESS_TOKEN": "syt_placeholder",
    "ALLOWED_INVITERS": " @alice:example.org, @anna:example.org ,",
}


def test_config_parses_allowed_inviters():
    config = load_config(ENV)
    assert config.allowed_inviters == ALLOWED
    assert config.homeserver == "https://matrix.example.org"


@pytest.mark.parametrize("value", [None, "", " , "])
def test_empty_allowed_inviters_refuses_to_start(value):
    env = {k: v for k, v in ENV.items() if k != "ALLOWED_INVITERS"}
    if value is not None:
        env["ALLOWED_INVITERS"] = value
    with pytest.raises(ConfigError, match="ALLOWED_INVITERS"):
        load_config(env)


@pytest.mark.parametrize("missing", ["MATRIX_HOMESERVER", "MATRIX_ACCESS_TOKEN"])
def test_missing_required_variable_refuses_to_start(missing):
    with pytest.raises(ConfigError, match=missing):
        load_config({**ENV, missing: ""})


# --- transaction ids --------------------------------------------------------

def test_txn_id_is_deterministic_per_root_event():
    assert txn_id(ROOT) == txn_id(ROOT)
    assert txn_id(ROOT) != txn_id("$another:example.org")
