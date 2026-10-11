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


def test_secrets_override_dotenv_without_exporting_values(monkeypatch, tmp_path):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    (tmp_path / ".env").write_text("MONGODB_URI=\n")
    expected = "mongodb://localhost:27018/from_secrets"
    (tmp_path / ".env.secrets").write_text(f"MONGODB_URI={expected}\nLITERAL_TOKEN=abc${{DO_NOT_EXPAND}}\n")
    assert config.mongodb_uri() == expected
    assert config.value("LITERAL_TOKEN") == "abc${DO_NOT_EXPAND}"
    assert "MONGODB_URI" not in config.os.environ


@pytest.mark.parametrize("success", [True, False])
def test_ping_prints_only_ok_or_fail(monkeypatch, capsys, success):
    from types import SimpleNamespace
    import db.ping as ping_module

    closed = []

    def command(name):
        assert name == "ping"
        if not success:
            raise RuntimeError("mongodb://private-user:private-password@private-host")

    client = SimpleNamespace(admin=SimpleNamespace(command=command), close=lambda: closed.append(True))
    monkeypatch.setattr(ping_module, "connect", lambda: SimpleNamespace(client=client))
    assert ping_module.main() == (0 if success else 1)
    assert capsys.readouterr().out == ("ok\n" if success else "fail\n")
    assert closed == [True]


def test_ping_hides_connection_setup_errors(monkeypatch, capsys):
    import db.ping as ping_module

    def fail():
        raise ValueError("mongodb://private-user:private-password@private-host")

    monkeypatch.setattr(ping_module, "connect", fail)
    assert ping_module.main() == 1
    assert capsys.readouterr().out == "fail\n"


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
