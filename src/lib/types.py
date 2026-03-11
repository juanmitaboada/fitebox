"""
FITEBOX shared type definitions.

TypedDicts used across multiple modules live here to avoid
cross-imports between fitebox_manager, oled_controller, and
schedule_parser.
"""

from typing_extensions import TypedDict


class Session(TypedDict):
    """A single talk/event from a Frab/Pentabarf schedule."""

    event_id: str
    title: str
    author: str
    description: str
    room: str
    date: str
    start: str
    end: str
    duration: str
    track: str
    language: str
    type: str
    slug: str
    url: str
    updated_at: str


class KnownNetwork(TypedDict):
    """A saved WiFi connection from NetworkManager."""

    name: str
    autoconnect: bool


class Recording(TypedDict):
    """A recording file entry for status broadcasts."""

    name: str
    size_mb: float
