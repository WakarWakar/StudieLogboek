"""Tk windows (quick note, screenshot, export, settings) and the tray icon."""
from __future__ import annotations

import datetime as dt
import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from PIL import Image, ImageDraw, ImageEnhance, ImageGrab, ImageTk

from . import config, winapi
from .server import Companion

log = logging.getLogger(__name__)

PAD = {"padx": 10, "pady": 4}


def _date_nl(text: str) -> dt.date:
    return dt.datetime.strptime(text.strip(), "%d-%m-%Y").date()


class UI:
    def __init__(self, app: Companion, tracker, on_quit: Callable[[], None]):
        self.app = app
        self.tracker = tracker
        self.on_quit = on_quit
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(config.APP_NAME)
        self._calls: queue.Queue = queue.Queue()
        self._busy = False  # one popup at a time
        self.tray = None
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
        self.root.after(100, self._pump)

    # ---- threading helpers -------------------------------------------------------------
    def call(self, fn: Callable, *args) -> None:
        """Run fn on the Tk thread (safe to call from any thread)."""
        self._calls.put((fn, args))

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self._calls.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    log.exception("UI call failed")
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def mainloop(self) -> None:
        self.root.mainloop()

    @staticmethod
    def _bring_to_front(win: tk.Toplevel) -> None:
        win.attributes("-topmost", True)
        win.deiconify()
        win.lift()
        win.update_idletasks()
        try:
            hwnd = int(win.wm_frame(), 16)
            winapi.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        win.focus_force()

    # ---- hotkey entry points (called on the hotkey thread) ---------------------------------
    def hotkey_note(self) -> None:
        ctx = self.tracker.context_now()
        self.call(self.quick_note, ctx)

    def hotkey_screenshot(self) -> None:
        ctx = self.tracker.context_now()
        try:
            img = ImageGrab.grab(all_screens=True)
        except Exception:
            log.exception("screen grab failed")
            return
        self.call(self.screenshot_select, img, ctx)

    # ---- quick note -------------------------------------------------------------------
    def quick_note(self, ctx: dict) -> None:
        if self._busy:
            return
        self._busy = True
        win = tk.Toplevel(self.root)
        win.title("Snelle notitie - Studielogboek")
        win.resizable(False, False)

        where = " - ".join(x for x in (ctx.get("app"), ctx.get("title")) if x) or "geen venster"
        ttk.Label(win, text=f"Bij: {where[:110]}", foreground="#666").grid(row=0, column=0, sticky="w", **PAD)

        type_var = tk.StringVar(value=config.NOTE_TYPES[0])
        types = ttk.Frame(win)
        types.grid(row=1, column=0, sticky="w", **PAD)
        for t in config.NOTE_TYPES:
            ttk.Radiobutton(types, text=t, value=t, variable=type_var).pack(side="left", padx=(0, 10))

        text = tk.Text(win, width=72, height=7, wrap="word", font=("Segoe UI", 10), undo=True)
        text.grid(row=2, column=0, **PAD)

        tags_frame = ttk.LabelFrame(win, text="Competenties (optioneel)")
        tags_frame.grid(row=3, column=0, sticky="we", **PAD)
        tag_vars = {}
        for i, c in enumerate(config.COMPETENCIES):
            v = tk.BooleanVar(value=False)
            tag_vars[c] = v
            ttk.Checkbutton(tags_frame, text=c, variable=v).grid(row=i // 3, column=i % 3, sticky="w", padx=8, pady=2)

        hint = ttk.Label(win, text="Enter = opslaan  ·  Shift+Enter = nieuwe regel  ·  Esc = annuleren", foreground="#888")
        hint.grid(row=4, column=0, sticky="w", **PAD)

        def close(_=None):
            self._busy = False
            win.destroy()

        def save(_=None):
            body = text.get("1.0", "end").strip()
            if not body:
                close()
                return "break"
            tags = [c for c, v in tag_vars.items() if v.get()]
            self.app.db.add_note(type_var.get(), body, tags, ctx)
            log.info("Notitie opgeslagen (%s)", type_var.get())
            self.notify("Notitie opgeslagen", f"{type_var.get()}: {body[:60]}")
            close()
            return "break"

        btns = ttk.Frame(win)
        btns.grid(row=5, column=0, sticky="e", **PAD)
        ttk.Button(btns, text="Annuleren", command=close).pack(side="right", padx=4)
        ttk.Button(btns, text="Opslaan", command=save).pack(side="right", padx=4)

        text.bind("<Return>", save)
        text.bind("<Shift-Return>", lambda e: None)  # default: newline
        win.bind("<Escape>", close)
        win.protocol("WM_DELETE_WINDOW", close)
        self._bring_to_front(win)
        text.focus_set()

    # ---- screenshot ---------------------------------------------------------------------
    def screenshot_select(self, img: Image.Image, ctx: dict) -> None:
        if self._busy:
            return
        self._busy = True
        vx, vy, vw, vh = winapi.virtual_screen()
        if img.size != (vw, vh):
            vw, vh = img.size
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.geometry(f"{vw}x{vh}{vx:+d}{vy:+d}")
        dim = ImageEnhance.Brightness(img).enhance(0.55)
        photo = ImageTk.PhotoImage(dim)
        canvas = tk.Canvas(overlay, width=vw, height=vh, highlightthickness=0, cursor="crosshair")
        canvas.pack()
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.create_text(vw // 2, 30, text="Sleep om een gebied te kiezen  ·  klik = heel scherm  ·  Esc = annuleren",
                           fill="white", font=("Segoe UI", 14, "bold"))
        canvas.photo = photo  # keep a reference
        state = {"x0": 0, "y0": 0, "rect": None}

        def cancel(_=None):
            self._busy = False
            overlay.destroy()

        def press(e):
            state["x0"], state["y0"] = e.x, e.y
            state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#4da3ff", width=2)

        def drag(e):
            if state["rect"]:
                canvas.coords(state["rect"], state["x0"], state["y0"], e.x, e.y)

        def release(e):
            x0, x1 = sorted((state["x0"], e.x))
            y0, y1 = sorted((state["y0"], e.y))
            overlay.destroy()
            crop = img if (x1 - x0 < 8 or y1 - y0 < 8) else img.crop((x0, y0, x1, y1))
            self._busy = False
            self.screenshot_caption(crop, ctx)

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        overlay.bind("<Escape>", cancel)
        self._bring_to_front(overlay)
        canvas.focus_set()

    def screenshot_caption(self, img: Image.Image, ctx: dict) -> None:
        self._busy = True
        win = tk.Toplevel(self.root)
        win.title("Screenshot opslaan - Studielogboek")
        win.resizable(False, False)
        thumb = img.copy()
        thumb.thumbnail((560, 320))
        photo = ImageTk.PhotoImage(thumb)
        lbl = ttk.Label(win, image=photo)
        lbl.image = photo
        lbl.grid(row=0, column=0, **PAD)
        where = " - ".join(x for x in (ctx.get("app"), ctx.get("title")) if x) or ""
        if where:
            ttk.Label(win, text=f"Bij: {where[:100]}", foreground="#666").grid(row=1, column=0, sticky="w", **PAD)
        ttk.Label(win, text="Bijschrift (wat laat dit zien?):").grid(row=2, column=0, sticky="w", **PAD)
        caption = ttk.Entry(win, width=80)
        caption.grid(row=3, column=0, **PAD)

        tags_frame = ttk.LabelFrame(win, text="Competenties (optioneel)")
        tags_frame.grid(row=4, column=0, sticky="we", **PAD)
        tag_vars = {}
        for i, c in enumerate(config.COMPETENCIES):
            v = tk.BooleanVar(value=False)
            tag_vars[c] = v
            ttk.Checkbutton(tags_frame, text=c, variable=v).grid(row=i // 3, column=i % 3, sticky="w", padx=8, pady=2)

        def close(_=None):
            self._busy = False
            win.destroy()

        def save(_=None):
            config.ensure_dirs()
            name = time.strftime("%Y-%m-%d_%H%M%S") + ".png"
            path = config.SCREENSHOT_DIR / name
            img.save(path, "PNG")
            tags = [c for c, v in tag_vars.items() if v.get()]
            self.app.db.add_screenshot(str(path), caption.get().strip(), tags, ctx)
            log.info("Screenshot opgeslagen: %s", path)
            self.notify("Screenshot opgeslagen", caption.get().strip() or name)
            close()

        btns = ttk.Frame(win)
        btns.grid(row=5, column=0, sticky="e", **PAD)
        ttk.Button(btns, text="Annuleren", command=close).pack(side="right", padx=4)
        ttk.Button(btns, text="Opslaan", command=save).pack(side="right", padx=4)
        caption.bind("<Return>", save)
        win.bind("<Escape>", close)
        win.protocol("WM_DELETE_WINDOW", close)
        self._bring_to_front(win)
        caption.focus_set()

    # ---- export ------------------------------------------------------------------------
    def export_dialog(self) -> None:
        from . import export

        win = tk.Toplevel(self.root)
        win.title("Exporteren naar Word - Studielogboek")
        win.resizable(False, False)
        today = dt.date.today()
        this_week = max(1, config.internship_week(today))
        mode = tk.StringVar(value="week")

        ttk.Radiobutton(win, text="Eén stageweek:", value="week", variable=mode).grid(row=0, column=0, sticky="w", **PAD)
        week = ttk.Spinbox(win, from_=1, to=60, width=5)
        week.set(this_week)
        week.grid(row=0, column=1, sticky="w", **PAD)

        ttk.Radiobutton(win, text="Periode van:", value="range", variable=mode).grid(row=1, column=0, sticky="w", **PAD)
        start_e = ttk.Entry(win, width=12)
        start_e.insert(0, config.week_range(this_week)[0].strftime("%d-%m-%Y"))
        start_e.grid(row=1, column=1, sticky="w", **PAD)
        ttk.Label(win, text="tot en met:").grid(row=1, column=2, sticky="w", **PAD)
        end_e = ttk.Entry(win, width=12)
        end_e.insert(0, today.strftime("%d-%m-%Y"))
        end_e.grid(row=1, column=3, sticky="w", **PAD)

        ttk.Radiobutton(win, text="Alles", value="all", variable=mode).grid(row=2, column=0, sticky="w", **PAD)
        ttk.Label(win, text="Datums als dd-mm-jjjj", foreground="#888").grid(row=3, column=0, columnspan=4, sticky="w", **PAD)

        def run():
            try:
                if mode.get() == "week":
                    path = export.export_week(self.app.db, int(week.get()))
                elif mode.get() == "range":
                    path = export.export_range(self.app.db, _date_nl(start_e.get()), _date_nl(end_e.get()))
                else:
                    path = export.export_all(self.app.db)
            except ValueError as e:
                messagebox.showerror("Exporteren", f"Ongeldige invoer: {e}", parent=win)
                return
            except Exception as e:
                log.exception("export failed")
                messagebox.showerror("Exporteren", f"Exporteren mislukt: {e}", parent=win)
                return
            win.destroy()
            try:
                os.startfile(path)
            except OSError:
                messagebox.showinfo("Exporteren", f"Opgeslagen als:\n{path}")

        btns = ttk.Frame(win)
        btns.grid(row=4, column=0, columnspan=4, sticky="e", **PAD)
        ttk.Button(btns, text="Annuleren", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Exporteren", command=run).pack(side="right", padx=4)
        self._bring_to_front(win)

    # ---- settings -----------------------------------------------------------------------
    def settings_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Instellingen - Studielogboek")
        ttk.Label(win, text="Websites die nooit worden opgeslagen (één per regel, deel van de domeinnaam):").grid(
            row=0, column=0, sticky="w", **PAD)
        domains = tk.Text(win, width=60, height=10)
        domains.insert("1.0", "\n".join(self.app.db.excluded_domains()))
        domains.grid(row=1, column=0, **PAD)
        ttk.Label(win, text="Programma's die nooit worden opgeslagen (bestandsnaam, bijv. WhatsApp.exe):").grid(
            row=2, column=0, sticky="w", **PAD)
        apps = tk.Text(win, width=60, height=6)
        apps.insert("1.0", "\n".join(self.app.db.excluded_apps()))
        apps.grid(row=3, column=0, **PAD)

        def save():
            lines = lambda w: [l.strip() for l in w.get("1.0", "end").splitlines() if l.strip()]
            self.app.db.set_setting("excluded_domains", lines(domains))
            self.app.db.set_setting("excluded_apps", lines(apps))
            win.destroy()

        btns = ttk.Frame(win)
        btns.grid(row=4, column=0, sticky="e", **PAD)
        ttk.Button(btns, text="Annuleren", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Opslaan", command=save).pack(side="right", padx=4)
        self._bring_to_front(win)

    # ---- tray --------------------------------------------------------------------------
    @staticmethod
    def tray_image(on: bool) -> Image.Image:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        fill = (46, 160, 67, 255) if on else (130, 130, 130, 255)
        d.rounded_rectangle((6, 8, 58, 56), radius=8, fill=fill)
        d.rectangle((30, 12, 33, 52), fill=(255, 255, 255, 230))  # book spine
        for y in (20, 28, 36):
            d.line((12, y, 26, y), fill=(255, 255, 255, 200), width=3)
            d.line((37, y, 52, y), fill=(255, 255, 255, 200), width=3)
        return img

    def start_tray(self) -> None:
        import pystray

        def toggle(icon, item):
            self.app.set_study_mode(not self.app.db.study_mode())

        menu = pystray.Menu(
            pystray.MenuItem("Studiemodus", toggle, checked=lambda item: self.app.db.study_mode(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Snelle notitie  (Ctrl+Alt+N)", lambda: self.call(self.quick_note, {"app": None, "title": None, "url": None})),
            # short delay so the tray menu has closed before the screen is captured
            pystray.MenuItem("Screenshot  (Ctrl+Alt+S)", lambda: threading.Timer(0.5, self.hotkey_screenshot).start()),
            pystray.MenuItem("Exporteren naar Word...", lambda: self.call(self.export_dialog)),
            pystray.MenuItem("Map openen", lambda: os.startfile(config.DATA_DIR)),
            pystray.MenuItem("Instellingen...", lambda: self.call(self.settings_dialog)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Afsluiten", lambda: self.call(self.on_quit)),
        )
        on = self.app.db.study_mode()
        self.tray = pystray.Icon(config.APP_NAME, self.tray_image(on), self._tray_title(on), menu)
        self.app.listeners.append(self._update_tray)
        self.tray.run_detached()

    @staticmethod
    def _tray_title(on: bool) -> str:
        return f"{config.APP_NAME} - studiemodus {'AAN' if on else 'uit'}"

    def _update_tray(self, on: bool) -> None:
        if self.tray:
            self.tray.icon = self.tray_image(on)
            self.tray.title = self._tray_title(on)
            self.tray.update_menu()

    def notify(self, title: str, message: str) -> None:
        try:
            if self.tray and getattr(self.tray, "HAS_NOTIFICATION", True):
                self.tray.notify(message, title)
        except Exception:
            pass
