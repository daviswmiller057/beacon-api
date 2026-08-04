import io
import json
from urllib.error import HTTPError, URLError

import pytest

from app.cli import (
    AuthenticationError,
    BeaconClient,
    BeaconResponseError,
    BeaconUnavailableError,
    CliConfig,
    ConfigurationError,
    MalformedResponseError,
    main,
    run_interactive,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class RecordingOpener:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        return FakeResponse(json.dumps(self.payload).encode())


def config(**changes):
    values = {
        "api_url": "http://beacon.test:8000",
        "api_key": "secret-key",
        "timeout": 4.5,
    }
    values.update(changes)
    return CliConfig(**values)


def test_configuration_loads_environment_and_cli_overrides():
    loaded = CliConfig.load(
        api_url="https://override.test/",
        timeout=2,
        environ={
            "BEACON_API_URL": "http://environment.test:8000",
            "BEACON_API_KEY": "environment-key",
        },
    )

    assert loaded.api_url == "https://override.test"
    assert loaded.api_key == "environment-key"
    assert loaded.timeout == 2
    assert "environment-key" not in repr(loaded)


def test_configuration_uses_local_default_and_requires_key_for_protected_calls():
    loaded = CliConfig.load(environ={})

    assert loaded.api_url == "http://localhost:8000"
    with pytest.raises(ConfigurationError, match="BEACON_API_KEY"):
        loaded.require_api_key()


@pytest.mark.parametrize("url", ["localhost:8000", "ftp://beacon.test", ""])
def test_configuration_rejects_invalid_explicit_urls(url):
    kwargs = {"api_url": url} if url else {"api_url": " "}
    with pytest.raises(ConfigurationError, match="BEACON_API_URL"):
        CliConfig.load(environ={}, **kwargs)


def test_interact_constructs_json_request_and_authentication_header():
    opener = RecordingOpener({"result": "Created task."})
    client = BeaconClient(config(), opener=opener)

    assert client.interact("Buy Liquid IV tomorrow")["result"] == "Created task."
    request, timeout = opener.calls[0]
    headers = dict(request.header_items())
    assert request.full_url == "http://beacon.test:8000/interact"
    assert request.method == "POST"
    assert json.loads(request.data) == {"message": "Buy Liquid IV tomorrow"}
    assert headers["X-beacon-api-key"] == "secret-key"
    assert headers["Content-type"] == "application/json"
    assert timeout == 4.5


def test_conversation_constructs_idempotent_session_request():
    opener = RecordingOpener(
        {"reply": "Synthetic reply", "session_id": "session-1"}
    )
    client = BeaconClient(config(), opener=opener)

    response = client.conversation(
        "Follow up", session_id="session-1", client_message_id="message-2"
    )

    assert response["reply"] == "Synthetic reply"
    request, _ = opener.calls[0]
    assert request.full_url.endswith("/v1/conversation")
    assert json.loads(request.data) == {
        "message": "Follow up",
        "client_message_id": "message-2",
        "session_id": "session-1",
    }


def test_conversation_response_exposes_session_for_follow_up():
    from app.cli import format_response

    assert format_response(
        "conversation", {"reply": "Synthetic reply", "session_id": "session-9"}
    ) == "Synthetic reply\nSession: session-9"


def test_one_shot_conversation_mode_preserves_session_and_message_ids(
    monkeypatch, capsys
):
    calls = []

    class FakeClient:
        def __init__(self, loaded_config):
            self.config = loaded_config

        def conversation(self, message, *, session_id, client_message_id):
            calls.append((message, session_id, client_message_id))
            return {"reply": "Follow-up complete", "session_id": session_id}

    monkeypatch.setattr("app.cli.BeaconClient", FakeClient)
    assert (
        main(
            [
                "--conversation",
                "--session-id",
                "session-1",
                "--client-message-id",
                "message-2",
                "Follow",
                "up",
            ]
        )
        == 0
    )
    assert calls == [("Follow up", "session-1", "message-2")]
    assert "Session: session-1" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("method_name", "path"),
    [("brief", "/brief"), ("status", "/status")],
)
def test_authenticated_get_calls(method_name, path):
    opener = RecordingOpener({"status": "ok"})
    client = BeaconClient(config(), opener=opener)

    getattr(client, method_name)()

    request, _ = opener.calls[0]
    assert request.full_url.endswith(path)
    assert request.method == "GET"
    assert dict(request.header_items())["X-beacon-api-key"] == "secret-key"


def test_health_is_public_and_does_not_send_authentication_header():
    opener = RecordingOpener({"status": "ok", "service": "beacon-api"})
    BeaconClient(config(api_key=None), opener=opener).health()

    request, _ = opener.calls[0]
    assert "X-beacon-api-key" not in dict(request.header_items())


def test_invalid_api_key_has_friendly_error():
    error = HTTPError(
        "http://beacon.test/status",
        401,
        "Unauthorized",
        {},
        io.BytesIO(b'{"detail":"Invalid Beacon API key"}'),
    )

    def reject(*args, **kwargs):
        raise error

    with pytest.raises(AuthenticationError, match="Check BEACON_API_KEY"):
        BeaconClient(config(), opener=reject).status()


def test_server_detail_is_preserved_without_raw_json():
    error = HTTPError(
        "http://beacon.test/brief",
        502,
        "Bad Gateway",
        {},
        io.BytesIO(b'{"detail":"Calendar unavailable"}'),
    )

    def reject(*args, **kwargs):
        raise error

    with pytest.raises(BeaconResponseError, match="Calendar unavailable"):
        BeaconClient(config(), opener=reject).brief()


def test_offline_and_timeout_errors_are_user_friendly():
    def offline(*args, **kwargs):
        raise URLError("connection refused")

    with pytest.raises(BeaconUnavailableError, match="Docker service"):
        BeaconClient(config(), opener=offline).health()


def test_malformed_json_response_is_reported():
    def malformed(*args, **kwargs):
        return FakeResponse(b"not-json")

    with pytest.raises(MalformedResponseError, match="unreadable response"):
        BeaconClient(config(), opener=malformed).status()


def test_one_shot_mode_sends_message_and_formats_result(monkeypatch, capsys):
    calls = []

    class FakeClient:
        def __init__(self, loaded_config):
            self.config = loaded_config

        def interact(self, message):
            calls.append(message)
            return {"result": 'Created task "Buy Liquid IV".'}

    monkeypatch.setattr("app.cli.BeaconClient", FakeClient)
    monkeypatch.setenv("BEACON_API_KEY", "test")

    assert main(["Buy Liquid IV tomorrow"]) == 0
    assert calls == ["Buy Liquid IV tomorrow"]
    assert 'Created task "Buy Liquid IV".' in capsys.readouterr().out


def test_brief_one_shot_mode(monkeypatch, capsys):
    class FakeClient:
        def __init__(self, loaded_config):
            self.config = loaded_config

        def brief(self):
            return {
                "date": "2026-08-03",
                "spoken_summary": "You have rehearsal at 2 PM.",
                "warnings": [],
            }

    monkeypatch.setattr("app.cli.BeaconClient", FakeClient)
    monkeypatch.setenv("BEACON_API_KEY", "test")

    assert main(["--brief"]) == 0
    output = capsys.readouterr().out
    assert "Daily brief — 2026-08-03" in output
    assert "You have rehearsal at 2 PM." in output


def test_interactive_commands_and_normal_text_are_dispatched():
    class FakeClient:
        config = config()

        def __init__(self):
            self.calls = []

        def interact(self, message):
            self.calls.append(("interact", message))
            return {"result": "Created task."}

        def brief(self):
            self.calls.append(("brief", None))
            return {"date": "2026-08-03", "spoken_summary": "Clear day."}

        def status(self):
            self.calls.append(("status", None))
            return {"status": "ok", "service": "beacon-api"}

        def health(self):
            self.calls.append(("health", None))
            return {"status": "ok", "service": "beacon-api"}

    client = FakeClient()
    entries = iter([
        "/help",
        "/brief",
        "/status",
        "/health",
        "Buy Liquid IV tomorrow",
        "/exit",
    ])
    output = io.StringIO()

    assert run_interactive(client, input_fn=lambda prompt: next(entries), output=output) == 0
    assert client.calls == [
        ("brief", None),
        ("status", None),
        ("health", None),
        ("interact", "Buy Liquid IV tomorrow"),
    ]
    rendered = output.getvalue()
    assert "Commands:" in rendered
    assert "Daily brief" in rendered
    assert "Beacon health: ok" in rendered


def test_interactive_errors_do_not_end_session():
    class FakeClient:
        config = config()

        def interact(self, message):
            raise BeaconUnavailableError("Beacon is unavailable.")

    entries = iter(["hello", "/quit"])
    errors = io.StringIO()

    assert run_interactive(
        FakeClient(),
        input_fn=lambda prompt: next(entries),
        output=io.StringIO(),
        error=errors,
    ) == 0
    assert "Beacon is unavailable." in errors.getvalue()
