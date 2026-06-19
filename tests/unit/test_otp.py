"""OTP engine over the in-memory store — no network, no Redis."""

import logging

from cogno_herald import InMemoryOTPStore, OtpService, extract_domain, generate_code


def test_generate_code_is_six_digits():
    for _ in range(50):
        code = generate_code()
        assert len(code) == 6 and code.isdigit()


def test_extract_domain():
    assert extract_domain("a@Example.COM ") == "example.com"
    assert extract_domain("bad") == ""
    assert extract_domain("") == ""


async def test_send_stores_and_verify_succeeds_once():
    captured = {}

    async def sender(to, subject, body):
        captured["body"] = body
        return True

    svc = OtpService(InMemoryOTPStore(), sender=sender)
    assert await svc.send("a@x.com") is True
    assert await svc.has_pending("a@x.com") is True

    # pull the code the user "received" out of the email body
    code = next(tok for tok in captured["body"].split() if tok.isdigit() and len(tok) == 6)
    assert await svc.verify("a@x.com", code) is True
    # single-use: the code is burned on success
    assert await svc.has_pending("a@x.com") is False
    assert await svc.verify("a@x.com", code) is False


async def test_wrong_code_fails_and_burns_after_max():
    svc = OtpService(InMemoryOTPStore(), max_verify=3)
    await svc.send("a@x.com")  # no sender → stored only
    for _ in range(3):
        assert await svc.verify("a@x.com", "000000") is False
    # 4th attempt exceeds max → burned (code gone even if guessed right next)
    assert await svc.verify("a@x.com", "000000") is False
    assert await svc.has_pending("a@x.com") is False


async def test_no_sender_logs_warning(caplog):
    svc = OtpService(InMemoryOTPStore())
    with caplog.at_level(logging.WARNING, logger="cogno_herald.otp"):
        await svc.send("a@x.com")
    assert any("event=no_sender" in r.message for r in caplog.records)


async def test_mock_code_bypass():
    svc = OtpService(InMemoryOTPStore(), mock_code="123456")
    assert await svc.verify("a@x.com", "123456") is True


async def test_clear_removes_pending():
    svc = OtpService(InMemoryOTPStore())
    await svc.send("a@x.com")
    await svc.clear("a@x.com")
    assert await svc.has_pending("a@x.com") is False


async def test_store_ttl_expiry(monkeypatch):
    import cogno_herald.ports as ports
    t = {"now": 1000.0}
    monkeypatch.setattr(ports.time, "monotonic", lambda: t["now"])
    store = InMemoryOTPStore()
    await store.set("k", "v", ttl_seconds=10)
    assert await store.get("k") == "v"
    t["now"] = 1011.0
    assert await store.get("k") is None
    assert await store.exists("k") is False
