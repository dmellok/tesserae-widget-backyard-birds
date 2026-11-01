"""Bird stickers: huashu-bird-sticker by Huashu (花叔),
https://github.com/alchaincyf/huashu-bird-sticker, one transparent sticker
with a white outline for each of the 11,064 living bird species in the IOC
list, drawn by an image model from a reference photograph and reviewed
against it for field marks. Offered under PolyForm Shield 1.0.0: free for any
use except a product that competes with the author's bird-watching app, with
credit and a link to the repository. Only the index (stickers.json: the
family folder, the file stem and the review score per species) is bundled;
a sticker is fetched from the pinned revision the first time a species needs
it and kept in the widget's data folder through the host's own cached
fetch, which the caller passes in. Loaded by server.py by path; the same
file serves the Backyard Birds and Specimen Sheet widgets.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent

RAW = "https://raw.githubusercontent.com/alchaincyf/huashu-bird-sticker/{pin}/stickers/{family}/{stem}.webp"
SOURCE = "https://github.com/alchaincyf/huashu-bird-sticker"
LICENCE = "PolyForm Shield 1.0.0"
CREDIT = "Bird stickers by Huashu, huashu-bird-sticker (github.com/alchaincyf/huashu-bird-sticker)"
PLATE_PX = 700
REFERENCE_PX = 768
PAPER = (243, 234, 214)

Fetch = Callable[[Path, str], bytes | None]

_INDEX: dict[str, Any] = {}
_BY_SCI: dict[str, tuple[str, str, int]] = {}


def index() -> dict[str, Any]:
    if not _INDEX:
        try:
            data = json.loads((_HERE / "stickers.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _INDEX.update(data if isinstance(data, dict) else {})
        families = _INDEX.get("families")
        if isinstance(families, dict):
            for family, rows in families.items():
                for stem, score in rows:
                    _BY_SCI[stem.replace("_", " ").lower()] = (family, stem, int(score))
    return _INDEX


def species_count() -> int:
    index()
    return len(_BY_SCI)


def lookup(sci: str) -> dict[str, Any] | None:
    """The sticker for a species, by its scientific name; a subspecies name
    falls back to the species."""
    index()
    words = str(sci or "").lower().split()
    for name in (" ".join(words), " ".join(words[:2])):
        hit = _BY_SCI.get(name)
        if hit:
            family, stem, score = hit
            return {
                "family": family,
                "stem": stem,
                "score": score,
                "pin": str(index().get("pin") or "main"),
                "url": RAW.format(pin=index().get("pin") or "main", family=family, stem=stem),
            }
    return None


def _image(entry: dict[str, Any], data_dir: Path, get: Fetch) -> Any:
    """The sticker as an RGBA image, or None."""
    raw = get(data_dir, entry["url"])
    if not raw:
        return None
    try:
        from PIL import Image

        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None


def _trim(im: Any, pad: int = 12) -> Any:
    """The sticker cropped to its outline, with a little room round it."""
    box = im.getchannel("A").getbbox()
    if not box:
        return im
    x0, y0, x1, y1 = box
    return im.crop(
        (max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad))
    )


def flat(im: Any, colour: tuple[int, int, int], longest: int) -> bytes:
    """The sticker on a flat colour, as JPEG bytes."""
    from PIL import Image

    im = im.copy()
    im.thumbnail((longest, longest), Image.Resampling.LANCZOS)
    page = Image.new("RGB", im.size, colour)
    page.paste(im, mask=im.getchannel("A"))
    out = io.BytesIO()
    page.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def cut(im: Any, longest: int) -> bytes:
    """The sticker as it is, transparent, as PNG bytes."""
    from PIL import Image

    im = im.copy()
    im.thumbnail((longest, longest), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def key_of(entry: dict[str, Any]) -> str:
    return (
        "s_"
        + hashlib.sha1(f"{entry['pin']}/{entry['family']}/{entry['stem']}".encode()).hexdigest()[
            :20
        ]
    )


def files(
    sci: str,
    data_dir: Path,
    get: Fetch,
    longest: int = PLATE_PX,
    paper: tuple[int, int, int] = PAPER,
) -> tuple[bytes, bytes, float] | None:
    """(flat JPEG on paper, transparent PNG, how much of the picture the bird
    fills) for a species, or None when it has no sticker or it cannot be
    fetched."""
    entry = lookup(sci)
    if not entry:
        return None
    im = _image(entry, data_dir, get)
    if im is None:
        return None
    im = _trim(im)
    box = im.getchannel("A").getbbox() or (0, 0, im.width, im.height)
    bird = max(box[2] - box[0], box[3] - box[1]) / max(im.width, im.height)
    return flat(im, paper, longest), cut(im, longest), round(bird, 3)


def reference(sci: str, data_dir: Path, get: Fetch) -> tuple[str, str] | None:
    """The sticker on white as a data URL, for a painter, and its credit."""
    entry = lookup(sci)
    if not entry:
        return None
    im = _image(entry, data_dir, get)
    if im is None:
        return None
    raw = flat(_trim(im), (255, 255, 255), REFERENCE_PX)
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode(), CREDIT
