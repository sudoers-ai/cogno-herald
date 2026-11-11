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
    """Escape special characters for an iCalendar TEXT property value (RFC 5545 §3.3.11).

    A backslash, ``;`` and ``,`` are escaped, and every newline form (``\\r\\n``, a lone
    ``\\r`` or ``\\n``) collapses to the literal ``\\n`` the standard uses — so a newline in
    free text (a multi-line DESCRIPTION) is PRESERVED as content and can never end the line
    early and inject a property. A raw CR is neutralised the same way, since none may survive
    into the wire. Identifier/address fields that are not TEXT (``UID``, a ``mailto:``) go
    through :func:`_reject_crlf` instead — there is nothing to escape a newline *to* there."""
    return (
        text.replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _reject_crlf(value: str, field: str) -> str:
    """Refuse a raw CR or LF in an identifier/address field (``UID``, an e-mail address).

    These are placed into the content line WITHOUT TEXT escaping — a UID and a ``mailto:``
    URI are not free text — so a newline in them is never content: it is an attempt to end
    the line and inject a property (``UID:x\\r\\nSUMMARY:evil`` or a forged ``ATTENDEE``).
    There is nothing to legitimately escape it to, so it is refused loudly rather than
    smuggled through. Returns the value unchanged when it is clean, to read inline."""
    if "\r" in value or "\n" in value:
        raise ValueError(
            f"{field} must not contain a CR or LF character (possible iCalendar injection)")
    return value


def _fold(line: str) -> str:
    """Fold one content line to at most 75 octets (RFC 5545 §3.1).

    A line longer than 75 octets is split and each continuation begins with a single space;
    an unfolding parser rejoins them by dropping the CRLF and that leading space. The split
    is made on a CHARACTER boundary so a multi-byte UTF-8 codepoint is never cut in half, and
    each physical line — the leading space included — stays within the 75-octet limit."""
    if len(line.encode("utf-8")) <= 75:
        return line
    pieces: List[bytes] = []
    cur = b""
    for ch in line:
        enc = ch.encode("utf-8")
        if len(cur) + len(enc) > 75:
            pieces.append(cur)
            cur = b" " + enc    # a continuation line opens with one space
        else:
            cur += enc
    pieces.append(cur)
    return "\r\n".join(p.decode("utf-8") for p in pieces)


def _render(lines: List[str]) -> str:
    """Join the content lines, folding each to the 75-octet limit, with the trailing CRLF."""
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


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
        f"UID:{_ical_escape(_reject_crlf(uid, 'uid'))}",
        f"DTSTAMP:{_utc_stamp()}",
        _dt_property("DTSTART", dtstart),
        _dt_property("DTEND", dtend),
        f"SUMMARY:{_ical_escape(summary)}",
        f"STATUS:{status}",
        f"SEQUENCE:{int(sequence)}",
    ]

    if organizer_name:
        lines.append(f"ORGANIZER;CN={_ical_escape(organizer_name)}:"
                     f"mailto:{_reject_crlf(organizer_email, 'organizer_email')}")
    else:
        lines.append(f"ORGANIZER:mailto:{_reject_crlf(organizer_email, 'organizer_email')}")

    for email in attendees or []:
        lines.append("ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:"
                     f"mailto:{_reject_crlf(email, 'attendee')}")

    if description:
        lines.append(f"DESCRIPTION:{_ical_escape(description)}")
    if location:
        lines.append(f"LOCATION:{_ical_escape(location)}")

    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return _render(lines)


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
        f"UID:{_ical_escape(_reject_crlf(uid, 'uid'))}",
        f"DTSTAMP:{_utc_stamp()}",
        _dt_property("DTSTART", dtstart),
        _dt_property("DTEND", dtend),
        f"SUMMARY:CANCELLED: {_ical_escape(summary)}",
        "STATUS:CANCELLED",
        f"SEQUENCE:{int(sequence)}",
    ]

    if organizer_name:
        lines.append(f"ORGANIZER;CN={_ical_escape(organizer_name)}:"
                     f"mailto:{_reject_crlf(organizer_email, 'organizer_email')}")
    else:
        lines.append(f"ORGANIZER:mailto:{_reject_crlf(organizer_email, 'organizer_email')}")

    for email in attendees or []:
        lines.append(f"ATTENDEE:mailto:{_reject_crlf(email, 'attendee')}")

    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return _render(lines)
