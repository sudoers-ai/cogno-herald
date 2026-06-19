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
