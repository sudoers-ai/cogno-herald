"""
cogno_herald.ical — iCalendar (.ics) builders.

Pure, stdlib-only RFC 5545 VEVENT generation (REQUEST / CANCEL) that works with
Google Calendar, Outlook, Apple Calendar, etc. Ported from the parent cogno's
``core/email.py``.
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
    """
    dt_start = dtstart.strftime("%Y%m%dT%H%M%S")
    dt_end = dtend.strftime("%Y%m%dT%H%M%S")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Cogno AI//Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_utc_stamp()}",
        f"DTSTART:{dt_start}",
        f"DTEND:{dt_end}",
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
    """
    dt_start = dtstart.strftime("%Y%m%dT%H%M%S")
    dt_end = dtend.strftime("%Y%m%dT%H%M%S")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Cogno AI//Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:CANCEL",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_utc_stamp()}",
        f"DTSTART:{dt_start}",
        f"DTEND:{dt_end}",
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
