"""Paths and constants for the Studielogboek companion."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

APP_NAME = "Studielogboek"

# Port the Firefox extension talks to. 5175 is taken by the MCreator MCP plugin.
HOST = "127.0.0.1"
PORT = 5176

# Everything lives in Documents\Studielogboek so it is easy to find and back up.
DATA_DIR = Path(os.environ.get("STUDIELOGBOEK_DATA", Path.home() / "Documents" / APP_NAME))
DB_PATH = DATA_DIR / "logboek.db"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
EXPORT_DIR = DATA_DIR / "exports"
LOG_PATH = DATA_DIR / "companion.log"

# Internship week 1 starts on this Monday.
INTERNSHIP_START = dt.date(2026, 8, 31)

# Window tracker
POLL_SECONDS = 5
IDLE_PAUSE_SECONDS = 5 * 60  # no keyboard/mouse input for this long pauses app tracking
MIN_SEGMENT_SECONDS = 10

# Literature list: which sources are worth citing
CITE_MIN_PAGE_SECONDS = 60
CITE_MIN_VIDEO_SECONDS = 60
CITE_MIN_PDF_SECONDS = 30

NOTE_TYPES = ["Geleerd", "Bevinding", "Vraag-ToDo", "Gesprek begeleider", "Feedback"]
COMPETENCIES = [
    "Manage & Control",
    "Analyseren",
    "Adviseren",
    "Ontwerpen",
    "Realiseren",
    "Professional skills",
]

DEFAULT_EXCLUDED_DOMAINS = [
    "mail.google.com",
    "outlook.live.com",
    "web.whatsapp.com",
    "facebook.com",
    "instagram.com",
    "netflix.com",
    "bank",  # any host containing "bank"
]
DEFAULT_EXCLUDED_APPS = ["WhatsApp.exe", "Discord.exe", "Spotify.exe", "KeePass.exe", "1Password.exe", "Bitwarden.exe"]


def ensure_dirs() -> None:
    for d in (DATA_DIR, SCREENSHOT_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def internship_week(day: dt.date) -> int:
    return (day - INTERNSHIP_START).days // 7 + 1


def week_range(week: int) -> tuple[dt.date, dt.date]:
    start = INTERNSHIP_START + dt.timedelta(days=(week - 1) * 7)
    return start, start + dt.timedelta(days=6)
