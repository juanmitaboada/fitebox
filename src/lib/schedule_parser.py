#!/usr/bin/env python3
"""
FITEBOX Schedule Parser
Parses Frab/Pentabarf schedule.xml and provides session lookup.
Future formats (CSV, JSON) can be added here.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional


def parse_schedule(xml_path: str) -> dict:
    """
    Parse a Frab/Pentabarf schedule.xml into a structured dict.

    Returns:
        {
            "conference": {"title": ..., "start": ..., "end": ...},
            "days": [
                {
                    "index": 1,
                    "date": "2026-06-07",
                    "rooms": {
                        "Sala A": [
                            {
                                "event_id": "42",
                                "title": "Talk Title",
                                "author": "John Doe",
                                "description": "...",
                                "room": "Sala A",
                                "date": "2026-06-07",
                                "start": "10:00",
                                "end": "10:45",
                                "duration": "00:45",
                                "track": "DevOps",
                                "language": "es",
                                "type": "Talk",
                                "slug": "talk-title",
                                "url": "https://..."
                            },
                            ...
                        ]
                    }
                }
            ]
        }
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Conference info
    conf_el = root.find("conference")
    conference = {}
    if conf_el is not None:
        conference = {
            "title": _text(conf_el, "title"),
            "start": _text(conf_el, "start"),
            "end": _text(conf_el, "end"),
            "days": _text(conf_el, "days"),
        }

    # Days
    days = []
    for day_el in root.findall("day"):
        day_data = {
            "index": int(day_el.get("index", "0")),
            "date": day_el.get("date", ""),
            "rooms": {},
        }

        for room_el in day_el.findall("room"):
            room_name = room_el.get("name", "Unknown")
            events = []

            for event_el in room_el.findall("event"):
                event = _parse_event(event_el, room_name, day_data["date"])
                events.append(event)

            # Sort by start time
            events.sort(key=lambda e: e["start"])
            day_data["rooms"][room_name] = events

        days.append(day_data)

    return {"conference": conference, "days": days}


def get_rooms(xml_path: str) -> list:
    """
    Extract unique room names from schedule.

    Returns: sorted list of room name strings
    """
    schedule = parse_schedule(xml_path)
    rooms = set()
    for day in schedule["days"]:
        rooms.update(day["rooms"].keys())
    return sorted(rooms)


def find_current_session(
    xml_path: str, room: str, dt: datetime = None, offset_minutes: int = 15
) -> Optional[dict]:
    """
    Find the session happening at dt + offset_minutes in the given room.

    Args:
        xml_path: Path to schedule.xml
        room: Room name to filter
        dt: Reference datetime (default: now)
        offset_minutes: Minutes to add for setup buffer

    Returns: session dict or None
    """
    _, current, _ = find_adjacent_sessions(xml_path, room, dt, offset_minutes)
    return current


def find_adjacent_sessions(
    xml_path: str, room: str, dt: datetime = None, offset_minutes: int = 15
) -> tuple:
    """
    Find previous, current, and next sessions for a room at a given time.

    Args:
        xml_path: Path to schedule.xml
        room: Room name to filter
        dt: Reference datetime (default: now)
        offset_minutes: Minutes to add for setup buffer

    Returns: (previous, current, next) - each is a session dict or None
    """
    if dt is None:
        dt = datetime.now()

    target_time = dt + timedelta(minutes=offset_minutes)
    target_date = target_time.strftime("%Y-%m-%d")

    schedule = parse_schedule(xml_path)

    # Collect all events for this room on the target date
    events = []
    for day in schedule["days"]:
        if day["date"] == target_date:
            events.extend(day["rooms"].get(room, []))

    # Also check adjacent days in case of edge cases (late night)
    if not events:
        for day in schedule["days"]:
            events.extend(day["rooms"].get(room, []))

    if not events:
        return (None, None, None)

    # Sort by start time
    events.sort(key=lambda e: e["start"])

    # Filter to target date
    day_events = [e for e in events if e["date"] == target_date]
    if not day_events:
        day_events = events  # fallback to all events

    target_hhmm = target_time.strftime("%H:%M")

    prev_session = None
    current_session = None
    next_session = None

    for i, event in enumerate(day_events):
        start = event["start"]
        end = event["end"]

        if start <= target_hhmm < end:
            # We're inside this event's time slot
            current_session = event
            prev_session = day_events[i - 1] if i > 0 else None
            next_session = (
                day_events[i + 1] if i < len(day_events) - 1 else None
            )
            return (prev_session, current_session, next_session)

        if start > target_hhmm:
            # The target time is before this event, so this is "next"
            next_session = event
            prev_session = day_events[i - 1] if i > 0 else None
            # No current session (we're between sessions)
            return (prev_session, None, next_session)

    # If we got here, target time is after all events
    if day_events:
        prev_session = day_events[-1]

    return (prev_session, None, None)


# === Internal Helpers ===


def _text(parent, tag: str, default: str = "") -> str:
    """Safely get text content of a child element."""
    el = parent.find(tag)
    return el.text.strip() if el is not None and el.text else default


def _parse_event(event_el, room_name: str, day_date: str) -> dict:
    """Parse a single <event> element into a session dict."""
    # Persons / authors
    persons = []
    persons_el = event_el.find("persons")
    if persons_el is not None:
        for person_el in persons_el.findall("person"):
            if person_el.text:
                persons.append(person_el.text.strip())
    author = ", ".join(persons)

    # Start time
    start = _text(event_el, "start")  # "10:00"

    # Duration → end time
    duration = _text(event_el, "duration")  # "00:45"
    end = _calc_end_time(start, duration)

    # Description: try 'description' first, fallback to 'abstract'
    description = _text(event_el, "description")
    if not description:
        description = _text(event_el, "abstract")

    return {
        "event_id": event_el.get("id", ""),
        "title": _text(event_el, "title"),
        "author": author,
        "description": description,
        "room": room_name,
        "date": day_date,
        "start": start,
        "end": end,
        "duration": duration,
        "track": _text(event_el, "track"),
        "language": _text(event_el, "language"),
        "type": _text(event_el, "type"),
        "slug": _text(event_el, "slug"),
        "url": _text(event_el, "url"),
    }


def _calc_end_time(start: str, duration: str) -> str:
    """Calculate end time from start + duration (both HH:MM)."""
    try:
        sh, sm = map(int, start.split(":"))
        dh, dm = map(int, duration.split(":"))
        total_min = sh * 60 + sm + dh * 60 + dm
        return f"{(total_min // 60) % 24:02d}:{total_min % 60:02d}"
    except Exception:
        return start


# === CLI for testing ===

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: schedule_parser.py <schedule.xml> [room] [HH:MM]")
        sys.exit(1)

    xml_path = sys.argv[1]

    print("=== Rooms ===")
    for r in get_rooms(xml_path):
        print(f"  {r}")

    if len(sys.argv) >= 3:
        room = sys.argv[2]
        dt = datetime.now()
        if len(sys.argv) >= 4:
            h, m = map(int, sys.argv[3].split(":"))
            dt = dt.replace(hour=h, minute=m)

        print(
            f"\n=== Sessions in '{room}' at {dt.strftime('%H:%M')} (+15min) ==="
        )
        prev_s, cur_s, next_s = find_adjacent_sessions(xml_path, room, dt)

        for label, s in [
            ("PREV", prev_s),
            ("CURRENT", cur_s),
            ("NEXT", next_s),
        ]:
            if s:
                print(
                    f"  {label}: {s['start']}-{s['end']} {s['author']} - {s['title']}"
                )
            else:
                print(f"  {label}: (none)")
