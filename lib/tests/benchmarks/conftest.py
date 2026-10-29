import pytest

# What OPENAI_API_KEY holds while replaying: anything non-empty gets past the
# library's up-front key checks, and VCR answers every request from the cassette.
REPLAY_PLACEHOLDER_KEY = "sk-cassette-replay"


@pytest.fixture(autouse=True)
def placeholder_key_for_replay(request, monkeypatch):
    """Let committed cassettes replay without a real key (#161).

    The library checks for OPENAI_API_KEY before it makes a call (judges.py
    refuses outright), so without one these suites failed before VCR could
    replay anything. Only in replay-only mode — pytest-recording's default,
    `none` — where no request can reach the network; in a recording mode a
    placeholder would get a 401 recorded into the cassette, so the real key
    stays required there.
    """
    if request.config.getoption("--record-mode", default=None) in (None, "none"):
        import os
        if not os.environ.get("OPENAI_API_KEY"):
            monkeypatch.setenv("OPENAI_API_KEY", REPLAY_PLACEHOLDER_KEY)
