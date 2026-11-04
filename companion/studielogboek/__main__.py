"""Entry point.

    python -m studielogboek                 start the tray app
    python -m studielogboek export --week 10
    python -m studielogboek export --from 01-09-2026 --to 30-09-2026 [--out file.docx]
    python -m studielogboek export --all
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

from . import config


def setup_logging() -> None:
    config.ensure_dirs()
    handlers = [logging.FileHandler(config.LOG_PATH, encoding="utf-8")]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    level = logging.DEBUG if os.environ.get("STUDIELOGBOEK_DEBUG") else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def run_app() -> int:
    from . import hotkeys, server, winapi
    from .db import Database
    from .tracker import WindowTracker
    from .ui import UI

    winapi.make_dpi_aware()
    db = Database()
    app = server.Companion(db)
    try:
        httpd = server.start_server(app)
    except OSError:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f"{config.APP_NAME} draait al (poort {config.PORT} is in gebruik).", config.APP_NAME, 0x40)
        return 1

    tracker = WindowTracker(app)
    tracker.start()

    def quit_app():
        app.set_study_mode(False)
        tracker.stop()
        keys.stop()
        httpd.shutdown()
        if ui.tray:
            ui.tray.stop()
        ui.root.quit()

    ui = UI(app, tracker, quit_app)
    keys = hotkeys.HotkeyThread([
        (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, ord("N"), "Ctrl+Alt+N", ui.hotkey_note),
        (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, ord("S"), "Ctrl+Alt+S", ui.hotkey_screenshot),
    ])
    keys.start()
    keys.ready.wait(2)
    ui.start_tray()
    if keys.failed:
        ui.root.after(1500, lambda: ui.notify("Sneltoets bezet", ", ".join(keys.failed) + " is al in gebruik door een ander programma."))
    logging.getLogger(__name__).info("%s gestart, data in %s", config.APP_NAME, config.DATA_DIR)
    ui.mainloop()
    tracker.join(timeout=config.POLL_SECONDS + 2)
    return 0


def run_export(argv: list[str]) -> int:
    from . import export
    from .db import Database

    p = argparse.ArgumentParser(prog="studielogboek export")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--week", type=int)
    g.add_argument("--from", dest="start")
    g.add_argument("--all", action="store_true")
    p.add_argument("--to", dest="end")
    p.add_argument("--out")
    a = p.parse_args(argv)
    db = Database()
    parse = lambda s: dt.datetime.strptime(s, "%d-%m-%Y").date()
    if a.week:
        path = export.export_week(db, a.week, a.out)
    elif a.start:
        path = export.export_range(db, parse(a.start), parse(a.end) if a.end else dt.date.today(), a.out)
    else:
        path = export.export_all(db, a.out)
    print(path)
    return 0


def main() -> int:
    setup_logging()
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        return run_export(sys.argv[2:])
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
