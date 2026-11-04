"""Build the Dutch Word logbook (.docx) from the local database."""
from __future__ import annotations

import datetime as dt
import re
import time
from collections import OrderedDict, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401  (kept for users who want landscape)
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

from . import config
from .db import Database

DAYS = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
          "september", "oktober", "november", "december"]
KIND_LABEL = {"page": "Webpagina", "video": "Video", "search": "Zoekopdracht", "pdf": "PDF", "ai": "AI-chat"}
GREY = RGBColor(0x6E, 0x77, 0x81)
LINK = RGBColor(0x05, 0x63, 0xC1)
AI_NAMES = {"ChatGPT", "Claude", "Gemini", "Microsoft Copilot", "Perplexity", "Le Chat (Mistral)", "Google AI Mode"}


# ============================================================ formatting helpers

def d_nl(day: dt.date) -> str:
    return day.strftime("%d-%m-%Y")


def long_date(day: dt.date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def dur(seconds: float) -> str:
    s = int(round(seconds or 0))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    return f"{m // 60} u {m % 60:02d} min"


def local(ts: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts)


def _set_cell_shading(cell, hex_fill: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def header_row(table) -> None:
    """Repeat the header row on every page and keep it with the first data row."""
    trPr = table.rows[0]._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)
    for cell in table.rows[0].cells:
        cell.paragraphs[0].paragraph_format.keep_with_next = True


def add_hyperlink(paragraph, url: str, text: str, size: Pt | None = None, color: RGBColor = LINK):
    part = paragraph.part
    r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    c = OxmlElement("w:color")
    c.set(qn("w:val"), str(color))
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rpr.append(c)
    rpr.append(u)
    if size:
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(size.pt * 2)))
        rpr.append(sz)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)
    return link


def add_checkbox(paragraph, checked: bool = False) -> None:
    """A real clickable Word checkbox (content control)."""
    W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
    sdt = OxmlElement("w:sdt")
    pr = OxmlElement("w:sdtPr")
    cb = pr.makeelement(f"{{{W14}}}checkbox", nsmap={"w14": W14})
    chk = cb.makeelement(f"{{{W14}}}checked", nsmap={"w14": W14})
    chk.set(f"{{{W14}}}val", "1" if checked else "0")
    on = cb.makeelement(f"{{{W14}}}checkedState", nsmap={"w14": W14})
    on.set(f"{{{W14}}}val", "2612")
    on.set(f"{{{W14}}}font", "MS Gothic")
    off = cb.makeelement(f"{{{W14}}}uncheckedState", nsmap={"w14": W14})
    off.set(f"{{{W14}}}val", "2610")
    off.set(f"{{{W14}}}font", "MS Gothic")
    cb.extend([chk, on, off])
    pr.append(cb)
    content = OxmlElement("w:sdtContent")
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    for a in ("w:ascii", "w:eastAsia", "w:hAnsi"):
        fonts.set(qn(a), "MS Gothic")
    rpr.append(fonts)
    r.append(rpr)
    t = OxmlElement("w:t")
    t.text = "☒" if checked else "☐"
    r.append(t)
    content.append(r)
    sdt.extend([pr, content])
    paragraph._p.append(sdt)


def add_seq_field(paragraph, name: str, number: int) -> None:
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), f" SEQ {name} \\* ARABIC ")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = str(number)
    r.append(t)
    fld.append(r)
    paragraph._p.append(fld)


def placeholder(doc, text: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.italic = True
    run.font.color.rgb = GREY


def grey(paragraph, text: str, size: float = 9) -> None:
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    run.font.color.rgb = GREY


# ============================================================ APA 7 references

def _clean_title(title: str, site: str | None) -> str:
    t = (title or "").strip()
    if site:
        t = re.sub(rf"\s*[|\-–—·:]\s*{re.escape(site)}\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*[-–—]\s*Wikipedia\s*$", "", t)
    t = re.sub(r"\s*-\s*YouTube\s*$", "", t)
    return t or (title or "").strip()


def _apa_author(name: str) -> str:
    name = name.strip()
    if not name or "," in name:
        return name
    parts = name.split()
    if len(parts) < 2 or len(parts) > 4 or any(ch.isdigit() for ch in name) or "&" in name \
            or any(p.lower() in {"team", "staff", "redactie", "inc", "b.v.", "ltd", "llc"} for p in parts):
        return name  # probably an organisation
    particles = {"van", "de", "der", "den", "het", "ter", "ten", "von", "da", "di", "le", "la"}
    last = parts[-1]
    given = [p for p in parts[:-1] if p.lower() not in particles]
    prefix = " ".join(p.lower() for p in parts[:-1] if p.lower() in particles)
    initials = " ".join(f"{p[0].upper()}." for p in given)
    # Dutch APA convention: "Berg, J. van der"
    return f"{last}, {initials}" + (f" {prefix}" if prefix else "")


def _apa_authors(names: list[str]) -> str:
    names = [_apa_author(n) for n in names if n and n.strip()]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) <= 20:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:19]) + ", ... " + names[-1]


def _apa_date(published: str | None) -> str:
    if not published:
        return "(z.d.)"
    m = re.match(r"(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?", published.strip())
    if not m:
        return "(z.d.)"
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if d and mo:
        return f"({y}, {int(d)} {MONTHS[int(mo) - 1]})"
    if mo:
        return f"({y}, {MONTHS[int(mo) - 1]})"
    return f"({y})"


def _end(title: str) -> str:
    """Period after a title unless it already ends in punctuation."""
    return "" if title.rstrip().endswith(("?", "!", ".")) else "."


def apa_reference(src: dict) -> list[tuple[str, bool]]:
    """Returns runs [(text, italic)] for one reference."""
    meta = src.get("meta") or {}
    site = src.get("site") or ""
    url = src.get("url") or ""
    accessed = f"Geraadpleegd op {long_date(src['accessed'])}"
    kind = src["kind"]
    date = _apa_date(meta.get("published"))

    if kind == "video":
        channel = meta.get("channel") or "Onbekend kanaal"
        title = _clean_title(src.get("title") or "", "YouTube")
        return [(f"{channel}. {date}. ", False), (title, True), (f" [Video]. YouTube. {accessed}, van {url}", False)]

    authors = _apa_authors(meta.get("authors") or [])
    title = _clean_title(meta.get("citationTitle") or src.get("title") or url, site)
    # "[PDF]" comes after the title, then a period; otherwise only a period if the title has none
    after_title = " [PDF]." if kind == "pdf" else _end(title)

    if meta.get("journal"):
        doi = meta.get("doi")
        link = f"https://doi.org/{doi}" if doi else url
        if authors:
            return [(f"{authors} {date}. {title}{_end(title)} ", False), (meta["journal"], True), (f". {link}", False)]
        return [(f"{title}{_end(title)} {date}. ", False), (meta["journal"], True), (f". {link}", False)]

    if (urlsplit(url).hostname or "").endswith("wikipedia.org"):
        # APA: wiki pages change constantly, so no date and the retrieval date instead
        title = _clean_title(src.get("title") or url, "Wikipedia")
        return [(f"{title}{_end(title)} (z.d.). In ", False), ("Wikipedia", True), (f". {accessed}, van {url}", False)]

    if not url.startswith("http"):
        return [(title, True), (f"{after_title} {date}. Lokaal bestand: {url}", False)]

    site_part = f" {site}." if site and site.lower() not in (authors or "").lower() else ""
    if authors:
        return [(f"{authors} {date}. ", False), (title, True), (f"{after_title}{site_part} {accessed}, van {url}", False)]
    return [(title, True), (f"{after_title} {date}.{site_part} {accessed}, van {url}", False)]


def _norm_url(url: str) -> str:
    return (url or "").rstrip("/").lower()


def collect_sources(events: list[dict], notes: list[dict], shots: list[dict]) -> list[dict]:
    referenced = {_norm_url(n.get("ctx_url")) for n in notes + shots if n.get("ctx_url")}
    by_url: "OrderedDict[str, dict]" = OrderedDict()
    totals: dict[str, float] = defaultdict(float)
    for ev in events:
        if ev["kind"] not in ("page", "pdf", "video") or not ev.get("url"):
            continue
        key = _norm_url(ev["url"])
        totals[key] += ev.get("seconds") or 0
        day = local(ev["start"]).date()
        if key not in by_url:
            by_url[key] = {**ev, "accessed": day}
        else:
            src = by_url[key]
            src["accessed"] = max(src["accessed"], day)
            src["meta"] = {**(ev.get("meta") or {}), **(src.get("meta") or {})}
    out = []
    for key, src in by_url.items():
        need = {"page": config.CITE_MIN_PAGE_SECONDS, "pdf": config.CITE_MIN_PDF_SECONDS,
                "video": config.CITE_MIN_VIDEO_SECONDS}[src["kind"]]
        if totals[key] >= need or key in referenced:
            out.append(src)

    def sort_key(src):
        runs = apa_reference(src)
        return "".join(t for t, _ in runs).casefold()
    return sorted(out, key=sort_key)


# ============================================================ grouping helpers

def merge_activity(events: list[dict]) -> list[dict]:
    """One row per source per day: first time, summed duration."""
    rows: "OrderedDict[tuple, dict]" = OrderedDict()
    for ev in events:
        if ev["kind"] == "app":
            sig = ("app", ev.get("app"), ev.get("title"))
        elif ev["kind"] == "search":
            sig = ("search", (ev.get("meta") or {}).get("engine"), ev.get("title"))
        else:
            sig = (ev["kind"], _norm_url(ev.get("url")))
        if sig in rows:
            rows[sig]["seconds"] += ev.get("seconds") or 0
        else:
            rows[sig] = {**ev, "seconds": ev.get("seconds") or 0}
    return sorted(rows.values(), key=lambda r: r["start"])


def note_is_ai(n: dict) -> bool:
    url = (n.get("ctx_url") or "").lower()
    host = urlsplit(url).hostname or ""
    return any(h in host for h in ("chatgpt.com", "openai.com", "claude.ai", "gemini.google", "copilot.microsoft",
                                   "perplexity.ai", "mistral.ai")) or "udm=50" in url or (n.get("ctx_title") in AI_NAMES)


# ============================================================ document builder

class LogbookBuilder:
    def __init__(self, db: Database, start: dt.date, end: dt.date):
        self.db = db
        self.start, self.end = start, end
        t0 = time.mktime(start.timetuple())
        t1 = time.mktime((end + dt.timedelta(days=1)).timetuple())
        self.events = db.events_between(t0, t1)
        self.notes = db.notes_between(t0, t1)
        self.shots = db.screenshots_between(t0, t1)
        self.figure = 0
        self.figure_of_shot: dict[int, int] = {}
        self.doc = Document()
        self._setup()

    # ---------------------------------------------------------------- setup
    def _setup(self) -> None:
        doc = self.doc
        sec = doc.sections[0]
        sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(sec, side, Cm(2.3))
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)
        rpr = normal.element.get_or_add_rPr()
        lang = OxmlElement("w:lang")
        lang.set(qn("w:val"), "nl-NL")
        rpr.append(lang)
        normal.paragraph_format.space_after = Pt(4)
        doc.core_properties.title = "Studielogboek"
        doc.core_properties.language = "nl-NL"

    # ---------------------------------------------------------------- small writers
    def bullet(self, text: str = "", bold_prefix: str | None = None):
        p = self.doc.add_paragraph(style="List Bullet")
        if bold_prefix:
            p.add_run(bold_prefix).bold = True
        if text:
            p.add_run(text)
        return p

    def note_block(self, n: dict, with_date: bool = False, checkbox: bool = False) -> None:
        when = local(n["ts"])
        stamp = when.strftime("%d-%m %H:%M") if with_date else when.strftime("%H:%M")
        lines = n["text"].splitlines() or [""]
        p = self.doc.add_paragraph(style=None if checkbox else "List Bullet")
        if checkbox:
            add_checkbox(p)
            p.add_run(" ")
        p.add_run(f"{stamp}  ").bold = True
        if n["type"] == "Feedback":
            p.add_run("Feedback: ").italic = True
        p.add_run(lines[0])
        for extra in lines[1:]:
            p.add_run().add_break()
            p.add_run(extra)
        ctx_bits = []
        if n.get("ctx_title") or n.get("ctx_url"):
            ctx_bits.append(n.get("ctx_title") or n.get("ctx_url"))
        if ctx_bits or n.get("tags"):
            p.add_run().add_break()
            if n.get("ctx_url"):
                grey(p, "bij: ")
                add_hyperlink(p, n["ctx_url"], (n.get("ctx_title") or n["ctx_url"])[:120], size=Pt(9))
            elif ctx_bits:
                grey(p, f"bij: {n.get('ctx_app') + ' - ' if n.get('ctx_app') else ''}{ctx_bits[0][:120]}")
            if n.get("tags"):
                grey(p, ("   ·   " if ctx_bits else "") + "competentie: " + ", ".join(n["tags"]))

    def screenshot_block(self, s: dict) -> None:
        path = Path(s["path"])
        if not path.exists():
            placeholder(self.doc, f"(Afbeelding niet gevonden: {path})")
            return
        with Image.open(path) as im:
            w_px, h_px = im.size
        # at most 16 cm wide and 18 cm tall, never upscaled beyond 96 dpi
        width = Cm(min(16.0, w_px / 96 * 2.54, 18.0 * w_px / max(h_px, 1)))
        self.doc.add_picture(str(path), width=width)
        self.figure += 1
        self.figure_of_shot[s["id"]] = self.figure
        style = "Caption" if "Caption" in [st.name for st in self.doc.styles] else None
        cap = self.doc.add_paragraph(style=style)
        cap.add_run("Figuur ")
        add_seq_field(cap, "Figuur", self.figure)
        cap.add_run(f": {s.get('caption') or 'screenshot'}")
        when = local(s["ts"]).strftime("%H:%M")
        src = s.get("ctx_title") or s.get("ctx_app")
        grey(cap, f"  ({when}{' · ' + src[:80] if src else ''})")

    def activity_table(self, rows: list[dict]) -> None:
        table = self.doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        header_row(table)
        widths = (Cm(1.4), Cm(2.6), Cm(10.6), Cm(1.8))
        for cell, label, w in zip(table.rows[0].cells, ("Tijd", "Activiteit", "Bron", "Duur"), widths):
            cell.text = ""
            run = cell.paragraphs[0].add_run(label)
            run.bold = True
            run.font.size = Pt(9)
            cell.width = w
            _set_cell_shading(cell, "E7ECF2")
        for r in rows:
            cells = table.add_row().cells
            for c, w in zip(cells, widths):
                c.width = w
            self._cell(cells[0], local(r["start"]).strftime("%H:%M"))
            meta = r.get("meta") or {}
            if r["kind"] == "app":
                self._cell(cells[1], r.get("app") or "Programma")
                self._cell(cells[2], r.get("title") or "")
            else:
                self._cell(cells[1], KIND_LABEL.get(r["kind"], r["kind"]))
                p = cells[2].paragraphs[0]
                if r["kind"] == "search":
                    run = p.add_run(f"“{r.get('title')}” ")
                    run.font.size = Pt(9)
                    grey(p, f"({meta.get('engine') or r.get('site')})")
                else:
                    title = _clean_title(r.get("title") or r.get("url") or "", r.get("site"))[:140]
                    if r.get("url", "").startswith("http"):
                        add_hyperlink(p, r["url"], title, size=Pt(9))
                    else:
                        p.add_run(title).font.size = Pt(9)
                    if r["kind"] == "video":
                        extra = meta.get("channel") or ""
                        if meta.get("duration"):
                            extra += f" · {dur(r['seconds'])} van {dur(meta['duration'])} gekeken"
                        if extra:
                            grey(p, f"  {extra.strip(' ·')}")
                    elif r.get("site") and r["kind"] != "ai":
                        wiki = (urlsplit(r.get("url") or "").hostname or "").endswith("wikipedia.org")
                        grey(p, "  Wikipedia" if wiki else f"  {r['site']}")
            self._cell(cells[3], "" if (r["kind"] == "search" and r["seconds"] < 10) else dur(r["seconds"]))

    @staticmethod
    def _cell(cell, text: str) -> None:
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.font.size = Pt(9)

    # ---------------------------------------------------------------- sections
    def build(self) -> Document:
        doc = self.doc
        doc.add_paragraph("Studielogboek", style="Title")
        sub = doc.add_paragraph()
        subtitle = self.db.get_setting("report_subtitle", "")
        if subtitle:
            sub.add_run(subtitle + "\n").bold = True
        grey(sub, f"Periode: {d_nl(self.start)} t/m {d_nl(self.end)}   ·   gegenereerd op "
                  f"{dt.datetime.now().strftime('%d-%m-%Y %H:%M')} door Studielogboek", 10)
        placeholder(doc, "Dit logboek is automatisch samengesteld uit wat ik tijdens mijn studiemomenten heb bekeken, "
                         "gelezen en genoteerd. Per week staat een voortgangsrapport, daarna per dag mijn notities, "
                         "screenshots en activiteiten. Achteraan staan het AI-gebruik, mijn bevindingen per competentie "
                         "en de literatuurlijst (APA 7).")

        days = self._days()
        if not days:
            placeholder(doc, "Er is in deze periode nog niets vastgelegd.")
        self._overview(days)

        weeks: "OrderedDict[int, list[dt.date]]" = OrderedDict()
        for day in days:
            weeks.setdefault(config.internship_week(day), []).append(day)
        for week, wdays in weeks.items():
            self._week(week, wdays)

        doc.add_page_break()
        self._ai_section()
        self._competency_section()
        self._literature()
        return doc

    def _days(self) -> list[dt.date]:
        seen = set()
        for ev in self.events:
            seen.add(local(ev["start"]).date())
        for n in self.notes + self.shots:
            seen.add(local(n["ts"]).date())
        return sorted(seen)

    def _on(self, items: list[dict], day: dt.date, ts_key: str) -> list[dict]:
        return [x for x in items if local(x[ts_key]).date() == day]

    def _overview(self, days: list[dt.date]) -> None:
        if not days:
            return
        self.doc.add_heading("Overzicht", level=1)
        table = self.doc.add_table(rows=1, cols=5)
        table.style = "Table Grid"
        for cell, label in zip(table.rows[0].cells, ("Week", "Periode", "Studietijd", "Bronnen", "Notities")):
            cell.text = ""
            cell.paragraphs[0].add_run(label).bold = True
            _set_cell_shading(cell, "E7ECF2")
        weeks = sorted({config.internship_week(d) for d in days})
        for w in weeks:
            ws, we = config.week_range(w)
            evs = [e for e in self.events if ws <= local(e["start"]).date() <= we]
            nts = [n for n in self.notes if ws <= local(n["ts"]).date() <= we]
            total = sum(e.get("seconds") or 0 for e in evs if e["kind"] != "search")
            srcs = len({_norm_url(e["url"]) for e in evs if e["kind"] in ("page", "pdf", "video") and e.get("url")})
            row = table.add_row().cells
            for c, text in zip(row, (self._week_label(w), f"{d_nl(ws)} t/m {d_nl(we)}", dur(total), str(srcs), str(len(nts)))):
                c.text = text

    @staticmethod
    def _week_label(week: int) -> str:
        return f"Week {week}" if week >= 1 else "Voor de stage"

    def _week(self, week: int, days: list[dt.date]) -> None:
        doc = self.doc
        ws, we = config.week_range(week)
        doc.add_heading(f"{self._week_label(week)} ({d_nl(ws)} t/m {d_nl(we)})", level=1)
        evs = [e for e in self.events if local(e["start"]).date() in days]
        nts = [n for n in self.notes if local(n["ts"]).date() in days]

        doc.add_heading("Voortgangsrapport", level=2)
        doc.add_heading("Wat heb ik gedaan", level=3)
        browser = sum(e["seconds"] or 0 for e in evs if e["source"] == "firefox" and e["kind"] != "search")
        apps = sum(e["seconds"] or 0 for e in evs if e["kind"] == "app")
        if evs:
            self.bullet(f"{dur(browser + apps)} (browser {dur(browser)}, programma's {dur(apps)}) "
                        f"verdeeld over {len(days)} dag(en).", "Studietijd: ")
            top = [r for r in merge_activity([e for e in evs if e["kind"] in ("page", "pdf", "video")])]
            top.sort(key=lambda r: -r["seconds"])
            if top:
                p = self.bullet(bold_prefix="Meest gebruikte bronnen: ")
                for i, r in enumerate(top[:5]):
                    if i:
                        p.add_run("; ")
                    add_hyperlink(p, r["url"], _clean_title(r.get("title") or r["url"], r.get("site"))[:80]) \
                        if r.get("url", "").startswith("http") else p.add_run(r.get("title") or "")
                    p.add_run(f" ({dur(r['seconds'])})")
            videos = [e for e in evs if e["kind"] == "video"]
            if videos:
                self.bullet(f"{len({e['url'] for e in videos})} video('s), samen {dur(sum(e['seconds'] for e in videos))} gekeken.",
                            "Video's: ")
            searches = [e for e in evs if e["kind"] == "search"]
            if searches:
                qs = list(OrderedDict.fromkeys(e["title"] for e in searches))
                self.bullet(", ".join(f"“{q}”" for q in qs[:12]) + (" ..." if len(qs) > 12 else ""),
                            f"Zoekopdrachten ({len(qs)}): ")
            app_rows = sorted(merge_activity([e for e in evs if e["kind"] == "app"]), key=lambda r: -r["seconds"])
            if app_rows:
                self.bullet("; ".join(f"{r['app']} - {r['title'][:60]} ({dur(r['seconds'])})" for r in app_rows[:6]),
                            "Gewerkt in: ")
        placeholder(doc, "Vul aan: beschrijf in een paar zinnen wat je deze week hebt gedaan en waarom.")

        doc.add_heading("Wat heb ik geleerd", level=3)
        learned = [n for n in nts if n["type"] in ("Geleerd", "Bevinding")]
        for n in learned:
            self.note_block(n, with_date=True)
        if not learned:
            placeholder(doc, "Nog geen notities van het type Geleerd of Bevinding deze week.")

        doc.add_heading("Feedback", level=3)
        fb = [n for n in nts if n["type"] in ("Feedback", "Gesprek begeleider")]
        for n in fb:
            self.note_block(n, with_date=True)
        if not fb:
            placeholder(doc, "Nog geen feedback of gesprekken vastgelegd deze week.")

        doc.add_heading("Volgende stappen", level=3)
        todos = [n for n in nts if n["type"] == "Vraag-ToDo"]
        for n in todos:
            self.note_block(n, with_date=True, checkbox=True)
        placeholder(doc, "Vul aan: wat ga je volgende week doen?")

        for day in days:
            self._day(day)

    def _day(self, day: dt.date) -> None:
        doc = self.doc
        doc.add_heading(f"{DAYS[day.weekday()].capitalize()} {d_nl(day)}", level=2)
        nts = self._on(self.notes, day, "ts")
        shots = self._on(self.shots, day, "ts")
        evs = self._on(self.events, day, "start")

        sections = [
            ("Gesprekken met begeleider", [n for n in nts if n["type"] in ("Gesprek begeleider", "Feedback")], False),
            ("Wat heb ik geleerd", [n for n in nts if n["type"] == "Geleerd"], False),
            ("Bevindingen", [n for n in nts if n["type"] == "Bevinding"], False),
            ("Open vragen & ToDo's", [n for n in nts if n["type"] == "Vraag-ToDo"], True),
        ]
        for title, items, checkbox in sections:
            if not items:
                continue
            doc.add_heading(title, level=3)
            for n in items:
                self.note_block(n, checkbox=checkbox)
        if shots:
            doc.add_heading("Screenshots", level=3)
            for s in shots:
                self.screenshot_block(s)
        if evs:
            doc.add_heading("Activiteiten", level=3)
            self.activity_table(merge_activity(evs))
        if not any(items for _, items, _ in sections) and not shots and not evs:
            placeholder(doc, "Geen activiteiten.")

    def _ai_section(self) -> None:
        doc = self.doc
        doc.add_heading("AI-gebruik", level=1)
        placeholder(doc, "Overzicht van de momenten waarop ik AI-tools heb gebruikt (voor hoofdstuk 6 van het portfolio). "
                         "Alleen de dienst en de tijd zijn vastgelegd, niet de inhoud van de gesprekken.")
        ai = [e for e in self.events if e["kind"] == "ai"]
        ai_notes = [n for n in self.notes if note_is_ai(n)]
        if not ai and not ai_notes:
            placeholder(doc, "Geen AI-gebruik vastgelegd in deze periode.")
            return
        if ai:
            totals: dict[str, float] = defaultdict(float)
            for e in ai:
                totals[e.get("site") or e.get("title")] += e.get("seconds") or 0
            p = doc.add_paragraph()
            p.add_run("Totaal: ").bold = True
            p.add_run("; ".join(f"{k} {dur(v)}" for k, v in sorted(totals.items(), key=lambda kv: -kv[1])))
            table = doc.add_table(rows=1, cols=4)
            table.style = "Table Grid"
            for cell, label in zip(table.rows[0].cells, ("Datum", "Tijd", "Tool", "Duur")):
                cell.text = ""
                cell.paragraphs[0].add_run(label).bold = True
                _set_cell_shading(cell, "E7ECF2")
            rows: "OrderedDict[tuple, dict]" = OrderedDict()
            for e in ai:
                k = (local(e["start"]).date(), e.get("site"))
                if k in rows:
                    rows[k]["seconds"] += e.get("seconds") or 0
                else:
                    rows[k] = {**e}
            for (day, site), e in rows.items():
                c = table.add_row().cells
                for cell, text in zip(c, (d_nl(day), local(e["start"]).strftime("%H:%M"), site or "", dur(e["seconds"]))):
                    self._cell(cell, text)
        if ai_notes:
            doc.add_heading("Notities over AI-gebruik", level=2)
            for n in ai_notes:
                self.note_block(n, with_date=True)
        doc.add_heading("Toelichting", level=2)
        placeholder(doc, "Vul aan: waarvoor heb je AI gebruikt, hoe heb je de antwoorden gecontroleerd, en past dit binnen "
                         "de richtlijnen van het bedrijf (EU AI Act)?")

    def _competency_section(self) -> None:
        doc = self.doc
        doc.add_heading("Bevindingen per competentie", level=1)
        placeholder(doc, "Notities en screenshots gegroepeerd per competentie, als bewijslast voor hoofdstuk 5 van het portfolio.")
        any_tag = False
        for comp in config.COMPETENCIES:
            nts = [n for n in self.notes if comp in (n.get("tags") or [])]
            shots = [s for s in self.shots if comp in (s.get("tags") or [])]
            if not nts and not shots:
                continue
            any_tag = True
            doc.add_heading(comp, level=2)
            for n in nts:
                p = self.bullet()
                p.add_run(local(n["ts"]).strftime("%d-%m-%Y") + "  ").bold = True
                p.add_run(f"{n['type']}: ").italic = True
                p.add_run(n["text"].replace("\n", " "))
            for s in shots:
                fig = self.figure_of_shot.get(s["id"])
                self.bullet(f"{local(s['ts']).strftime('%d-%m-%Y')}  Figuur {fig}: {s.get('caption') or 'screenshot'}")
        untagged = [n for n in self.notes if not n.get("tags")]
        if not any_tag:
            placeholder(doc, "Nog geen notities met een competentie. Vink in de snelle notitie een competentie aan.")
        elif untagged:
            grey(doc.add_paragraph(), f"{len(untagged)} notitie(s) zonder competentie staan alleen bij de dagen.", 10)

    def _literature(self) -> None:
        doc = self.doc
        doc.add_heading("Literatuurlijst", level=1)
        sources = collect_sources(self.events, self.notes, self.shots)
        if not sources:
            placeholder(doc, "Nog geen bronnen die lang genoeg zijn bekeken om op te nemen.")
            return
        grey(doc.add_paragraph(), f"Opgemaakt volgens APA 7. Opgenomen: pagina's die minstens "
                                  f"{config.CITE_MIN_PAGE_SECONDS // 60} min, video's die minstens "
                                  f"{config.CITE_MIN_VIDEO_SECONDS // 60} min bekeken zijn, en bronnen met een notitie. "
                                  "Controleer auteurs en datums: die komen uit de pagina zelf en ontbreken soms.", 9)
        for src in sources:
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.left_indent = Cm(1.27)
            pf.first_line_indent = Cm(-1.27)
            pf.space_after = Pt(6)
            for text, italic in apa_reference(src):
                p.add_run(text).italic = italic


# ============================================================ public API

def _save(doc: Document, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc.save(path)
    except PermissionError:  # the previous export is still open in Word
        path = path.with_name(f"{path.stem}_{time.strftime('%H%M%S')}{path.suffix}")
        doc.save(path)
    return path


def export_range(db: Database, start: dt.date, end: dt.date, out: str | None = None) -> Path:
    if end < start:
        raise ValueError("de einddatum ligt voor de begindatum")
    doc = LogbookBuilder(db, start, end).build()
    path = Path(out) if out else config.EXPORT_DIR / f"Studielogboek_{start:%Y-%m-%d}_tm_{end:%Y-%m-%d}.docx"
    return _save(doc, path)


def export_week(db: Database, week: int, out: str | None = None) -> Path:
    start, end = config.week_range(week)
    doc = LogbookBuilder(db, start, end).build()
    path = Path(out) if out else config.EXPORT_DIR / f"Studielogboek_week{week:02d}.docx"
    return _save(doc, path)


def export_all(db: Database, out: str | None = None) -> Path:
    first, last = db.first_and_last_timestamp()
    today = dt.date.today()
    start = local(first).date() if first else today
    end = max(local(last).date(), today) if last else today
    doc = LogbookBuilder(db, start, end).build()
    path = Path(out) if out else config.EXPORT_DIR / "Studielogboek_alles.docx"
    return _save(doc, path)
