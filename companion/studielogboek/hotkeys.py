"""Global hotkeys via RegisterHotKey, running a message loop on its own thread."""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


class HotkeyThread(threading.Thread):
    """bindings: list of (modifiers, virtual key, label, callback). Callbacks run on this thread."""

    def __init__(self, bindings: list[tuple[int, int, str, Callable[[], None]]]):
        super().__init__(name="hotkeys", daemon=True)
        self.bindings = bindings
        self.failed: list[str] = []
        self._thread_id: int | None = None
        self.ready = threading.Event()

    def run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        for i, (mods, vk, label, _) in enumerate(self.bindings, start=1):
            if not user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                self.failed.append(label)
                log.warning("Sneltoets %s is al in gebruik door een ander programma", label)
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and 1 <= msg.wParam <= len(self.bindings):
                try:
                    self.bindings[msg.wParam - 1][3]()
                except Exception:
                    log.exception("hotkey callback failed")
        for i in range(1, len(self.bindings) + 1):
            user32.UnregisterHotKey(None, i)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
