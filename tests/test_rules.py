import pytest

from bot import ConfigError, load_config, should_join, thread_reply, txn_id

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


PLAIN_THREAD = {
    "msgtype": "m.notice",
    "body": "🧵",
    "m.relates_to": {
        "rel_type": "m.thread",
        "event_id": ROOT,
        "is_falling_back": True,
        "m.in_reply_to": {"event_id": ROOT},
    },
}


def titled_thread(body: str, formatted: str) -> dict:
    return {**PLAIN_THREAD, "body": body, "format": "org.matrix.custom.html", "formatted_body": formatted}


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


# --- root messages get a plain thread ---------------------------------------

@pytest.mark.parametrize(
    "event",
    [
        pytest.param(text("hello"), id="plain text"),
        pytest.param(message({"msgtype": "m.emote", "body": "**waves**"}), id="emote (bold not a title)"),
        pytest.param(message({"msgtype": "m.image", "body": "cat.png", "url": "mxc://example.org/a"}), id="image"),
        pytest.param(message({"msgtype": "m.file", "body": "a.pdf", "url": "mxc://example.org/b"}), id="file"),
        pytest.param(message({"msgtype": "m.video", "body": "v.mp4", "url": "mxc://example.org/c"}), id="video"),
        pytest.param(message({"msgtype": "m.audio", "body": "a.ogg", "url": "mxc://example.org/d"}), id="audio"),
        pytest.param(encrypted(), id="encrypted root"),
        pytest.param(text("this is **partly** bold"), id="partially bold body"),
        pytest.param(text("**one** and **two**"), id="two bold parts in body"),
        pytest.param(html("**one** rest", "<strong>one</strong> rest"), id="partially bold html"),
        pytest.param(text("****"), id="empty bold"),
        pytest.param(text("**line one\nline two**"), id="multi-line bold"),
    ],
)
def test_plain_thread(event):
    assert thread_reply(event, BOT) == PLAIN_THREAD


def test_root_without_relation_object_is_threaded():
    event = text("hello", **{"m.relates_to": {}})
    assert thread_reply(event, BOT) == PLAIN_THREAD


# --- bold titles ------------------------------------------------------------

def test_bold_plain_body_becomes_title():
    assert thread_reply(text("**Titel**"), BOT) == titled_thread("🧵 **Titel**", "🧵 <strong>Titel</strong>")


@pytest.mark.parametrize("tag", ["strong", "b"])
def test_bold_formatted_body_becomes_title(tag):
    event = html("Titel", f"<{tag}>Titel</{tag}>")
    assert thread_reply(event, BOT) == titled_thread("🧵 **Titel**", "🧵 <strong>Titel</strong>")


def test_title_is_html_escaped():
    event = html("**Fish & <Chips>**", "<strong>Fish &amp; &lt;Chips&gt;</strong>")
    expected = titled_thread("🧵 **Fish & <Chips>**", "🧵 <strong>Fish &amp; &lt;Chips&gt;</strong>")
    assert thread_reply(event, BOT) == expected


def test_title_from_plain_body_is_html_escaped():
    expected = titled_thread("🧵 **a<b>c**", "🧵 <strong>a&lt;b&gt;c</strong>")
    assert thread_reply(text("**a<b>c**"), BOT) == expected


def test_encrypted_root_never_gets_title():
    event = encrypted()
    event["content"].update(msgtype="m.text", body="**Titel**")
    assert thread_reply(event, BOT) == PLAIN_THREAD


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
