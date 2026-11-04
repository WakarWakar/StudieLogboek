"""Records which app is in the foreground while Study Mode is on."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from urllib.parse import unquote, urlsplit

from . import config, winapi
from .server import Companion

log = logging.getLogger(__name__)

OFFICE = {"winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "onenote.exe": "OneNote"}

# Remote / cloud desktop clients. We only ever see the window title (session name), never the remote screen.
REMOTE = {
    "mstsc.exe": "Extern bureaublad",
    "msrdc.exe": "Extern bureaublad",
    "msrdcw.exe": "Extern bureaublad",
    "windows365.exe": "Windows App",
    "windowsapp.exe": "Windows App",
    "vmware-view.exe": "VMware Horizon",
    "wfica32.exe": "Citrix Workspace",
    "cdviewer.exe": "Citrix Workspace",
    "anydesk.exe": "AnyDesk",
    "teamviewer.exe": "TeamViewer",
    "parsecd.exe": "Parsec",
}

IGNORED_EXES = {
    "firefox.exe",  # the extension records Firefox itself
    "lockapp.exe", "searchhost.exe", "searchapp.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "textinputhost.exe", "applicationframehost.exe",
}
IGNORED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "#32768"}

OFFICE_SUFFIX = re.compile(r"\s+-\s+(Word|Excel|PowerPoint|OneNote)(\s+\(.*\))?$")


def _explorer_path(hwnd: int) -> str | None:
    """Folder shown in a File Explorer window, via the Shell COM API."""
    try:
        import win32com.client

        for w in win32com.client.Dispatch("Shell.Application").Windows():
            try:
                if int(w.HWND) != int(hwnd):
                    continue
                try:
                    return str(w.Document.Folder.Self.Path)
                except Exception:
                    loc = str(w.LocationURL or "")
                    if loc.startswith("file:///"):
                        return unquote(urlsplit(loc).path.lstrip("/")).replace("/", "\\")
                    return loc or None
            except Exception:
                continue
    except Exception:
        log.debug("explorer path lookup failed", exc_info=True)
    return None


def describe_window(hwnd: int | None, excluded_apps: list[str] | None = None) -> dict | None:
    """Turn a window into {app, title, exe, kind} or None when it should not be logged."""
    if not hwnd:
        return None
    pid = winapi.window_pid(hwnd)
    if pid == os.getpid():
        return None
    exe = winapi.process_exe(pid)
    exe_l = exe.lower()
    cls = winapi.window_class(hwnd)
    title = winapi.window_title(hwnd).strip()
    # Firefox by window class too: its process can report a staged-update name like "moz4eae181e-..."
    if exe_l == "firefox.exe" or cls == "MozillaWindowClass":
        return {"app": "Firefox", "title": title, "exe": exe, "kind": "browser"}
    if exe_l in IGNORED_EXES or cls in IGNORED_CLASSES:
        return None
    if excluded_apps and exe_l in {a.lower() for a in excluded_apps}:
        return None

    if exe_l in OFFICE:
        doc = OFFICE_SUFFIX.sub("", title) or title
        return {"app": OFFICE[exe_l], "title": doc, "exe": exe, "kind": "office"}
    if exe_l == "explorer.exe":
        if cls != "CabinetWClass":
            return None
        path = _explorer_path(hwnd) or title
        return {"app": "Verkenner", "title": path, "exe": exe, "kind": "explorer"}
    if exe_l in REMOTE:
        return {"app": REMOTE[exe_l], "title": title or "(sessie)", "exe": exe, "kind": "remote"}
    if not title:
        return None
    name = exe.rsplit(".", 1)[0] if exe else "Onbekend"
    return {"app": name, "title": title, "exe": exe, "kind": "other"}


class WindowTracker(threading.Thread):
    def __init__(self, app: Companion):
        super().__init__(name="tracker", daemon=True)
        self.app = app
        self.stop_event = threading.Event()
        self.current: dict | None = None  # {sig, app, title, exe, kind, start, last}

    # Called from the hotkey handler, before our own popup takes focus.
    def context_now(self) -> dict:
        info = describe_window(winapi.foreground_window())
        if info and info["kind"] == "browser":
            page = self.app.active_page or {}
            return {"app": "Firefox", "title": page.get("title") or info["title"], "url": page.get("url")}
        if info:
            return {"app": info["app"], "title": info["title"], "url": None}
        return {"app": None, "title": None, "url": None}

    def _flush(self, final: bool) -> None:
        seg = self.current
        if not seg:
            return
        seconds = seg["last"] - seg["start"]
        if seconds >= config.MIN_SEGMENT_SECONDS:
            self.app.db.upsert_event({
                "key": f"win-{seg['start']:.3f}",
                "kind": "app",
                "source": "windows",
                "start": seg["start"],
                "end": seg["last"],
                "seconds": seconds,
                "title": seg["title"],
                "app": seg["app"],
                "meta": {"exe": seg["exe"], "appKind": seg["kind"]},
            })
        if final:
            self.current = None

    def run(self) -> None:
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        last_write = 0.0
        while not self.stop_event.wait(config.POLL_SECONDS):
            try:
                now = time.time()
                if not self.app.db.study_mode():
                    self._flush(final=True)
                    continue
                idle = winapi.idle_seconds()
                if idle >= config.IDLE_PAUSE_SECONDS:
                    if self.current:
                        self.current["last"] = min(self.current["last"], now - idle)
                    self._flush(final=True)
                    continue
                info = describe_window(winapi.foreground_window(), self.app.db.excluded_apps())
                if not info or info["kind"] == "browser":
                    self._flush(final=True)
                    continue
                sig = (info["app"], info["title"])
                if self.current and self.current["sig"] == sig:
                    self.current["last"] = now
                    if now - last_write > 30:
                        self._flush(final=False)
                        last_write = now
                else:
                    if self.current:
                        self.current["last"] = now
                    self._flush(final=True)
                    self.current = {"sig": sig, "start": now, "last": now, **info}
            except Exception:
                log.exception("tracker poll failed")
        self._flush(final=True)

    def stop(self) -> None:
        self.stop_event.set()
