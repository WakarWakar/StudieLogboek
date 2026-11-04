"""What you are doing inside Word, PowerPoint and Excel, read through their COM automation.

Only structure is read: the heading your cursor is under, the slide you are on and its title,
the sheet name, and word/slide counts. The text you write is never read or stored.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

WD_GOTO_HEADING, WD_GOTO_PREVIOUS, WD_STAT_WORDS = 11, 3, 0
WORD_COUNT_EVERY = 60  # seconds; counting a long document takes a few hundred ms


def _active(progid: str):
    import pythoncom
    import win32com.client
    try:
        obj = pythoncom.GetActiveObject(progid)
    except pythoncom.com_error:
        return None
    return win32com.client.Dispatch(obj.QueryInterface(pythoncom.IID_IDispatch))


def _matches(window_title: str, doc_name: str) -> bool:
    """Window titles show the name with or without extension ("Verslag.docx - Word" / "Verslag - Word")."""
    stem = doc_name.rsplit(".", 1)[0] if "." in doc_name else doc_name
    # "Presentatie1" must not match "Presentatie10", so only an exact name or name + space counts
    return window_title in (doc_name, stem) or window_title.startswith((doc_name + " ", stem + " "))


def _clean(text: str | None, limit: int = 100) -> str:
    text = (text or "").replace("\r", " ").replace("\x07", " ").replace("\x0b", " ").strip()
    return text[:limit]


class OfficeReader:
    """Called by the window tracker for the app in front. Every call is best effort:
    Office refuses COM calls while a dialog is open or you are typing fast, and then we just skip."""

    def __init__(self):
        self._words: dict[str, tuple[float, int]] = {}  # doc name -> (when counted, count)

    def details(self, app: str, window_title: str) -> dict | None:
        try:
            if app == "Word":
                return self._word(window_title)
            if app == "PowerPoint":
                return self._powerpoint(window_title)
            if app == "Excel":
                return self._excel(window_title)
        except Exception:  # pythoncom.com_error and friends: Office busy or no document
            log.debug("office details failed for %s", app, exc_info=True)
        return None

    def _word(self, window_title: str) -> dict | None:
        word = _active("Word.Application")
        if word is None or word.Documents.Count == 0:
            return None
        # the document whose window is in front (not necessarily Word's "active" one)
        doc = next((d for d in word.Documents if _matches(window_title, d.Name)), None)
        if doc is None:
            return None
        name = doc.Name
        sel = doc.ActiveWindow.Selection
        heading = ""
        try:
            heading = _clean(doc.Range(sel.Start, sel.Start).GoTo(WD_GOTO_HEADING, WD_GOTO_PREVIOUS).Paragraphs(1).Range.Text)
        except Exception:
            pass
        now = time.time()
        counted = self._words.get(name)
        if not counted or now - counted[0] > WORD_COUNT_EVERY:
            counted = (now, int(doc.ComputeStatistics(WD_STAT_WORDS)))
            self._words[name] = counted
        return {"doc": name, "part": heading or "(begin van het document)", "words": counted[1]}

    def _powerpoint(self, window_title: str) -> dict | None:
        ppt = _active("PowerPoint.Application")
        if ppt is None or ppt.Presentations.Count == 0:
            return None
        win = next((w for w in ppt.Windows if _matches(window_title, w.Presentation.Name)), None)
        if win is None:
            return None
        pres = win.Presentation
        name = pres.Name
        slide = win.View.Slide
        title = ""
        try:
            if slide.Shapes.HasTitle:
                title = _clean(slide.Shapes.Title.TextFrame.TextRange.Text, 80)
        except Exception:
            pass
        part = f"dia {slide.SlideIndex}" + (f" ‘{title}’" if title else "")
        # key on the slide number, so a title you are still typing doesn't become a separate entry
        return {"doc": name, "part": part, "key": f"dia {slide.SlideIndex}", "slides": int(pres.Slides.Count)}

    def _excel(self, window_title: str) -> dict | None:
        xl = _active("Excel.Application")
        if xl is None or xl.Workbooks.Count == 0:
            return None
        book = next((b for b in xl.Workbooks if _matches(window_title, b.Name)), None)
        if book is None:
            return None
        return {"doc": book.Name, "part": f"blad ‘{_clean(book.ActiveSheet.Name, 60)}’"}


def describe(app: str, meta: dict) -> str:
    """Readable Dutch line for the logbook, e.g.
    "schreef in ‘5.1.2 Leerproces’ (12 min), ‘6. AI-gebruik’ (3 min) · +240 woorden"."""
    from .export import dur  # local import: export imports this module too

    parts = meta.get("parts") or []
    if not parts:
        return ""
    parts = sorted(parts, key=lambda p: -p[1])
    shown = ", ".join(f"{p[0] if p[0].startswith(('dia ', 'blad ')) else chr(0x2018) + p[0] + chr(0x2019)} ({dur(p[1])})"
                      for p in parts[:4])
    more = f" en {len(parts) - 4} andere" if len(parts) > 4 else ""
    extra = ""
    verb = "werkte in" if app != "PowerPoint" else "werkte aan"
    if "words_start" in meta and "words_end" in meta:
        delta = meta["words_end"] - meta["words_start"]
        if delta >= 20:
            verb, extra = "schreef in", f" · +{delta} woorden"
        elif delta <= -20:
            verb, extra = "bewerkte", f" · {delta} woorden"
        else:
            verb = "las/bewerkte"
    if "slides_start" in meta and "slides_end" in meta:
        a, b = meta["slides_start"], meta["slides_end"]
        extra = f" · {a} → {b} dia's" if a != b else f" · {b} dia's"
        if b > a:
            verb = "maakte dia's en werkte aan"
    return f"{verb} {shown}{more}{extra}"


def merge_meta(a: dict, b: dict) -> dict:
    """Combine two segments of the same document (same day) for the activity table."""
    out = dict(a)
    parts: dict[str, float] = {}
    for p in (a.get("parts") or []) + (b.get("parts") or []):
        parts[p[0]] = parts.get(p[0], 0) + p[1]
    out["parts"] = [[k, v] for k, v in parts.items()]
    # a is the earlier segment: counts run from its start to b's end
    for key, first, second in (("words_start", a, b), ("slides_start", a, b), ("words_end", b, a), ("slides_end", b, a)):
        if key in first:
            out[key] = first[key]
        elif key in second:
            out[key] = second[key]
    return out
