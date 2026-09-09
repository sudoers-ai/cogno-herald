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


# ── the zone: which of RFC 5545 §3.3.5's three forms a DATE-TIME is rendered in ───────
#
# The defect these pin, measured 2026-09-08 on the live booking invite: `DTSTART` was rendered
# with `dtstart.strftime(...)`, which DISCARDS `tzinfo`, so every event went out FLOATING. A
# floating DATE-TIME is not "no timezone" — §3.3.5 says it "is always interpreted in the context
# of the local time of the recipient", so the invite asserted A DIFFERENT INSTANT PER READER: a
# 09:00 São Paulo appointment opened at 09:00 Lisbon in the guest's calendar, four hours off,
# and nothing anywhere was red.

def _dtstart_line(ics: str) -> str:
    for line in ics.split("\r\n"):
        if line.startswith("DTSTART"):
            return line
    raise AssertionError(f"no DTSTART in {ics!r}")


def _dtend_line(ics: str) -> str:
    for line in ics.split("\r\n"):
        if line.startswith("DTEND"):
            return line
    raise AssertionError(f"no DTEND in {ics!r}")


def test_a_naive_datetime_still_renders_the_floating_form_it_always_did():
    """The back-compat half, and the reason the zone rides in the VALUE rather than a parameter.

    A caller that does not know the zone must not have one invented for it — rendering a guess
    is worse than omitting, because a guess is unfalsifiable at the far end. So the bytes for a
    naive input are the bytes this builder rendered before the aware branches existed."""
    ics = build_ics_event("u", "S", datetime(2026, 6, 23, 9, 0), datetime(2026, 6, 23, 10, 0),
                          "o@x.com")
    assert _dtstart_line(ics) == "DTSTART:20260623T090000"
    assert _dtend_line(ics) == "DTEND:20260623T100000"
    # Said as a property too, so a future branch that renders BOTH a TZID and the old digits
    # cannot pass the two equalities above by accident: neither line carries a zone at all.
    for line in (_dtstart_line(ics), _dtend_line(ics)):
        assert "TZID" not in line and not line.endswith("Z"), line


def test_an_aware_datetime_in_a_named_zone_travels_with_its_TZID():
    """The fix. The WALL CLOCK survives and the zone name goes with it, which is what a stored
    "09:00" means: an appointment is a wall clock in somebody's zone, not a fixed instant."""
    from zoneinfo import ZoneInfo

    sp = ZoneInfo("America/Sao_Paulo")
    ics = build_ics_event("u", "S",
                          datetime(2026, 6, 23, 9, 0, tzinfo=sp),
                          datetime(2026, 6, 23, 10, 0, tzinfo=sp), "o@x.com")
    assert _dtstart_line(ics) == "DTSTART;TZID=America/Sao_Paulo:20260623T090000"
    assert _dtend_line(ics) == "DTEND;TZID=America/Sao_Paulo:20260623T100000"


def test_the_cancellation_carries_the_zone_too():
    """A CANCEL is matched by UID, but a human reads its DTSTART — and a cancellation that names
    a different hour than the invite it cancels is how somebody keeps the wrong slot."""
    from zoneinfo import ZoneInfo

    sp = ZoneInfo("America/Sao_Paulo")
    ics = build_ics_cancel("u", "S",
                           datetime(2026, 6, 23, 9, 0, tzinfo=sp),
                           datetime(2026, 6, 23, 10, 0, tzinfo=sp), "o@x.com")
    assert _dtstart_line(ics) == "DTSTART;TZID=America/Sao_Paulo:20260623T090000"


def test_an_aware_datetime_with_no_zone_NAME_renders_the_instant_in_UTC():
    """`timezone.utc` and a fixed offset carry an instant but no wall clock in a named zone.

    Rendering `TZID=UTC+00:00` would be a zone name nobody's tz database has; rendering the
    digits floating would throw away the one thing the caller DID establish. So: the instant,
    in the one form every client reads identically."""
    from datetime import timedelta, timezone as _tz

    lisbon_summer = _tz(timedelta(hours=1))
    ics = build_ics_event("u", "S",
                          datetime(2026, 6, 23, 9, 0, tzinfo=lisbon_summer),
                          datetime(2026, 6, 23, 10, 0, tzinfo=lisbon_summer), "o@x.com")
    assert _dtstart_line(ics) == "DTSTART:20260623T080000Z"
    assert _dtend_line(ics) == "DTEND:20260623T090000Z"


def test_a_zone_name_that_could_inject_a_parameter_falls_back_to_UTC():
    """`tzinfo` is caller-supplied and its `.key` is a plain string. A `;` in it would not break
    loudly — it would inject a parameter into the property line, which is how a malformed
    calendar becomes a malformed calendar somebody else parses."""
    from datetime import timedelta, tzinfo as _tzinfo

    class _Sneaky(_tzinfo):
        key = "X;RSVP=FALSE"

        def utcoffset(self, dt):
            return timedelta(0)

        def dst(self, dt):
            return timedelta(0)

        def tzname(self, dt):
            return "X"

    ics = build_ics_event("u", "S", datetime(2026, 6, 23, 9, 0, tzinfo=_Sneaky()),
                          datetime(2026, 6, 23, 10, 0, tzinfo=_Sneaky()), "o@x.com")
    assert _dtstart_line(ics) == "DTSTART:20260623T090000Z"
    assert "RSVP=FALSE" not in ics
