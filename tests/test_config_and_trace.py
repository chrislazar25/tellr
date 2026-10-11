import pytest

import config
from observability import configure_trace, trace


@pytest.mark.parametrize("value", [None, "", "   "])
def test_uri_falls_back_to_local(monkeypatch, tmp_path, value):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    if value is None:
        monkeypatch.delenv("MONGODB_URI", raising=False)
    else:
        monkeypatch.setenv("MONGODB_URI", value)
    assert config.mongodb_uri() == config.LOCAL_MONGODB_URI


def test_explicit_uri_wins_over_dotenv(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("MONGODB_URI=mongodb://localhost:27018/from_file\n")
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27019/from_environment")
    assert config.mongodb_uri() == "mongodb://localhost:27019/from_environment"


def test_trace_is_gated_and_transport_failure_is_nonfatal(monkeypatch):
    events = []
    configure_trace(events.append)
    try:
        monkeypatch.delenv("RAINDROP_WRITE_KEY", raising=False)
        trace({"step": "demo"})
        assert events == []
        monkeypatch.setenv("RAINDROP_WRITE_KEY", "fictional-test-key")
        trace({"step": "demo"})
        assert events == [{"step": "demo"}]

        def failing_sink(event):
            raise RuntimeError("transport unavailable")

        configure_trace(failing_sink)
        trace({"step": "demo"})
    finally:
        configure_trace(None)
