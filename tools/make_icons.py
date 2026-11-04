"""Regenerate the extension icons from the same drawing as the tray icon."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "companion"))
from studielogboek.ui import UI  # noqa: E402

out = root / "extension" / "icons"
out.mkdir(parents=True, exist_ok=True)
for on in (True, False):
    big = UI.tray_image(on)
    for size in (16, 32, 48, 96):
        big.resize((size, size)).save(out / f"{'on' if on else 'off'}-{size}.png")
print("icons written to", out)
