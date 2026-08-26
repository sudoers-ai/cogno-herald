"""Pure .ics builder tests — no network."""

from datetime import datetime

from cogno_herald.ical import _ical_escape, build_ics_cancel, build_ics_event


def test_event_has_required_vevent_fields():
    ics = build_ics_event(
        uid="appt-1",
        summary="Consulta",
        dtstart=datetime(2026, 6, 20, 14, 0, 0),
        dtend=datetime(2026, 6, 20, 14, 30, 0),
        organizer_email="vet@clinic.com",
        organizer_name="Dra. Ana",
        attendees=["client@example.com"],
        description="Retorno",
        location="Sala 2",
    )
    assert "METHOD:REQUEST" in ics
    assert "UID:appt-1" in ics
    assert "DTSTART:20260620T140000" in ics
    assert "DTEND:20260620T143000" in ics
    assert "STATUS:CONFIRMED" in ics
    assert "ORGANIZER;CN=Dra. Ana:mailto:vet@clinic.com" in ics
    assert "ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:mailto:client@example.com" in ics
    assert ics.endswith("END:VCALENDAR\r\n")


def test_event_without_organizer_name_uses_plain_organizer():
    ics = build_ics_event("u", "S", datetime(2026, 1, 1), datetime(2026, 1, 1),
                          "o@x.com")
    assert "ORGANIZER:mailto:o@x.com" in ics
    assert "CN=" not in ics


def test_cancel_uses_cancel_method_and_status():
    ics = build_ics_cancel("appt-1", "Consulta", datetime(2026, 6, 20, 14, 0),
                           datetime(2026, 6, 20, 14, 30), "vet@clinic.com",
                           attendees=["client@example.com"])
    assert "METHOD:CANCEL" in ics
    assert "STATUS:CANCELLED" in ics
    assert "SUMMARY:CANCELLED: Consulta" in ics
    assert "SEQUENCE:1" in ics


def test_escape_handles_special_chars():
    assert _ical_escape("a;b,c\nd\\e") == "a\\;b\\,c\\nd\\\\e"


# ── SEQUENCE: the revision number a client uses to decide an update is newer ──────────
def test_event_sequence_defaults_to_zero_and_is_settable():
    """The default preserves the previously hardcoded value, so existing callers are unchanged."""
    base = build_ics_event("u", "S", datetime(2026, 1, 1), datetime(2026, 1, 1), "o@x.com")
    assert "SEQUENCE:0" in base
    bumped = build_ics_event("u", "S", datetime(2026, 1, 1), datetime(2026, 1, 1), "o@x.com",
                             sequence=7)
    assert "SEQUENCE:7" in bumped and "SEQUENCE:0" not in bumped


def test_cancel_sequence_defaults_to_one_and_is_settable():
    """A cancellation is where a wrong SEQUENCE is least visible.

    The hardcoded ``1`` is LOWER than any revision a caller may have sent in between, so such
    a cancel is discarded by the client and the appointment stays in the calendar for good —
    while the send succeeds and is logged. Hence the parameter."""
    base = build_ics_cancel("u", "S", datetime(2026, 1, 1), datetime(2026, 1, 1), "o@x.com")
    assert "SEQUENCE:1" in base
    bumped = build_ics_cancel("u", "S", datetime(2026, 1, 1), datetime(2026, 1, 1), "o@x.com",
                              sequence=99)
    assert "SEQUENCE:99" in bumped and "SEQUENCE:1" not in bumped


def test_a_revision_can_be_numbered_ABOVE_the_cancel_default():
    """The property the caller actually needs: an update, then a cancel, both honoured.

    Asserts the ORDER between the two messages rather than each in isolation — a client keeps
    one number per UID, so a cancel that does not exceed the last update is ignored."""
    import re

    def _seq(ics: str) -> int:
        m = re.search(r"SEQUENCE:(\d+)", ics)
        assert m is not None
        return int(m.group(1))

    convite = build_ics_event("appt-1", "S", datetime(2026, 1, 1), datetime(2026, 1, 1),
                              "o@x.com", sequence=100)
    remarcado = build_ics_event("appt-1", "S", datetime(2026, 1, 2), datetime(2026, 1, 2),
                                "o@x.com", sequence=101)
    cancelado = build_ics_cancel("appt-1", "S", datetime(2026, 1, 2), datetime(2026, 1, 2),
                                 "o@x.com", sequence=102)
    assert _seq(convite) < _seq(remarcado) < _seq(cancelado)
