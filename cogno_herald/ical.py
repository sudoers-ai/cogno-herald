"""
cogno_herald.ical — iCalendar (.ics) builders.

Pure, stdlib-only RFC 5545 VEVENT generation (REQUEST / CANCEL) that works with
Google Calendar, Outlook, Apple Calendar, etc. Ported from the parent cogno's
``core/email.py``.

**Time and zone — the zone rides in the VALUE, never in a parameter.** RFC 5545 §3.3.5 gives a
DATE-TIME three forms, and the difference is not cosmetic: ``20260623T090000`` is FLOATING and
"is always interpreted in the context of the LOCAL TIME of the recipient", ``...Z`` is an
instant, and ``;TZID=America/Sao_Paulo:`` is a wall clock in a named zone. This builder used to
render form 1 unconditionally — ``dtstart.strftime(...)`` DISCARDS ``tzinfo`` — so a caller that
had already done the work of resolving a zone had it thrown away at the last line, and a guest
reading the invite in Lisbon saw a São Paulo appointment at their own 09:00. That is not an
OMITTED timezone: floating asserts one, and asserts a different one per reader.

So the rendering now follows what the caller HANDS IN, and adds no parameter to decide it:

* a **naive** ``datetime`` → floating, exactly the bytes this function rendered before. A caller
  that does not know the zone must not have one invented for it, and back-compat is the same
  decision seen from the other side.
* an **aware** ``datetime`` whose ``tzinfo`` names a zone (``zoneinfo.ZoneInfo``, which carries
  ``.key``) → ``;TZID=<key>:<wall clock>``. The wall clock is preserved, which is the reading
  the rest of this house already uses (``cogno_praxis.coordinator.ics.build_ics_calendar``
  renders the same shape for the same reason) and the one a stored "14:00" means: an appointment
  is a wall clock in somebody's zone, not a fixed instant, and it must survive a change to that
  zone's offset rules.
* an **aware** ``datetime`` with no usable name (``timezone.utc``, a fixed ``timedelta`` offset,
  a ``pytz`` object) → converted and rendered as UTC ``Z``. The instant is right; only the
  tenant's wall clock is lost, and it was never available to lose.

**No ``VTIMEZONE`` is emitted, and that is deliberate rather than forgotten.** §3.2.19 wants a
``TZID`` to reference a ``VTIMEZONE`` in the same ``VCALENDAR``; Google/Outlook/Apple all resolve
a bare tz-database name, and the failure mode of one that does not is to fall back to floating —
which is precisely today's behaviour, so a bare ``TZID`` is a strict improvement and never a
regression. Emitting one properly means describing a zone's transitions, and doing it for ONE of
this house's two ``.ics`` producers would put the two out of step again; it belongs in a change
that moves both.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional


def _ical_escape(text: str) -> str:
    """Escape special characters for iCalendar property values."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


#: Characters that cannot appear unquoted in an RFC 5545 parameter VALUE (§3.1/§3.2). A zone
#: name carrying one is not a zone name — a ``tzinfo`` is caller-supplied, and a ``;`` in it
#: would inject a parameter into the property line rather than break loudly.
_TZID_FORBIDDEN = set(';:,"\\\r\n')


def _zone_key(dt: datetime) -> Optional[str]:
    """The tz-database name an aware ``dt`` carries, or ``None``.

    ``zoneinfo.ZoneInfo`` exposes it as ``.key``; ``timezone.utc`` and fixed-offset objects have
    no name to expose, and neither does a ``pytz`` zone (it uses ``.zone``) — all of those fall
    to the UTC branch, which is CORRECT for them: their instant is exact and there is no wall
    clock in a named zone to preserve. Read duck-typed rather than by ``isinstance`` so a
    ``ZoneInfo`` subclass, or any tzinfo that chooses to carry the same attribute, works.
    """
    key = getattr(dt.tzinfo, "key", None)
    if not isinstance(key, str) or not key.strip():
        return None
    return None if set(key) & _TZID_FORBIDDEN else key


def _dt_property(name: str, dt: datetime) -> str:
    """One ``DTSTART``/``DTEND`` content line, in the RFC 5545 form ``dt`` actually justifies.

    See the module docstring for why the zone travels in the value. The three branches are the
    three forms of §3.3.5, in the order they are decided: no ``tzinfo`` at all → floating; a
    named zone → ``TZID`` + the wall clock; anything else aware → the instant, in UTC.
    """
    if dt.tzinfo is None:
        return f"{name}:{dt.strftime('%Y%m%dT%H%M%S')}"
    key = _zone_key(dt)
    if key is not None:
        return f"{name};TZID={key}:{dt.strftime('%Y%m%dT%H%M%S')}"
    return f"{name}:{dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def build_ics_event(
    uid: str,
    summary: str,
    dtstart: datetime,
    dtend: datetime,
    organizer_email: str,
    organizer_name: str = "",
    attendees: Optional[List[str]] = None,
    description: str = "",
    location: str = "",
    status: str = "CONFIRMED",
    sequence: int = 0,
) -> str:
    """Build an iCalendar VEVENT string (``METHOD:REQUEST``).

    ``uid`` should be a stable per-event id (e.g. the appointment id) so a later
    CANCEL with the same UID removes the event from the recipient's calendar.

    ``sequence`` is the RFC 5545 revision number. A message about a UID a client has
    already seen is only honoured when its ``SEQUENCE`` is **higher** than the one it
    holds; at an equal (or lower) value the update is silently discarded, so a corrected
    time can be emailed, logged as sent, and never reach the calendar. This library only
    renders the number — deciding it belongs to whoever owns the event's history. The
    default keeps the previous hardcoded value, so callers that do not pass it are
    unchanged.

    ``dtstart``/``dtend`` decide their own RFC 5545 form: naive → floating (unchanged), aware in
    a named zone → ``TZID``, aware otherwise → UTC ``Z``. See the module docstring — a caller
    that knows the tenant's zone hands in an aware value and stops having it discarded here.
    """

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Cogno AI//Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_utc_stamp()}",
        _dt_property("DTSTART", dtstart),
        _dt_property("DTEND", dtend),
        f"SUMMARY:{_ical_escape(summary)}",
        f"STATUS:{status}",
        f"SEQUENCE:{int(sequence)}",
    ]

    if organizer_name:
        lines.append(f"ORGANIZER;CN={_ical_escape(organizer_name)}:mailto:{organizer_email}")
    else:
        lines.append(f"ORGANIZER:mailto:{organizer_email}")

    for email in attendees or []:
        lines.append(f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:mailto:{email}")

    if description:
        lines.append(f"DESCRIPTION:{_ical_escape(description)}")
    if location:
        lines.append(f"LOCATION:{_ical_escape(location)}")

    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"


def build_ics_cancel(
    uid: str,
    summary: str,
    dtstart: datetime,
    dtend: datetime,
    organizer_email: str,
    organizer_name: str = "",
    attendees: Optional[List[str]] = None,
    sequence: int = 1,
) -> str:
    """Build an iCalendar VEVENT string (``METHOD:CANCEL``).

    ``uid`` must match the original event so the recipient's calendar removes it.

    ``sequence`` follows the same rule as :func:`build_ics_event`, and a cancellation is
    where getting it wrong is least visible: the hardcoded ``1`` this default preserves is
    *lower* than any revision a caller may have sent in between, so such a cancel is
    discarded and the appointment stays in the calendar for good. A caller that numbers its
    updates must number its cancellations from the same sequence.

    ``dtstart``/``dtend`` follow the same rule as :func:`build_ics_event` — and they must be
    given in the SAME form the original REQUEST used: a client matches the cancellation by
    ``UID``, but a human reading the message sees whatever these two say.
    """

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Cogno AI//Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:CANCEL",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_utc_stamp()}",
        _dt_property("DTSTART", dtstart),
        _dt_property("DTEND", dtend),
        f"SUMMARY:CANCELLED: {_ical_escape(summary)}",
        "STATUS:CANCELLED",
        f"SEQUENCE:{int(sequence)}",
    ]

    if organizer_name:
        lines.append(f"ORGANIZER;CN={_ical_escape(organizer_name)}:mailto:{organizer_email}")
    else:
        lines.append(f"ORGANIZER:mailto:{organizer_email}")

    for email in attendees or []:
        lines.append(f"ATTENDEE:mailto:{email}")

    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"
