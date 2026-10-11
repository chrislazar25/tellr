import importlib
from urllib.parse import quote

import pytest

import config


def test_error_redacts_loaded_secrets_encoded_credentials_and_ips(monkeypatch, tmp_path):
    key, password = "fictional-sensitive-token", "fictional@password/$"
    uri = f"mongodb+srv://fixture-user:{quote(password, safe='')}@fixture-host.example/?appName=Test"
    (tmp_path / ".env.secrets").write_text(
        f"ANTHROPIC_API_KEY={key}\nMONGODB_URI={uri}\nMONGODB_ATLAS_PASSWORD='{password}'\n"
    )
    for variable in ("ANTHROPIC_API_KEY", "MONGODB_URI", "MONGODB_ATLAS_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    message = config.safe_exception(ValueError(f"Auth denied: {key} {password} {quote(password, safe='')} {uri} fixture-host.example fixture-user 192.0.2.123"))
    assert message.startswith("ValueError: Auth denied:")
    assert "[redacted]" in message
    for secret in (key, password, quote(password, safe=""), uri, "fixture-host.example", "fixture-user", "192.0.2.123"):
        assert secret not in message


def test_error_redacts_unconfigured_mongodb_uri_and_key_patterns():
    message = config.safe_exception(RuntimeError("Cannot connect mongodb://user:password@host.example/test sk-fictionalkey x-api-key: fictional-key"))
    assert "RuntimeError: Cannot connect" in message
    assert "password" not in message
    assert "sk-fictionalkey" not in message
    assert "fictional-key" not in message


def test_error_retains_actionable_provider_message():
    assert config.safe_exception(ValueError("Selected transcript has no payment terms")) == "ValueError: Selected transcript has no payment terms"


def test_error_redacts_overridden_file_secret_and_ipv6(monkeypatch, tmp_path):
    (tmp_path / ".env.secrets").write_text("ANTHROPIC_API_KEY=old-fictional-key\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "new-fictional-key")
    result = config.safe_exception(RuntimeError("old-fictional-key new-fictional-key 2001:db8::1 ::1 cluster0-shard-00-00.example.mongodb.net"))
    for secret in ("old-fictional-key", "new-fictional-key", "2001:db8::1", "::1", "example.mongodb.net"):
        assert secret not in result


@pytest.mark.parametrize("module_name,argv", [
    ("pipeline", ["pipeline.py", "enqueue"]), ("worker", ["worker.py"]),
    ("db.seed", ["seed.py"]), ("db.ping", ["ping.py"]),
    ("scripts.change_vendor_account", ["change_vendor_account.py", "vendor-001", "acct-test"]),
])
def test_all_clis_report_exception_type_and_safe_message(monkeypatch, capsys, module_name, argv):
    module = importlib.import_module(module_name)
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fictional-private-key")
    def fail():
        raise RuntimeError("Authentication failed for fictional-private-key at mongodb://private:password@private-host/")
    monkeypatch.setattr(module, "connect", fail)
    assert module.main() == 1
    output = capsys.readouterr()
    assert "RuntimeError: Authentication failed" in output.err
    assert "fictional-private-key" not in output.err
    assert "password" not in output.err
    assert "private-host" not in output.err
    assert output.out == ("fail\n" if module_name == "db.ping" else "")
