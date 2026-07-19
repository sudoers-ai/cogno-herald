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


async def test_per_call_sender_override():
    # A multi-tenant host passes the tenant's own sender per call; it wins over the instance one,
    # and the code still verifies (store is unchanged).
    used = []

    async def instance_sender(to, subject, body):
        used.append(("instance", to))
        return True

    async def tenant_sender(to, subject, body):
        used.append(("tenant", to))
        return True

    svc = OtpService(InMemoryOTPStore(), sender=instance_sender)
    assert await svc.send("emp@acme.com", sender=tenant_sender) is True
    assert used == [("tenant", "emp@acme.com")]              # override won; instance sender untouched
    # falls back to the instance sender when no override is given
    assert await svc.send("other@x.com") is True
    assert used[-1] == ("instance", "other@x.com")


async def test_non_consuming_check_then_consume():
    # check mode leaves the code alive so a later consuming verify (post-commit) still passes
    svc = OtpService(InMemoryOTPStore())
    await svc.send("a@x.com")
    code = await svc.store.get("otp:a@x.com")
    assert await svc.verify("a@x.com", code, consume=False) is True
    assert await svc.has_pending("a@x.com") is True          # NOT burned
    assert await svc.verify("a@x.com", code) is True         # default consumes
    assert await svc.has_pending("a@x.com") is False
    assert await svc.verify("a@x.com", code) is False        # single-use still holds


async def test_non_consuming_check_wrong_code_still_counts_attempts():
    # brute-force lockout is just as tight in check mode: wrong guesses burn after max_verify
    svc = OtpService(InMemoryOTPStore(), max_verify=3)
    await svc.send("a@x.com")
    code = await svc.store.get("otp:a@x.com")
    for _ in range(3):
        assert await svc.verify("a@x.com", "000000", consume=False) is False
    # over the limit → burned; even the right code is dead now
    assert await svc.verify("a@x.com", code, consume=False) is False
    assert await svc.has_pending("a@x.com") is False


async def test_wrong_code_fails_and_burns_after_max():
    svc = OtpService(InMemoryOTPStore(), max_verify=3)
    await svc.send("a@x.com")  # no sender → stored only
    for _ in range(3):
        assert await svc.verify("a@x.com", "000000") is False
    # 4th attempt exceeds max → burned (code gone even if guessed right next)
    assert await svc.verify("a@x.com", "000000") is False
    assert await svc.has_pending("a@x.com") is False


def test_inmemory_store_warns_at_construction(caplog):
    with caplog.at_level(logging.WARNING, logger="cogno_herald.otp"):
        OtpService(InMemoryOTPStore())
    assert any("event=insecure_store" in r.message for r in caplog.records)


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
