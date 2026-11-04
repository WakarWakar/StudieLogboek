"""python -m unittest discover -s companion/tests   (run from the project folder, with .venv)"""
import datetime as dt
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("STUDIELOGBOEK_DATA", tempfile.mkdtemp())

from studielogboek import config, export  # noqa: E402
from studielogboek.db import Database  # noqa: E402


def text(runs):
    return "".join(t for t, _ in runs)


class ApaTests(unittest.TestCase):
    def src(self, **kw):
        base = {"kind": "page", "title": "", "url": "https://example.org/x", "site": "example.org",
                "meta": {}, "accessed": dt.date(2026, 10, 14)}
        base.update(kw)
        return base

    def test_webpage_without_author(self):
        r = text(export.apa_reference(self.src(title="Overview of the REST API | Illumio", site="Illumio")))
        self.assertEqual(r, "Overview of the REST API. (z.d.). Illumio. Geraadpleegd op 14 oktober 2026, van https://example.org/x")

    def test_author_with_dutch_particles_and_question_mark(self):
        r = text(export.apa_reference(self.src(title="What is MCP?", site="ElevenLabs",
                                               meta={"authors": ["Jan van der Berg"], "published": "2025-03-14T10:00:00Z"})))
        self.assertTrue(r.startswith("Berg, J. van der (2025, 14 maart). What is MCP? ElevenLabs."), r)

    def test_youtube(self):
        r = text(export.apa_reference(self.src(kind="video", title="Linux Firewall", url="https://www.youtube.com/watch?v=1",
                                               meta={"channel": "NetworkChuck", "published": "2023-05-05"})))
        self.assertTrue(r.startswith("NetworkChuck. (2023, 5 mei). Linux Firewall [Video]. YouTube."), r)

    def test_wikipedia_has_no_date(self):
        r = text(export.apa_reference(self.src(title="Zero trust architecture - Wikipedia", url="https://en.wikipedia.org/wiki/Z",
                                               site="Wikimedia Foundation, Inc.", meta={"published": "2020-08-25"})))
        self.assertTrue(r.startswith("Zero trust architecture. (z.d.). In Wikipedia."), r)

    def test_organisation_author_is_kept(self):
        self.assertEqual(export._apa_author("Illumio Team"), "Illumio Team")
        self.assertEqual(export._apa_author("Hamid Serraj"), "Serraj, H.")


class OfficeTests(unittest.TestCase):
    def test_word_description(self):
        from studielogboek import office
        meta = {"parts": [["5.1.2 Leerproces", 720], ["6. AI-gebruik", 180]], "words_start": 5000, "words_end": 5240}
        self.assertEqual(office.describe("Word", meta),
                         "schreef in ‘5.1.2 Leerproces’ (12 min), ‘6. AI-gebruik’ (3 min) · +240 woorden")

    def test_powerpoint_description_and_merge(self):
        from studielogboek import office
        a = {"parts": [["dia 3 ‘Planning’", 300]], "slides_start": 8, "slides_end": 8}
        b = {"parts": [["dia 3 ‘Planning’", 60], ["dia 9 ‘Vragen’", 240]], "slides_start": 8, "slides_end": 9}
        m = office.merge_meta(a, b)
        self.assertEqual(office.describe("PowerPoint", m),
                         "maakte dia's en werkte aan dia 3 ‘Planning’ (6 min), dia 9 ‘Vragen’ (4 min) · 8 → 9 dia's")

    def test_tracker_credits_time_to_the_current_part(self):
        from studielogboek.tracker import WindowTracker
        seg = {"parts": {}}
        WindowTracker._add_details(seg, {"part": "Inleiding", "words": 100}, 0)
        WindowTracker._add_details(seg, {"part": "Inleiding", "words": 120}, 5)
        WindowTracker._add_details(seg, {"part": "Voorwoord", "words": 150}, 5)
        WindowTracker._add_details(seg, None, 5)  # Word busy: time stays with the last heading
        self.assertEqual(seg["parts"], {"Inleiding": ["Inleiding", 5], "Voorwoord": ["Voorwoord", 10]})
        self.assertEqual((seg["words_start"], seg["words_end"]), (100, 150))

    def test_slide_title_being_typed_is_one_entry(self):
        from studielogboek.tracker import WindowTracker
        seg = {"parts": {}}
        for title, t in (("Intro", 0), ("Introcu", 5), ("Introductie Sinteg", 5)):
            WindowTracker._add_details(seg, {"part": f"dia 3 ‘{title}’", "key": "dia 3", "slides": 15}, t)
        self.assertEqual(list(seg["parts"].values()), [["dia 3 ‘Introductie Sinteg’", 10]])


class WeekTests(unittest.TestCase):
    def test_week_numbers(self):
        self.assertEqual(config.internship_week(dt.date(2026, 8, 31)), 1)
        self.assertEqual(config.internship_week(dt.date(2026, 9, 6)), 1)
        self.assertEqual(config.internship_week(dt.date(2026, 11, 2)), 10)
        self.assertEqual(config.week_range(10), (dt.date(2026, 11, 2), dt.date(2026, 11, 8)))


class ExportTests(unittest.TestCase):
    def test_export_builds_a_valid_docx(self):
        db = Database(Path(tempfile.mkdtemp()) / "t.db")
        t = time.mktime(dt.date(2026, 11, 2).timetuple()) + 9 * 3600
        db.upsert_event({"key": "p", "kind": "page", "source": "firefox", "start": t, "seconds": 120,
                         "title": "Page", "url": "https://example.org/p", "site": "example.org"})
        db.upsert_event({"key": "p", "kind": "page", "source": "firefox", "start": t, "seconds": 150,
                         "title": "Page", "url": "https://example.org/p", "site": "example.org"})  # checkpoint update
        db.upsert_event({"key": "w", "kind": "app", "source": "windows", "start": t + 200, "seconds": 600,
                         "title": "Stage portfolio.docx", "app": "Word"})
        db.add_note("Vraag-ToDo", "Credentials vragen", ["Manage & Control"], {})
        out = Path(tempfile.mkdtemp()) / "w.docx"
        export.export_range(db, dt.date(2026, 11, 2), dt.date.today(), str(out))
        xml = zipfile.ZipFile(out).read("word/document.xml").decode()
        self.assertIn("Literatuurlijst", xml)
        self.assertIn("Credentials vragen", xml)
        self.assertIn("w14:checkbox", xml)
        self.assertIn("2 min", xml)  # merged checkpoint, not 4 min


if __name__ == "__main__":
    unittest.main()
