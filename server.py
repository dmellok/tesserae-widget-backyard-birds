"""backyard_birds: the birds BirdNET-Go has heard today.

Runs on the Tesserae side, never the client. Returns a JSON-serialisable dict,
or {"error": "friendly message"}; never raises.

Detections come from a BirdNET-Go server on the operator's network (its v2
API):

  /api/v2/analytics/species/daily   today's species: count, hourly counts,
                                    first and latest heard, new-species flags
  /api/v2/detections/recent         the latest individual detections

Both are public unless BirdNET-Go's Private Mode is on, in which case an API
token goes in the Authorization header. With no server URL the widget runs in
demo mode: a believable day of detections for the region of the cell's (or the
app's) location, generated from the clock, so it can be tried without
BirdNET-Go.

Pictures, in the order the "pictures" option asks for them:

  plate      a classic natural history plate from the reviewed pack in
             plates.json, fetched once from Wikimedia Commons, cropped to the
             bird and kept
  generated  an AI painting in the same plate style, made once per species
             through fal.ai from a reference photo, kept for good (experimental,
             needs a fal key in the widget settings). Painting takes longer
             than Tesserae gives a fetch, so it runs in a background thread
             and the bird shows a photo until the painting is ready. The
             admin page (blueprint() below) lists the paintings, keeps
             earlier versions, and deletes or repaints them.
  photo      BirdNET-Go's own species thumbnail, or an openly licensed
             iNaturalist photo in demo mode

Every picture is resized for the layout and inlined as a data URI, as are the
caption fonts, because the render sandbox has no network.

Files in ctx["data_dir"]:
  result_<slug>.json    BirdNET-Go answer (TTL = refresh_min)
  img/                  fetched originals and resized copies
  gen/                  AI paintings, their credits, the daily budget, the
                        last failure per bird and which ones replace a plate
  gen/archive/          earlier paintings kept when a bird is repainted
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import io
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
USER_AGENT = "tesserae-backyard-birds/0.1 (+https://github.com/dmellok/tesserae)"
HTTP_TIMEOUT_S = 12
IMG_MAX_BYTES = 12_000_000
INAT_TAXA = "https://api.inaturalist.org/v1/taxa"
COMMONS_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath/"
FAL_EDIT = "https://fal.run/fal-ai/nano-banana/edit"
GEN_DAILY_CAP = 12
# The daily limit in force: the widget setting, carried as a context variable
# so the painting threads (which copy the context) and the admin page see the
# same limit without threading it through every call.
GEN_CAPS = (6, 12, 24, 48, 96)
_CAP: contextvars.ContextVar[int] = contextvars.ContextVar(
    "birds_cap", default=GEN_DAILY_CAP
)


def _daily_cap(settings: dict[str, Any] | None) -> int:
    """The widget setting's daily limit on new AI paintings, or the default."""
    v = _int((settings or {}).get("gen_daily_cap"), GEN_DAILY_CAP, 1, 1000)
    return v if v in GEN_CAPS else GEN_DAILY_CAP


GEN_TIMEOUT_S = 120
# A failed painting is not retried for this long, so a bad key or an empty
# fal balance costs one attempt per bird, not one per refresh.
GEN_RETRY_S = 6 * 3600

ERR_UNREACHABLE = "Couldn't reach BirdNET-Go at {url}."
ERR_AUTH = "BirdNET-Go asked for a login. Add an API token in this cell's settings."
NOTE_NO_KEY = "AI paintings need a fal.ai key in this widget's settings."
NOTE_PAINTING = "Painting {name} with fal.ai; it appears on a later refresh."
NOTE_GEN_FAILED = "AI painting of {name} failed: {why}"
NOTE_GEN_CAP = "AI paintings: today's limit of {cap} is used up."

LAYOUTS = ("list", "latest", "gallery", "chart", "plate", "bleed", "cluster")
CAPTIONS = (
    "band",
    "editorial",
    "notebook",
    "tag",
    "poster",
    "folio",
    "museum",
    "figure",
    "cyanotype",
    "stamp",
    "woodblock",
    "cameo",
    "nouveau",
)
PICTURES = ("plate_photo", "plate_gen", "gen", "photo", "none")
# A photo shown among plates is toned to a warm monotone by default.
PHOTO_TONES = ("toned", "light", "natural")
# What the panel can show. A page is composed once and quantised per device
# afterwards, so the widget cannot learn this at render time; the cell says.
LEVELS = ("colour", "gray16", "gray4", "mono")
CHART_LABELS = ("none", "counts", "peak")

# Longest edge, in pixels, of a picture for each layout. Big enough for a
# 13.3" panel at full bleed, small enough that a list stays a few hundred KB.
SIZE = {
    "list": 160,
    "gallery": 520,
    "latest": 1100,
    "plate": 1100,
    "bleed": 1600,
    "cluster": 1600,
}

# Fonts each caption style needs, inlined only when that style is on screen.
FONT_GROUPS = {
    "editorial": ("playfair", "archivo_narrow"),
    "notebook": ("caveat",),
    "tag": ("special_elite",),
    "poster": ("archivo_black", "archivo_narrow"),
    "engraved": ("im_fell", "im_fell_sc"),
    "folio": ("im_fell", "im_fell_sc"),
    "cluster": ("im_fell", "im_fell_sc"),
    "museum": ("im_fell", "im_fell_sc", "caveat"),
    "figure": ("im_fell", "im_fell_sc"),
    "cyanotype": ("im_fell", "im_fell_sc"),
    "stamp": ("im_fell", "im_fell_sc"),
    "woodblock": ("im_fell", "im_fell_sc"),
    "cameo": ("im_fell", "im_fell_sc"),
    "nouveau": ("playfair", "archivo_narrow"),
}


# ----------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------


def _tz(options: dict[str, Any]) -> tzinfo | None:
    """IANA zone carried on the location option, or None for the server's."""
    for value in (options.get("timezone"), options.get("location")):
        if isinstance(value, dict):
            value = value.get("timezone")
        if isinstance(value, str) and value.strip():
            with contextlib.suppress(ValueError, KeyError, OSError):
                return ZoneInfo(value.strip())
    return None


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return default


def _is_progress(note: str) -> bool:
    """True for a notice about work under way rather than one to act on."""
    return note.startswith(("Painting ", "AI paintings: today's limit"))


def _choice(value: Any, allowed: tuple[str, ...]) -> str:
    v = str(value or "").strip()
    return v if v in allowed else allowed[0]


def _hhmm(value: str | None) -> str:
    """'2026-10-11 07:42:10', '2026-10-11T07:42:10+11:00' or '07:42:10' -> '07:42'."""
    if not value:
        return ""
    s = str(value).replace("T", " ")
    s = s.split(" ")[-1] if " " in s else s
    return s[:5] if len(s) >= 5 and s[2] == ":" else ""


def _minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _ago_min(hhmm: str, now: datetime) -> int | None:
    mins = _minutes(hhmm)
    if mins is None:
        return None
    diff = now.hour * 60 + now.minute - mins
    return diff if diff >= 0 else None


def _write(path: Path, data: bytes) -> None:
    """Write via a temp file and rename, so a concurrent render never reads a
    half-written image."""
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{id(data)}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "x"


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------


def _http(
    url: str,
    token: str = "",
    accept: str = "application/json",
    *,
    auth_scheme: str = "Bearer",
    body: bytes | None = None,
    timeout: float = HTTP_TIMEOUT_S,
    detail: bool = False,
) -> tuple[bytes | None, str | None]:
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if token:
        headers["Authorization"] = f"{auth_scheme} {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(IMG_MAX_BYTES + 1)
            return (None, "too large") if len(data) > IMG_MAX_BYTES else (data, None)
    except urllib.error.HTTPError as err:
        if err.code in (401, 403) and not detail:
            return None, "auth"
        why = f"HTTP {err.code}"
        if detail:
            with contextlib.suppress(Exception):
                body = json.loads(err.read(20_000).decode("utf-8", "replace"))
                msg = body.get("detail") if isinstance(body, dict) else None
                if isinstance(msg, list) and msg and isinstance(msg[0], dict):
                    msg = msg[0].get("msg")
                if isinstance(msg, str) and msg.strip():
                    why += f": {msg.strip()[:160]}"
        return None, why
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, "unreachable"


def _json(url: str, token: str = "", **kw: Any) -> tuple[Any | None, str | None]:
    body, err = _http(url, token, **kw)
    if err or body is None:
        return None, err
    try:
        return json.loads(body.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "not json"


# ----------------------------------------------------------------------
# BirdNET-Go
# ----------------------------------------------------------------------


def _base_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if raw and "://" not in raw:
        raw = "http://" + raw
    return raw


def _from_birdnet(
    base: str, token: str, min_conf: int
) -> tuple[dict[str, Any] | None, str | None]:
    daily, err = _json(
        f"{base}/api/v2/analytics/species/daily?min_confidence={min_conf}", token
    )
    if err == "auth":
        return None, ERR_AUTH
    if err == "unreachable":
        return None, ERR_UNREACHABLE.format(url=base)
    if err:
        return None, f"BirdNET-Go answered {err}. Is the URL right?"
    if not isinstance(daily, list):
        return (
            None,
            "BirdNET-Go's species summary wasn't in the expected shape. Is the URL right?",
        )

    species = []
    for s in daily:
        if not isinstance(s, dict) or not s.get("common_name"):
            continue
        hourly = s.get("hourly_counts")
        species.append(
            {
                "name": str(s["common_name"]),
                "sci": str(s.get("scientific_name") or ""),
                "count": int(s.get("count") or 0),
                "hourly": [int(h or 0) for h in hourly][:24]
                if isinstance(hourly, list)
                else [],
                "first": _hhmm(s.get("first_heard")),
                "last": _hhmm(s.get("latest_heard")),
                "conf": round(float(s.get("max_confidence") or 0), 2),
                "new": bool(s.get("is_new_species")),
                "thumb": str(s.get("thumbnail_url") or ""),
            }
        )

    recent_raw, _rerr = _json(f"{base}/api/v2/detections/recent?limit=20", token)
    recent = []
    for d in recent_raw if isinstance(recent_raw, list) else []:
        if not isinstance(d, dict) or not d.get("commonName"):
            continue
        if float(d.get("confidence") or 0) * 100 < min_conf:
            continue
        recent.append(
            {
                "name": str(d["commonName"]),
                "time": _hhmm(d.get("time") or d.get("timestamp")),
                "conf": round(float(d.get("confidence") or 0), 2),
            }
        )
    return {"species": species, "recent": recent}, None


# ----------------------------------------------------------------------
# Demo mode
# ----------------------------------------------------------------------

# (common name, scientific name, weight, pattern). Names follow the eBird /
# Clements spelling BirdNET uses. Pattern: dawn (chorus peak, dusk echo),
# day (steady through daylight), night (owls).
_DEMO: dict[str, list[tuple[str, str, float, str]]] = {
    "au": [
        ("Australian Magpie", "Gymnorhina tibicen", 9, "dawn"),
        ("Rainbow Lorikeet", "Trichoglossus moluccanus", 8, "day"),
        ("Noisy Miner", "Manorina melanocephala", 10, "day"),
        ("Red Wattlebird", "Anthochaera carunculata", 6, "day"),
        ("Laughing Kookaburra", "Dacelo novaeguineae", 3, "dawn"),
        ("Superb Fairywren", "Malurus cyaneus", 4, "dawn"),
        ("Magpie-lark", "Grallina cyanoleuca", 4, "day"),
        ("Gray Butcherbird", "Cracticus torquatus", 3, "dawn"),
        ("Spotted Pardalote", "Pardalotus punctatus", 3, "day"),
        ("Sulphur-crested Cockatoo", "Cacatua galerita", 3, "day"),
        ("Eastern Spinebill", "Acanthorhynchus tenuirostris", 2, "dawn"),
        ("Southern Boobook", "Ninox boobook", 2, "night"),
    ],
    "eu": [
        ("Eurasian Blackbird", "Turdus merula", 9, "dawn"),
        ("European Robin", "Erithacus rubecula", 8, "dawn"),
        ("Great Tit", "Parus major", 7, "day"),
        ("Eurasian Blue Tit", "Cyanistes caeruleus", 6, "day"),
        ("Common Chaffinch", "Fringilla coelebs", 5, "dawn"),
        ("House Sparrow", "Passer domesticus", 7, "day"),
        ("Eurasian Wren", "Troglodytes troglodytes", 4, "dawn"),
        ("Common Wood-Pigeon", "Columba palumbus", 5, "day"),
        ("Eurasian Magpie", "Pica pica", 3, "day"),
        ("Carrion Crow", "Corvus corone", 3, "day"),
        ("European Greenfinch", "Chloris chloris", 2, "day"),
        ("Tawny Owl", "Strix aluco", 2, "night"),
    ],
    "na": [
        ("American Robin", "Turdus migratorius", 9, "dawn"),
        ("Northern Cardinal", "Cardinalis cardinalis", 8, "dawn"),
        ("Blue Jay", "Cyanocitta cristata", 6, "day"),
        ("Black-capped Chickadee", "Poecile atricapillus", 6, "day"),
        ("Mourning Dove", "Zenaida macroura", 5, "dawn"),
        ("House Finch", "Haemorhous mexicanus", 6, "day"),
        ("Song Sparrow", "Melospiza melodia", 5, "dawn"),
        ("Carolina Wren", "Thryothorus ludovicianus", 4, "dawn"),
        ("American Crow", "Corvus brachyrhynchos", 4, "day"),
        ("Downy Woodpecker", "Dryobates pubescens", 3, "day"),
        ("Dark-eyed Junco", "Junco hyemalis", 3, "day"),
        ("Great Horned Owl", "Bubo virginianus", 2, "night"),
    ],
}


def _region(options: dict[str, Any]) -> str:
    try:
        lat = float(options.get("latitude"))
        lon = float(options.get("longitude"))
    except (TypeError, ValueError):
        return "na"
    if lat < -8 and lon > 100:
        return "au"
    if -30 <= lon <= 60 and lat > 30:
        return "eu"
    return "na"


def _activity(pattern: str, hour: int) -> float:
    """Relative calling rate for an hour of the day."""
    if pattern == "night":
        return 1.0 if hour >= 20 or hour <= 4 else 0.0
    if hour < 5 or hour > 20:
        return 0.0
    if pattern == "dawn":
        return {5: 1.0, 6: 2.6, 7: 2.2, 8: 1.3, 18: 0.9, 19: 1.0}.get(hour, 0.45)
    return 0.5 if hour in (5, 20) else 1.0


def _demo(options: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A day of detections up to now, the same on every call within a minute.

    Hourly counts are seeded per (date, hour), so a finished hour never
    changes between refreshes and the current hour only grows as its minutes
    pass. One bird a day is flagged new so that state can be seen too."""
    region = _region(options)
    day_seed = now.strftime("%Y%m%d") + region
    birds = _DEMO[region]
    new_idx = random.Random(day_seed + "new").randrange(len(birds))
    species = []
    log: list[tuple[int, str]] = []  # (minute of day, name)
    for i, (name, sci, weight, pattern) in enumerate(birds):
        # some species stay away on some days, so the list isn't identical daily
        if (
            i >= 6
            and pattern != "night"
            and random.Random(day_seed + name).random() < 0.35
        ):
            continue
        hourly = [0] * 24
        for hour in range(now.hour + 1):
            rate = weight * _activity(pattern, hour)
            if rate <= 0:
                continue
            rng = random.Random(f"{day_seed}{name}{hour}")
            k = min(59, int(rate * 0.6 + rng.random() * rate * 0.6))
            minutes = sorted(rng.sample(range(60), k))
            if hour == now.hour:
                minutes = [m for m in minutes if m <= now.minute]
            hourly[hour] = len(minutes)
            log.extend((hour * 60 + m, name) for m in minutes)
        if not sum(hourly):
            continue
        heard = sorted(m for m, n in log if n == name)
        rng = random.Random(day_seed + name + "conf")
        species.append(
            {
                "name": name,
                "sci": sci,
                "count": sum(hourly),
                "hourly": hourly,
                "first": f"{heard[0] // 60:02d}:{heard[0] % 60:02d}",
                "last": f"{heard[-1] // 60:02d}:{heard[-1] % 60:02d}",
                "conf": round(0.78 + rng.random() * 0.2, 2),
                "new": i == new_idx,
                "thumb": "",
            }
        )
    log.sort(reverse=True)
    recent = []
    for minute, name in log[:20]:
        rng = random.Random(f"{day_seed}{name}{minute}")
        recent.append(
            {
                "name": name,
                "time": f"{minute // 60:02d}:{minute % 60:02d}",
                "conf": round(0.72 + rng.random() * 0.26, 2),
            }
        )
    return {"species": species, "recent": recent, "region": region}


# ----------------------------------------------------------------------
# Pictures
# ----------------------------------------------------------------------


def _pack() -> dict[str, Any]:
    with contextlib.suppress(OSError, json.JSONDecodeError):
        data = json.loads((HERE / "plates.json").read_text(encoding="utf-8"))
        plates = data.get("plates")
        if isinstance(plates, dict):
            return plates
    return {}


def _cached_get(data_dir: Path, url: str, token: str = "") -> bytes | None:
    """Fetch an image once and keep it; a failure is remembered for a day so a
    dead link isn't retried on every refresh."""
    folder = data_dir / "img"
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode()).hexdigest()[:20]
    path, miss = folder / f"{key}.bin", folder / f"{key}.miss"
    if path.exists():
        with contextlib.suppress(OSError):
            return path.read_bytes()
    if miss.exists() and time.time() - miss.stat().st_mtime < 86400:
        return None
    body, err = _http(url, token, accept="image/*", timeout=40)
    if err or not body:
        with contextlib.suppress(OSError):
            miss.touch()
        return None
    _write(path, body)
    return body


def _render(
    raw: bytes,
    size: int,
    box: list[float] | None = None,
    cache: Path | None = None,
    levels: str = "colour",
) -> str:
    """Crop to ``box`` (fractions), fit the longest edge to ``size`` and return a
    JPEG data URI. Cached per (source, box, size, levels) when ``cache`` is
    given. For a greyscale or black-and-white panel the picture is converted
    here, with the contrast stretched so a watercolour plate still reads once
    the panel dithers it; the harder the panel, the harder the stretch."""
    if cache is not None and levels != "colour":
        cache = cache.with_name(f"{cache.stem}_{levels}{cache.suffix}")
    if cache is not None and cache.exists():
        with contextlib.suppress(OSError):
            return (
                "data:image/jpeg;base64,"
                + base64.b64encode(cache.read_bytes()).decode()
            )
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow ships with Tesserae
        return "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception:  # noqa: BLE001 - any undecodable image is just skipped
        return ""
    if box and len(box) == 4:
        w, h = im.size
        x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in box)
        if x1 - x0 > 0.05 and y1 - y0 > 0.05:
            im = im.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    im.thumbnail((size, size), Image.Resampling.LANCZOS)
    if levels != "colour":
        cutoff = {"gray16": 0.5, "gray4": 1.0, "mono": 1.0}.get(levels, 1.0)
        im = ImageOps.autocontrast(im.convert("L"), cutoff=cutoff)
        if levels == "mono":
            # The panel dithers every mid-tone to a texture, and a plate's
            # reds and greens all sit in the mid-tones, so lift them a little
            # rather than pushing contrast, which turned the bird into a blot.
            im = im.point([round(255 * (i / 255) ** 0.8) for i in range(256)])
    out = io.BytesIO()
    im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    data = out.getvalue()
    if cache is not None:
        _write(cache, data)
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def _paper(src: str, cache: Path, levels: str = "colour") -> str:
    """The page colour around a plate (or a painting in the plate style), as
    #rrggbb: the median of a thin band along the picture's edges. Full bleed
    fills the panel with it so the whole plate fits without a visible edge."""
    if levels == "mono":
        return "#ffffff"
    if levels != "colour":
        cache = cache.with_name(f"{cache.stem}_{levels}{cache.suffix}")
    with contextlib.suppress(OSError):
        hit = cache.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"#[0-9a-f]{6}", hit):
            return hit
    colour = "#f3ead6"
    try:
        from PIL import Image, ImageStat

        im = Image.open(io.BytesIO(base64.b64decode(src.split(",", 1)[1]))).convert(
            "RGB"
        )
        w, h = im.size
        b = max(2, min(w, h) // 40)
        bands = [
            im.crop((0, 0, w, b)),
            im.crop((0, h - b, w, h)),
            im.crop((0, 0, b, h)),
            im.crop((w - b, 0, w, h)),
        ]
        strip = Image.new("RGB", (sum(x.width * x.height for x in bands), 1))
        strip.putdata([px for band in bands for px in band.getdata()])
        r, g, bl = (int(v) for v in ImageStat.Stat(strip).median)
        colour = f"#{r:02x}{g:02x}{bl:02x}"
    except Exception:  # noqa: BLE001 - fall back to the default cream
        pass
    _write(cache, colour.encode())
    return colour


def _plate(
    sci: str, pack: dict[str, Any], size: int, data_dir: Path, levels: str = "colour"
) -> dict[str, Any] | None:
    entry = pack.get(sci)
    if not isinstance(entry, dict) or not entry.get("file"):
        return None
    url = COMMONS_FILE + urllib.parse.quote(str(entry["file"])) + "?width=2000"
    raw = _cached_get(data_dir, url)
    if not raw:
        return None
    key = hashlib.sha1(
        f"{entry['file']}|{entry.get('box')}|{size}".encode()
    ).hexdigest()[:20]
    src = _render(
        raw, size, entry.get("box"), data_dir / "img" / f"p_{key}.jpg", levels
    )
    if not src:
        return None
    box = entry.get("box") or [0, 0, 1, 1]
    fx, fy = entry.get("focus") or [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
    # focus is stored against the whole scan; the client needs it inside the crop
    bw, bh = max(0.01, box[2] - box[0]), max(0.01, box[3] - box[1])
    focus = [
        round(min(1, max(0, (fx - box[0]) / bw)), 3),
        round(min(1, max(0, (fy - box[1]) / bh)), 3),
    ]
    source = str(entry.get("source") or "Classic plate")
    paper = _paper(src, data_dir / "img" / f"p_{key}.paper", levels)
    return {
        "src": src,
        "kind": "plate",
        "credit": f"{source} · public domain",
        "focus": focus,
        "paper": paper,
    }


def _inat_ref(sci: str, data_dir: Path) -> tuple[str, str]:
    """(image URL, credit) of the first openly licensed iNaturalist photo of a
    species, cached for a month. The default photo is often all rights
    reserved, so the taxon's whole photo list is walked."""
    folder = data_dir / "img"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"inat_{_slug(sci)}.json"
    with contextlib.suppress(OSError, json.JSONDecodeError):
        if time.time() - path.stat().st_mtime < 30 * 86400:
            hit = json.loads(path.read_text(encoding="utf-8"))
            return str(hit.get("url", "")), str(hit.get("credit", ""))
    url = credit = ""
    q = urllib.parse.urlencode({"q": sci, "rank": "species", "per_page": 1})
    found, err = _json(f"{INAT_TAXA}?{q}")
    results = found.get("results") if isinstance(found, dict) and not err else None
    if results:
        taxon, err = _json(f"{INAT_TAXA}/{int(results[0].get('id') or 0)}")
        rows = taxon.get("results") if isinstance(taxon, dict) and not err else None
        photos = (
            [tp.get("photo") or {} for tp in (rows[0].get("taxon_photos") or [])]
            if rows
            else []
        )
        photo = next((p for p in photos if p.get("license_code")), None)
        if photo:
            url = str(photo.get("url") or "").replace("/square.", "/large.")
            who = (
                str(photo.get("attribution_name") or "").strip()
                or str(photo.get("attribution") or "")
                .removeprefix("(c) ")
                .split(",")[0]
                .strip()
            )
            credit = (
                f"© {who} ({str(photo['license_code']).upper()}) · iNaturalist"
                if who
                else "iNaturalist"
            )
    if results is not None:
        _write(path, json.dumps({"url": url, "credit": credit}).encode())
    return url, credit


def _photo(
    row: dict[str, Any],
    size: int,
    base: str,
    token: str,
    data_dir: Path,
    levels: str = "colour",
) -> dict[str, Any] | None:
    if row.get("thumb") and base:
        thumb = str(row["thumb"])
        url = thumb if "://" in thumb else base + "/" + thumb.lstrip("/")
        # the token is for BirdNET-Go alone, never a thumbnail host it links to
        raw = _cached_get(data_dir, url, token if url.startswith(base + "/") else "")
        credit = "via BirdNET-Go"
    else:
        url, credit = _inat_ref(row["sci"], data_dir)
        raw = _cached_get(data_dir, url) if url else None
    if not raw:
        return None
    key = hashlib.sha1(f"{url}|{size}".encode()).hexdigest()[:20]
    src = _render(raw, size, None, data_dir / "img" / f"f_{key}.jpg", levels)
    return (
        {"src": src, "kind": "photo", "credit": credit, "focus": [0.5, 0.42]}
        if src
        else None
    )


# Art styles for AI paintings: (label, how to paint it). Each style keeps its
# own painting per bird, so switching a cell's style never repaints the old one.
GEN_STYLES: dict[str, tuple[str, str]] = {
    "gould": (
        "Hand-coloured lithograph (Gould)",
        "a plate from a nineteenth-century natural history book: a hand-coloured "
        "lithograph in the manner of John Gould. Soft watercolour washes over fine "
        "engraved linework, plain cream paper background, generous margins",
    ),
    "audubon": (
        "Watercolour after Audubon",
        "a life-size watercolour in the manner of John James Audubon's Birds of America: "
        "a lively natural pose among a few leaves, watercolour and pastel with fine "
        "detail in the feathers, plain cream paper background",
    ),
    "woodblock": (
        "Japanese woodblock print",
        "a Japanese kacho-e woodblock print in the manner of Ohara Koson: flat areas of "
        "colour with a fine dark keyline, a soft graded background, a single flowering "
        "branch, the texture of washi paper",
    ),
    "engraving": (
        "Black-ink engraving",
        "a black-ink copperplate engraving from a Victorian encyclopaedia of natural "
        "history: fine cross-hatching and stippling, no colour at all, plain white paper",
    ),
    "linocut": (
        "Linocut",
        "a bold linocut print in black ink with one muted colour: strong carved lines, "
        "simple shapes, a little texture from the block, plain off-white paper",
    ),
    "grumpy": (
        "Grumpy (plate style)",
        "a hand-coloured lithograph from a nineteenth-century natural history book, soft "
        "watercolour washes over fine engraved linework on warm cream paper, of a comically "
        "plump bird: an almost perfectly round ball of feathers with a big head, a stubby tail "
        "and small feet, like a cartoon game character. Give it a very expressive, furious "
        "face: heavy dark brows of feathers slanting down over narrowed eyes, a hard glare "
        "and a determined scowl. Paint all of it with real, detailed feathers, keeping the "
        "species' true colours, markings and bill so it is still plainly that bird",
    ),
    "sketch": (
        "Field sketch",
        "a naturalist's field sketch in graphite with a light watercolour wash: loose "
        "construction lines, a few annotations of shape only, on cream sketchbook paper",
    ),
}
GEN_STYLE_IDS = tuple(GEN_STYLES)
GEN_BLEND = (
    "This picture is a collage of {n} birds, each painted on its own: {birds}. Repaint it "
    "as one seamless picture, {style}, with all the birds together in a single natural "
    "scene: the birds gathered in an oval cluster, like a Victorian vignette, on branches "
    "and foliage that join them, one continuous background, the same light and paper "
    "throughout. Keep every bird's species, plumage, markings, pose and size as painted, "
    "and keep each about where it is in the oval. No text, no labels, no numbers, no "
    "border, no signature."
)
GEN_ONE = (
    "Paint the bird in the reference photo, the {name} ({sci}), as {style}. Keep the "
    "plumage, markings, bill and proportions exactly as in the photo, perched on a simple "
    "branch, the whole bird and its perch alone on plain, empty paper with a clear margin "
    "all round. No other birds, no scenery, no text, no lettering, no border, no signature."
)
# Most birds the blended cluster shows (the specimen sheet takes more).
CLUSTER_MAX = 6
# The cluster as a specimen sheet (each bird printed as painted) or blended
# into one scene by a final AI pass.
CLUSTER_STYLES = ("sheet", "numbered", "wreath", "notebook", "drawer", "blend")
# Layout version of the specimen sheet, part of its cache tag.
SHEET_LAYOUT = "sheet4"
# Most birds a specimen sheet shows.
SHEET_MAX = 24
# Label type size as a fraction of the sheet's height.
SHEET_LABEL = 0.017

# Paintings being made right now, by key, so two renders never start the same
# one. Paintings run in daemon threads that outlive the fetch that started
# them: fal takes 15 to 60 seconds and Tesserae gives a fetch about six.
_PAINTING: set[str] = set()
_PAINTING_LOCK = threading.Lock()


def _pkey(slug: str, style: str) -> str:
    """File key for a bird's painting in a style. The Gould style keeps the
    bare slug, so paintings made before styles existed are still found."""
    return slug if style == "gould" else f"{slug}--{style}"


def _blend_key(style: str) -> str:
    """File key for the cluster's blended picture in a style."""
    return f"cluster--{style}"


def _gen_dir(data_dir: Path) -> Path:
    folder = data_dir / "gen"
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read_json(path: Path, default: Any) -> Any:
    with contextlib.suppress(OSError, json.JSONDecodeError):
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _budget_used(folder: Path) -> int:
    used = _read_json(folder / "budget.json", {})
    return int(used.get(time.strftime("%Y-%m-%d"), 0)) if isinstance(used, dict) else 0


def _spend_budget(folder: Path) -> None:
    today = time.strftime("%Y-%m-%d")
    _write(
        folder / "budget.json",
        json.dumps({today: _budget_used(folder) + 1}).encode(),
    )


def _prefers_painting(data_dir: Path, key: str) -> bool:
    """True when the admin page marked this painting to replace the bird's plate."""
    prefs = _read_json(_gen_dir(data_dir) / "prefs.json", {})
    return bool(isinstance(prefs, dict) and (prefs.get(key) or {}).get("prefer"))


def _drop_derived(folder: Path, key: str) -> None:
    """Remove the resized copies and paper colours made from a painting,
    after it is replaced or deleted."""
    pattern = re.compile(rf"{re.escape(key)}(_\d+(_\w+)?\.jpg|(_\w+)?\.paper)")
    with contextlib.suppress(OSError):
        for f in folder.iterdir():
            if pattern.fullmatch(f.name):
                with contextlib.suppress(OSError):
                    f.unlink()


def _paint(job: dict[str, Any], data_dir: Path, fal_key: str) -> str | None:
    """Make one bird's painting and keep it under job["key"]. Blocking: run it
    through _start_painting. ``job`` is {key, style, name, sci}. Returns None,
    or why it failed (also kept in <key>.err for renders and the admin page)."""
    folder = _gen_dir(data_dir)
    key = job["key"]
    err_path = folder / f"{key}.err"
    label, how = GEN_STYLES.get(job.get("style") or "gould", GEN_STYLES["gould"])

    def fail(why: str) -> str:
        info = {k: job.get(k) for k in ("name", "sci", "style", "birds")}
        _write(
            err_path, json.dumps({"why": why, "at": int(time.time()), **info}).encode()
        )
        return why

    if job.get("collage"):
        # a blend: the collage of the birds' own paintings, repainted as one
        try:
            sheet = Path(job["collage"]).read_bytes()
        except OSError:
            return fail(
                "the collage to blend has gone; it is made again on the next refresh"
            )
        image_url = "data:image/jpeg;base64," + base64.b64encode(sheet).decode()
        birds = job.get("birds") or []
        prompt = GEN_BLEND.format(
            n=len(birds),
            birds="; ".join(f"the {b['name']} ({b['sci']})" for b in birds),
            style=how,
        )
        ref_credit = ""
    else:
        image_url, ref_credit = (
            _inat_ref(job["sci"], data_dir) if job.get("sci") else ("", "")
        )
        if not image_url:
            return fail("no openly licensed reference photo on iNaturalist")
        prompt = GEN_ONE.format(name=job["name"], sci=job["sci"], style=how)
    _spend_budget(folder)
    body = json.dumps(
        {
            "prompt": prompt,
            "image_urls": [image_url],
            "num_images": 1,
            "output_format": "jpeg",
            "aspect_ratio": job.get("aspect") or "3:4",
        }
    ).encode()
    answer, err = _json(
        FAL_EDIT,
        fal_key,
        auth_scheme="Key",
        body=body,
        timeout=GEN_TIMEOUT_S,
        detail=True,
    )
    if err:
        if err.startswith(("HTTP 401", "HTTP 403")):
            err = "fal.ai refused the key or the account has no balance (" + err + ")"
        return fail(err if err.startswith("fal") else f"fal.ai answered {err}")
    images = answer.get("images") if isinstance(answer, dict) else None
    out_url = str(images[0].get("url") or "") if images else ""
    raw, err = (
        _http(out_url, accept="image/*", timeout=60) if out_url else (None, "no image")
    )
    if not raw:
        return fail(f"fal.ai sent no picture ({err})")
    credit = f"AI painting, {label.split(' (')[0].lower()} (fal.ai nano-banana)"
    if job.get("collage"):
        credit += ", blended from each bird's own AI painting after iNaturalist photos"
    elif ref_credit:
        credit += f" after {ref_credit}"
    img = folder / f"{key}.jpg"
    if img.exists():  # a repaint: the old one is kept, not lost
        _archive(folder, key)
    meta = {
        "credit": credit,
        "made": int(time.time()),
        "style": job.get("style") or "gould",
        "name": job.get("name", ""),
        "sci": job.get("sci", ""),
    }
    if job.get("collage"):
        meta["birds"] = job.get("birds") or []
        meta["tag"] = job.get("tag", "")
    _write(folder / f"{key}.json", json.dumps(meta).encode())
    _write(img, raw)
    _drop_derived(folder, key)
    with contextlib.suppress(OSError):
        err_path.unlink()
    return None


def _archive(folder: Path, key: str) -> None:
    """Move a painting (and its credit) into gen/archive/."""
    arch = folder / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with contextlib.suppress(OSError):
        os.replace(folder / f"{key}.jpg", arch / f"{key}__{stamp}.jpg")
    with contextlib.suppress(OSError):
        os.replace(folder / f"{key}.json", arch / f"{key}__{stamp}.json")
    _drop_derived(folder, key)


def _start_painting(job: dict[str, Any], data_dir: Path, fal_key: str) -> bool:
    """Start a painting in the background. False if one is already running
    under this key. The thread carries the caller's context, so the widget's
    network capability scope still applies to it."""
    key = job["key"]
    with _PAINTING_LOCK:
        if key in _PAINTING:
            return False
        _PAINTING.add(key)

    def run() -> None:
        try:
            _paint(job, data_dir, fal_key)
        except Exception as err:  # noqa: BLE001 - a thread must not die loudly
            _write(
                _gen_dir(data_dir) / f"{key}.err",
                json.dumps({"why": str(err)[:200], "at": int(time.time())}).encode(),
            )
        finally:
            with _PAINTING_LOCK:
                _PAINTING.discard(key)

    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(run,), name=f"birds-paint-{key}", daemon=True
    ).start()
    return True


def _painting(
    key: str, size: int, data_dir: Path, levels: str = "colour"
) -> dict[str, Any] | None:
    """A kept painting, resized for the layout, or None."""
    folder = _gen_dir(data_dir)
    img = folder / f"{key}.jpg"
    if not img.exists():
        return None
    meta = _read_json(folder / f"{key}.json", {})
    meta = meta if isinstance(meta, dict) else {}
    try:
        raw = img.read_bytes()
    except OSError:
        return None
    src = _render(raw, size, None, folder / f"{key}_{size}.jpg", levels)
    if not src:
        return None
    return {
        "src": src,
        "kind": "generated",
        "credit": str(meta.get("credit") or "AI painting"),
        "focus": [0.5, 0.45],
        "paper": _paper(src, folder / f"{key}.paper", levels),
    }


def _may_paint(
    data_dir: Path, key: str, label: str, fal_key: str, allow_new: bool
) -> tuple[bool, str | None]:
    """(go ahead, notice) for starting a painting under ``key``: not while one
    is running, not within GEN_RETRY_S of a failure, not past the daily cap."""
    folder = _gen_dir(data_dir)
    with _PAINTING_LOCK:
        busy = key in _PAINTING
    if busy:
        return False, NOTE_PAINTING.format(name=label)
    failed = _read_json(folder / f"{key}.err", None)
    if (
        isinstance(failed, dict)
        and time.time() - float(failed.get("at") or 0) < GEN_RETRY_S
    ):
        return False, NOTE_GEN_FAILED.format(name=label, why=failed.get("why"))
    if not (allow_new and fal_key):
        return False, None
    if _budget_used(folder) >= _CAP.get():
        return False, NOTE_GEN_CAP.format(cap=_CAP.get())
    return True, None


def _generated(
    row: dict[str, Any],
    size: int,
    data_dir: Path,
    fal_key: str,
    allow_new: bool,
    levels: str = "colour",
    style: str = "gould",
) -> tuple[dict[str, Any] | None, bool, str | None]:
    """(picture, started_one, notice). Reads a kept painting, or starts one in
    the background when allowed: one new painting per fetch at most, and
    the daily limit, so a busy morning fills in over a few refreshes. A
    bird whose painting failed is left alone for GEN_RETRY_S and the failure
    is shown as a notice."""
    key = _pkey(_slug(row["sci"] or row["name"]), style)
    pic = _painting(key, size, data_dir, levels)
    if pic is not None:
        return pic, False, None
    go, note = _may_paint(data_dir, key, f"the {row['name']}", fal_key, allow_new)
    if not go:
        return None, False, note
    job = {"key": key, "style": style, "name": row["name"], "sci": row["sci"]}
    started = _start_painting(job, data_dir, fal_key)
    return None, started, NOTE_PAINTING.format(name=f"the {row['name']}")


def _attach_pictures(
    rows: list[dict[str, Any]],
    mode: str,
    size: int,
    base: str,
    token: str,
    fal_key: str,
    data_dir: Path,
    levels: str = "colour",
    style: str = "gould",
    allow_new: bool = True,
) -> list[str]:
    """Give each row a ``pic`` per the pictures option. Returns notices."""
    notices: list[str] = []
    if mode == "none" or not rows:
        return notices
    pack = _pack() if mode.startswith("plate") else {}
    wants_gen = mode in ("plate_gen", "gen")
    if wants_gen and not fal_key:
        notices.append(NOTE_NO_KEY)

    def key_of(row: dict[str, Any]) -> str:
        return _pkey(_slug(row["sci"] or row["name"]), style)

    def plate_for(row: dict[str, Any]) -> dict[str, Any] | None:
        # a painting marked on the admin page to replace the plate wins
        if _prefers_painting(data_dir, key_of(row)):
            pic = _painting(key_of(row), size, data_dir, levels)
            if pic is not None:
                return pic
        return _plate(row["sci"], pack, size, data_dir, levels)

    # Plates and photos are independent fetches, so they run side by side; a
    # generated painting is at most one new one per fetch, in the background.
    with ThreadPoolExecutor(max_workers=6) as pool:
        plates = list(pool.map(plate_for, rows)) if pack else [None] * len(rows)
    started = not allow_new
    for row, plate in zip(rows, plates, strict=True):
        pic = plate
        if pic is None and wants_gen:
            pic, did, note = _generated(
                row,
                size,
                data_dir,
                fal_key,
                allow_new=not started,
                levels=levels,
                style=style,
            )
            started = started or did
            if note and note not in notices:
                notices.append(note)
        elif pic is None and pack:
            # a painting kept from before still beats a photo among plates
            pic = _painting(key_of(row), size, data_dir, levels)
        row["pic"] = pic
    missing = [r for r in rows if not r.get("pic")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        photos = list(
            pool.map(lambda r: _photo(r, size, base, token, data_dir, levels), missing)
        )
    for row, pic in zip(missing, photos, strict=True):
        row["pic"] = pic
    return notices


# Collage layout version: part of a collage's cache tag, so changing the
# arrangement rebuilds the sheets (and lets the blend run again).
COLLAGE_LAYOUT = "oval2"


def _oval_slots(
    n: int, portrait: bool = False
) -> list[tuple[float, float, float, float]]:
    """Where each bird goes on the collage sheet, as (x, y, w, h) fractions,
    busiest first: an oval cluster like a Victorian vignette, following the
    sheet's own shape. With four or more birds the busiest sits large in the
    middle and the rest ring it, starting at the top; two or three birds
    share the ring between them. Neighbours may overlap a little: the
    cut-outs are multiplied onto the sheet, so overlaps read as ink."""
    import math

    def at(
        cx: float, cy: float, w: float, h: float
    ) -> tuple[float, float, float, float]:
        x = min(max(cx - w / 2, 0.0), 1 - w)
        y = min(max(cy - h / 2, 0.0), 1 - h)
        return (x, y, w, h)

    if n <= 1:
        return [at(0.5, 0.5, 0.84, 0.88)]
    if n == 2:
        if portrait:
            return [at(0.5, 0.29, 0.86, 0.5), at(0.5, 0.71, 0.86, 0.5)]
        return [at(0.29, 0.5, 0.5, 0.86), at(0.71, 0.5, 0.5, 0.86)]
    if n == 3:
        ring, rx, ry, w, h, centre = 3, 0.25, 0.24, 0.48, 0.52, []
    else:
        ring, rx, ry = n - 1, 0.33, 0.31
        w = min(0.36, 1.05 * 2 * math.pi * rx / ring)
        h = min(0.42, 1.05 * 2 * math.pi * ry / ring)
        centre = [at(0.5, 0.5, 0.44, 0.52)]
    slots = []
    for i in range(ring):
        a = math.radians(-90 + 360 * i / ring)
        slots.append(at(0.5 + rx * math.cos(a), 0.5 + ry * math.sin(a), w, h))
    return centre + slots


def _cutout(raw: bytes) -> tuple[Any, tuple[int, int, int]] | None:
    """(the bird cropped from its painting with the paper turned white, the
    paper colour). The painting is on plain paper, so anything far enough
    from the paper's colour is the bird (and its perch); dividing by the
    paper colour makes the paper white, so the cut-outs can be multiplied
    onto one sheet and read as printed on it, with no edges to hide."""
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageStat
    except ImportError:  # pragma: no cover - Pillow ships with Tesserae
        return None
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:  # noqa: BLE001 - an undecodable painting is skipped
        return None
    im.thumbnail((1200, 1200))
    w, h = im.size
    b = max(2, min(w, h) // 40)
    edge = Image.new("RGB", (w, b * 2 + h * 0))
    edge.paste(im.crop((0, 0, w, b)), (0, 0))
    edge.paste(im.crop((0, h - b, w, h)), (0, b))
    paper = tuple(int(v) for v in ImageStat.Stat(edge).median)
    diff = ImageChops.difference(im, Image.new("RGB", im.size, paper)).convert("L")
    mask = diff.point(lambda v: 255 if v > 34 else 0).filter(ImageFilter.MaxFilter(9))
    box = mask.getbbox()
    if not box:
        return None
    pad = max(w, h) // 40
    box = (
        max(0, box[0] - pad),
        max(0, box[1] - pad),
        min(w, box[2] + pad),
        min(h, box[3] + pad),
    )
    crop = im.crop(box)
    # paper to white: each channel divided by the paper's own value
    lut = []
    for c in paper:
        lut += [min(255, round(i * 255 / max(1, c))) for i in range(256)]
    return crop.point(lut), paper  # type: ignore[return-value]


def _collage(
    keys: list[str], aspect: str, size: int, data_dir: Path, levels: str
) -> dict[str, Any] | None:
    """The paintings under ``keys`` cut out and arranged on one sheet, busiest
    first: {src, boxes ([x, y, w, h] fractions), paper, tag, sheet (the
    full-size colour file)}. Cached on the paintings it is made of, so it is
    rebuilt only when one of them changes."""
    folder = _gen_dir(data_dir)
    stamps = []
    for k in keys:
        with contextlib.suppress(OSError):
            stamps.append(f"{k}:{int((folder / f'{k}.jpg').stat().st_mtime)}")
    if len(stamps) != len(keys) or not keys:
        return None
    tag = hashlib.sha1(
        f"{'|'.join(stamps)}|{aspect}|{size}|{COLLAGE_LAYOUT}".encode()
    ).hexdigest()[:16]
    sheet_path, info_path = (
        folder / f"collage_{tag}.jpg",
        folder / f"collage_{tag}.json",
    )
    info = _read_json(info_path, None)
    if not (sheet_path.exists() and isinstance(info, dict)):
        try:
            from PIL import Image, ImageChops
        except ImportError:  # pragma: no cover
            return None
        cuts = []
        for k in keys:
            with contextlib.suppress(OSError):
                got = _cutout((folder / f"{k}.jpg").read_bytes())
                if got:
                    cuts.append(got)
        if len(cuts) != len(keys):
            return None
        portrait = aspect == "3:4"
        W, H = (size * 3 // 4, size) if portrait else (size, size * 3 // 4)
        avg = [sum(p[i] for _c, p in cuts) // len(cuts) for i in range(3)]
        sheet = Image.new("RGB", (W, H), tuple(avg))
        slots = _oval_slots(len(cuts), portrait)
        boxes = []
        for (cut, _p), (x, y, sw, sh) in zip(cuts, slots, strict=False):
            m = 0.03  # a little air round each bird
            cx, cy, cw, ch = (
                (x + sw * m) * W,
                (y + sh * m) * H,
                sw * (1 - 2 * m) * W,
                sh * (1 - 2 * m) * H,
            )
            scale = min(cw / cut.width, ch / cut.height)
            fit = cut.resize(
                (max(1, int(cut.width * scale)), max(1, int(cut.height * scale))),
                Image.Resampling.LANCZOS,
            )
            px, py = int(cx + (cw - fit.width) / 2), int(cy + (ch - fit.height) / 2)
            region = sheet.crop((px, py, px + fit.width, py + fit.height))
            sheet.paste(ImageChops.multiply(region, fit), (px, py))
            boxes.append(
                [
                    round(px / W, 4),
                    round(py / H, 4),
                    round(fit.width / W, 4),
                    round(fit.height / H, 4),
                ]
            )
        out = io.BytesIO()
        sheet.save(out, "JPEG", quality=86)
        _write(sheet_path, out.getvalue())
        info = {"boxes": boxes}
        _write(info_path, json.dumps(info).encode())
        # older collages of the same birds are no use once a painting changes
        with contextlib.suppress(OSError):
            for old in folder.glob("collage_*"):
                if (
                    old.stem != f"collage_{tag}"
                    and time.time() - old.stat().st_mtime > 86400
                ):
                    old.unlink()
    try:
        raw = sheet_path.read_bytes()
    except OSError:
        return None
    src = _render(raw, size, None, folder / f"collage_{tag}_{size}.jpg", levels)
    if not src:
        return None
    paper = _paper(src, folder / f"collage_{tag}.paper", levels)
    return {
        "src": src,
        "boxes": info["boxes"],
        "paper": paper,
        "tag": tag,
        "sheet": str(sheet_path),
    }


def _specimen_sheet(
    species: list[dict[str, Any]],
    n: int,
    style: str,
    ratio: float,
    fal_key: str,
    data_dir: Path,
    levels: str,
    label: float = SHEET_LABEL,
    mode: str = "sheet",
) -> tuple[dict[str, Any] | None, list[str]]:
    """The cluster as a specimen sheet: today's busiest birds, each painted
    on its own (one new painting per refresh), cut out and printed on one
    page. No blend: the birds stay as painted. None until a bird is painted."""
    birds = [dict(b) for b in sorted(species, key=lambda s: -s["count"])[:n]]
    notices: list[str] = []
    if not fal_key:
        notices.append(NOTE_NO_KEY)
    started = False
    for b in birds:
        pic, did, note = _generated(
            b,
            SIZE["gallery"],
            data_dir,
            fal_key,
            allow_new=not started,
            levels=levels,
            style=style,
        )
        started = started or did
        b["painted"] = pic is not None
        if note and note not in notices:
            notices.append(note)
    painted = [b for b in birds if b["painted"]]
    if not painted:
        return None, notices
    keys = [_pkey(_slug(b["sci"] or b["name"]), style) for b in painted]
    made = _sheet(painted, keys, ratio, SIZE["cluster"], data_dir, levels, label, mode)
    if not made:
        return None, notices
    counts = {b["name"]: b["count"] for b in birds}
    label = GEN_STYLES[style][0].split(" (")[0].lower()
    return {
        "sheet": True,
        "pic": {
            "src": made["src"],
            "kind": "sheet",
            "paper": made["paper"],
            "credit": f"AI paintings, {label} (fal.ai nano-banana), after iNaturalist photos",
        },
        "birds": [{**b, "count": counts.get(b["name"], 0)} for b in made["birds"]],
        "ratio": made["ratio"],
        "label": made["label"],
        "mode": made["mode"],
        "waiting": [b["name"] for b in birds if not b["painted"]],
        "montage": False,
    }, notices


def _cluster(
    species: list[dict[str, Any]],
    n: int,
    every_h: float,
    mode: str,
    style: str,
    aspect: str,
    base: str,
    token: str,
    fal_key: str,
    data_dir: Path,
    levels: str,
) -> tuple[dict[str, Any], list[str]]:
    """Today's busiest birds in one seamless picture.

    Each bird gets its own AI painting (one new one per refresh, kept and
    reused everywhere). Once all of them are painted they are cut out and
    arranged on one sheet, and fal.ai repaints that collage as a single
    scene: the blend. The blend is kept, and made again when the line-up
    changes, at most once every ``every_h`` hours. Until the first blend is
    ready the cut-out collage stands in; with AI painting off, or before any
    bird is painted, it is a numbered montage of their plates and photos."""
    birds = [dict(b) for b in sorted(species, key=lambda s: -s["count"])[:n]]
    notices: list[str] = []
    counts = {s["sci"]: s["count"] for s in species}
    if mode in ("plate_gen", "gen"):
        if not fal_key:
            notices.append(NOTE_NO_KEY)
        started = False
        for b in birds:
            pic, did, note = _generated(
                b,
                SIZE["gallery"],
                data_dir,
                fal_key,
                allow_new=not started,
                levels=levels,
                style=style,
            )
            started = started or did
            b["painted"] = pic is not None
            if note and note not in notices:
                notices.append(note)
        painted = [b for b in birds if b["painted"]]
        keys = [_pkey(_slug(b["sci"] or b["name"]), style) for b in painted]
        made = (
            _collage(keys, aspect, SIZE["cluster"], data_dir, levels)
            if painted
            else None
        )
        waiting = [b["name"] for b in birds if not b["painted"]]
        bkey = _blend_key(style)
        blend = _painting(bkey, SIZE["cluster"], data_dir, levels)
        bmeta = _read_json(_gen_dir(data_dir) / f"{bkey}.json", {}) if blend else {}
        bmeta = bmeta if isinstance(bmeta, dict) else {}
        age_h = (time.time() - float(bmeta.get("made") or 0)) / 3600
        if (
            made
            and not waiting
            and len(painted) >= 2
            and bmeta.get("tag") != made["tag"]
            and (blend is None or age_h >= every_h)
            and not started
        ):
            label = "today's birds together"
            go, note = _may_paint(data_dir, bkey, label, fal_key, True)
            if go:
                _start_painting(
                    {
                        "key": bkey,
                        "style": style,
                        "collage": made["sheet"],
                        "tag": made["tag"],
                        "aspect": aspect,
                        "birds": [
                            {"name": b["name"], "sci": b["sci"]} for b in painted
                        ],
                    },
                    data_dir,
                    fal_key,
                )
                note = NOTE_PAINTING.format(name=label)
            if note and note not in notices:
                notices.append(note)
        if blend is not None:
            shown = [
                {"name": b["name"], "sci": b["sci"], "count": counts.get(b["sci"], 0)}
                for b in bmeta.get("birds") or []
            ]
            return {
                "pic": blend,
                "birds": shown,
                "montage": False,
                "waiting": [],
                "aspect": aspect,
            }, notices
        if made:
            label = GEN_STYLES[style][0].split(" (")[0].lower()
            pic = {
                "src": made["src"],
                "kind": "collage",
                "paper": made["paper"],
                "credit": f"AI paintings, {label} (fal.ai nano-banana), after iNaturalist photos",
            }
            shown = [
                {"name": b["name"], "sci": b["sci"], "count": b["count"]}
                for b in painted
            ]
            return {
                "pic": pic,
                "birds": shown,
                "montage": False,
                "waiting": waiting,
                "aspect": aspect,
            }, notices
    rows = [dict(b) for b in birds]
    montage_mode = "plate_photo" if mode in ("plate_gen", "gen") else mode
    notices += _attach_pictures(
        rows,
        montage_mode,
        SIZE["gallery"],
        base,
        token,
        fal_key,
        data_dir,
        levels,
        style,
        allow_new=False,
    )
    shown = [
        {"name": r["name"], "sci": r["sci"], "count": r["count"], "pic": r.get("pic")}
        for r in rows
    ]
    return {"pic": None, "birds": shown, "montage": True, "waiting": []}, notices


# ----------------------------------------------------------------------
# Specimen sheet
# ----------------------------------------------------------------------


def _lengths() -> dict[str, float]:
    with contextlib.suppress(OSError, json.JSONDecodeError):
        data = json.loads((HERE / "sizes.json").read_text(encoding="utf-8"))
        return {k: float(v) for k, v in (data.get("length_cm") or {}).items()}
    return {}


def _size_factor(sci: str, lengths: dict[str, float]) -> float:
    """How large a bird is drawn against a 20 cm bird: true to size, but
    compressed so a wren stays readable beside a crow."""
    cm = lengths.get(sci, 20.0)
    return max(0.55, min(2.0, (cm / 20.0) ** 0.75))


def _bird_mask(im: Any, paper: tuple[int, int, int]) -> Any:
    """The bird (and its perch) in a painting on plain paper, as an L mask.

    The paper is found by spreading in from the picture's edges over
    anything that looks like paper: close to the paper colour, or a smooth,
    grey, light wash such as a painted ground or shadow. What the spread
    cannot reach is the bird, so a white bird enclosed by its own outline
    stays whole even though its feathers are the paper's colour. Worked on a
    small copy for speed, then scaled back up."""
    import numpy as np
    from PIL import Image, ImageFilter

    w, h = im.size
    k = min(1.0, 320 / max(w, h))
    small = im.resize(
        (max(1, int(w * k)), max(1, int(h * k))), Image.Resampling.BILINEAR
    )
    a = np.asarray(small).astype(np.int16)
    diff = np.abs(a - np.array(paper, dtype=np.int16)).max(axis=2)
    lum = small.convert("L")
    hp = np.abs(
        np.asarray(lum, dtype=np.int16)
        - np.asarray(lum.filter(ImageFilter.GaussianBlur(2)), dtype=np.int16)
    )
    texture = np.asarray(
        Image.fromarray(np.clip(hp * 8, 0, 255).astype(np.uint8)).filter(
            ImageFilter.BoxBlur(2)
        )
    )
    sat = a.max(axis=2) - a.min(axis=2)
    L = np.asarray(lum, dtype=np.int16)
    paper_l = int(0.299 * paper[0] + 0.587 * paper[1] + 0.114 * paper[2])
    paperlike = diff < 24
    washlike = (texture < 24) & (sat < 26) & (L > paper_l - 95)
    cand = paperlike | washlike
    bg = np.zeros_like(cand)
    bg[0, :], bg[-1, :], bg[:, 0], bg[:, -1] = (
        cand[0, :],
        cand[-1, :],
        cand[:, 0],
        cand[:, -1],
    )
    for _ in range(4 * max(bg.shape)):
        grown = bg.copy()
        grown[1:, :] |= bg[:-1, :]
        grown[:-1, :] |= bg[1:, :]
        grown[:, 1:] |= bg[:, :-1]
        grown[:, :-1] |= bg[:, 1:]
        grown &= cand
        if (grown == bg).all():
            break
        bg = grown
    # a wash's own hard edge is texture, not paper: let the paper spread a
    # few more steps from the wash (never from plain paper, so a bird's pale
    # edge against plain paper is left alone) into pale grey pixels
    edge = (sat < 26) & (L > paper_l - 95)
    reach = bg & washlike & ~paperlike
    for _ in range(8):
        grown = reach.copy()
        grown[1:, :] |= reach[:-1, :]
        grown[:-1, :] |= reach[1:, :]
        grown[:, 1:] |= reach[:, :-1]
        grown[:, :-1] |= reach[:, 1:]
        reach = grown & (edge | bg)
    bg = bg | reach
    obj = Image.fromarray(((~bg) * 255).astype(np.uint8))
    # drop specks, close pinholes, back to full size
    obj = obj.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(5))
    return obj.resize((w, h), Image.Resampling.BILINEAR).point(
        lambda v: 255 if v > 127 else 0
    )


def _cutout_masked(raw: bytes) -> tuple[Any, Any, tuple[int, int, int]] | None:
    """(the bird cropped from its painting with the paper turned white and
    everything outside its outline whitened too, its mask, the paper
    colour). Whitening outside a soft mask drops any wash or vignette the
    painter left round the bird, so only the bird and its perch are printed
    on the sheet."""
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageStat
    except ImportError:  # pragma: no cover
        return None
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    im.thumbnail((1000, 1000))
    w, h = im.size
    b = max(2, min(w, h) // 40)
    edge = Image.new("RGB", (w, b * 2))
    edge.paste(im.crop((0, 0, w, b)), (0, 0))
    edge.paste(im.crop((0, h - b, w, h)), (0, b))
    paper = tuple(int(v) for v in ImageStat.Stat(edge).median)
    try:
        hard = _bird_mask(im, paper)
    except ImportError:  # pragma: no cover - numpy ships with Tesserae
        diff = ImageChops.difference(im, Image.new("RGB", im.size, paper)).convert("L")
        hard = diff.point(lambda v: 255 if v > 30 else 0)
        hard = hard.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MinFilter(5))
    # the crop box ignores specks: measure it on a slightly eroded mask
    box = hard.filter(ImageFilter.MinFilter(7)).getbbox() or hard.getbbox()
    if not box:
        return None
    pad = max(w, h) // 60
    box = (
        max(0, box[0] - pad),
        max(0, box[1] - pad),
        min(w, box[2] + pad),
        min(h, box[3] + pad),
    )
    lut = []
    for c in paper:
        lut += [min(255, round(i * 255 / max(1, c))) for i in range(256)]
    crop = im.crop(box).point(lut)
    mask = hard.crop(box).filter(ImageFilter.GaussianBlur(2))
    white = Image.new("RGB", crop.size, (255, 255, 255))
    return Image.composite(crop, white, mask), hard.crop(box), paper  # type: ignore[return-value]


def _rot_of(name: str, mode: str) -> float:
    """A small, steady tilt for the field notebook's taped-in birds."""
    if mode != "notebook":
        return 0.0
    return (int(hashlib.sha1(name.encode()).hexdigest()[:4], 16) % 9) - 4.0


def _pack_sheet(
    birds: list[dict[str, Any]],
    W: int,
    H: int,
    label: float = SHEET_LABEL,
    mode: str = "sheet",
) -> list[tuple[int, int, float]] | None:
    """Place cut-outs on a W x H page: biggest first, a little apart, each with
    room under it for its label. Where they go depends on ``mode``:

      sheet, numbered  the free spot nearest the middle of an oval
      wreath           a ring round an empty middle, nearest the ring's line
      notebook         rows across a ruled page, top left first

    Returns (x, y, scale) per bird, or None if numpy is missing; birds that
    do not fit at all get scale 0."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with Tesserae
        return None
    from PIL import Image, ImageFilter

    cell = 6
    gw, gh = W // cell, H // cell
    ys, xs = np.mgrid[0:gh, 0:gw]
    cx, cy, rx, ry = gw / 2, gh * 0.47, gw * 0.48, gh * 0.45
    oval = ((xs + 0.5 - cx) / rx) ** 2 + ((ys + 0.5 - cy) / ry) ** 2
    if mode == "wreath":
        cy = gh / 2
        oval = ((xs + 0.5 - cx) / rx) ** 2 + ((ys + 0.5 - cy) / ry) ** 2
        inside = (oval <= 1) & (oval >= 0.3**2)
    elif mode == "notebook":
        inside = (
            (xs >= gw * 0.12) & (xs < gw * 0.97) & (ys >= gh * 0.1) & (ys < gh * 0.97)
        )
    else:
        inside = oval <= 1
    label_rows = max(2, int(label * 1.5 * H / cell))
    gap = 2

    def footprint(b: dict[str, Any], scale: float) -> Any:
        m = b["mask"]
        if b.get("rot"):
            m = m.rotate(b["rot"], expand=True, resample=Image.Resampling.BILINEAR)
        tw, th = (
            max(1, int(m.width * scale / cell)),
            max(1, int(m.height * scale / cell)),
        )
        small = (
            m.resize((tw, th)).filter(ImageFilter.MaxFilter(2 * gap + 1))
            if min(tw, th) > 2
            else m.resize((tw, th))
        )
        bird = np.asarray(small) > 40
        # the label's strip under the bird, as wide as the bird or the label
        text = "00" if mode == "numbered" else b["name"]
        name_w = int(len(text) * label * 0.74 * H / cell) + 3
        fw = max(tw, name_w)
        fp = np.zeros((th + label_rows + gap, fw), dtype=bool)
        off = (fw - tw) // 2
        fp[:th, off : off + tw] = bird
        fp[
            th + 1 : th + label_rows, (fw - name_w) // 2 : (fw - name_w) // 2 + name_w
        ] = True
        return fp, off

    def order(ty: Any, tx: Any, fh: int, fw: int) -> tuple[Any, Any]:
        """(candidate order, the distance used to stop looking)."""
        if mode == "notebook":
            d = ty * gw + tx
            return np.argsort(d, axis=None), np.zeros_like(d, dtype=float)
        d = ((tx + fw / 2 - cx) / rx) ** 2 + ((ty + fh / 2 - cy) / ry) ** 2
        if mode == "wreath":
            # nearest the ring's line (65% of the way out), not the middle
            return np.argsort(np.abs(np.sqrt(d) - 0.65), axis=None), d
        return np.argsort(d, axis=None), d

    def attempt(unit: float) -> list[tuple[int, int, float]] | None:
        occ = np.zeros((gh, gw), dtype=bool)
        placed: list[tuple[int, int, float]] = []
        for b in birds:
            scale = unit * b["factor"]
            fp, off = footprint(b, scale)
            fh, fw = fp.shape
            if fh >= gh or fw >= gw:
                return None
            ty, tx = np.mgrid[0 : gh - fh : 2, 0 : gw - fw : 2]
            ranked, d = order(ty, tx, fh, fw)
            spot = None
            for k in ranked:
                if mode in ("sheet", "numbered") and d.flat[k] > 1.2:
                    break
                y, x = int(ty.flat[k]), int(tx.flat[k])
                if (occ[y : y + fh, x : x + fw] & fp).any():
                    continue
                if not inside[y : y + fh, x : x + fw][fp].all():
                    continue
                spot = (y, x)
                break
            if spot is None:
                return None
            y, x = spot
            occ[y : y + fh, x : x + fw] |= fp
            placed.append(((x + off) * cell, y * cell, scale))
        return placed

    area = sum((b["mask"].width * b["mask"].height) * b["factor"] ** 2 for b in birds)
    room = float(inside.sum()) * cell * cell
    unit = ((0.5 * room) / max(1, area)) ** 0.5
    best = None
    for _ in range(12):  # shrink until everything fits
        best = attempt(unit)
        if best:
            break
        unit *= 0.88
    if not best:
        return [(0, 0, 0.0)] * len(birds)
    for _ in range(9):  # then grow while it still fits, to fill the page
        bigger = attempt(unit * 1.07)
        if not bigger:
            break
        unit, best = unit * 1.07, bigger
    return best


def _drawer_grid(n: int, W: int, H: int) -> list[tuple[int, int, int, int]]:
    """Compartments of the specimen drawer, as (x, y, w, h) inside its frame."""
    import math

    frame = int(min(W, H) * 0.05)
    iw, ih = W - 2 * frame, H - 2 * frame
    cols = max(1, round(math.sqrt(n * iw / ih)))
    rows = math.ceil(n / cols)
    cols = math.ceil(n / rows)
    cw, ch = iw / cols, ih / rows
    return [
        (int(frame + c * cw), int(frame + r * ch), int(cw), int(ch))
        for r in range(rows)
        for c in range(cols)
    ][:n]


def _build_sheet(
    rows: list[dict[str, Any]],
    keys: list[str],
    ratio: float,
    size: int,
    label: float,
    folder: Path,
    tag: str,
    mode: str = "sheet",
) -> None:
    """Make the cluster page under ``tag`` in the look ``mode`` (slow: run it
    in a thread). Paper looks (sheet, numbered, wreath) multiply the cut-outs
    onto one sheet so they read as printed on it; the notebook tapes them
    onto ruled paper at a slight tilt; the drawer sets each in a compartment
    on felt."""
    sheet_path, info_path = folder / f"sheet_{tag}.jpg", folder / f"sheet_{tag}.json"
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
    except ImportError:  # pragma: no cover
        return
    lengths = _lengths()
    birds = []
    for r, k in zip(rows, keys, strict=True):
        with contextlib.suppress(OSError):
            got = _cutout_masked((folder / f"{k}.jpg").read_bytes())
            if got:
                cut, mask, paper = got
                birds.append(
                    {
                        "name": r["name"],
                        "sci": r["sci"],
                        "cut": cut,
                        "mask": mask,
                        "paper": paper,
                        "factor": _size_factor(r["sci"], lengths),
                        "rot": _rot_of(r["name"], mode),
                    }
                )
    if not birds:
        return
    W, H = (size, int(size / ratio)) if ratio >= 1 else (int(size * ratio), size)
    # biggest first: in the middle of a plate, on the ring of a wreath
    birds.sort(key=lambda b: -(b["factor"] ** 2) * b["mask"].width * b["mask"].height)
    avg = tuple(sum(b["paper"][i] for b in birds) // len(birds) for i in range(3))

    if mode == "drawer":
        sheet = Image.new("RGB", (W, H), (58, 44, 32))  # the drawer's wood
        draw = ImageDraw.Draw(sheet)
        cells = _drawer_grid(len(birds), W, H)
        line = max(2, int(min(W, H) * 0.006))
        for x, y, w, h in cells:
            draw.rectangle(
                (x, y, x + w, y + h),
                fill=(232, 224, 205),
                outline=(58, 44, 32),
                width=line,
            )
        # one scale for all, so they stay true to size against each other
        tag_h = label * 2.6 * H
        unit = min(
            min((w * 0.84) / b["cut"].width, (h - tag_h) * 0.84 / b["cut"].height)
            / b["factor"]
            for b, (_x, _y, w, h) in zip(birds, cells, strict=False)
        )
        spots = []
        for b, (x, y, w, h) in zip(birds, cells, strict=False):
            s = unit * b["factor"]
            bw, bh = b["cut"].width * s, b["cut"].height * s
            spots.append(
                (int(x + (w - bw) / 2), int(y + (h - tag_h - bh) / 2 + line), s)
            )
        anchors = [
            ((x + w / 2) / W, (y + h - tag_h * 0.95) / H) for x, y, w, h in cells
        ]
    else:
        spots = _pack_sheet(birds, W, H, label, mode)
        if spots is None:
            return
        sheet = Image.new("RGB", (W, H), avg if mode != "notebook" else (246, 239, 219))
        if mode == "notebook":
            draw = ImageDraw.Draw(sheet)
            step = max(8, int(H * 0.042))
            for y in range(int(H * 0.09), H, step):
                draw.line((0, y, W, y), fill=(201, 214, 227), width=max(1, H // 900))
            draw.line(
                (int(W * 0.1), 0, int(W * 0.1), H),
                fill=(214, 120, 110),
                width=max(1, H // 700),
            )
        anchors = []

    shown = []
    for i, (b, (x, y, scale)) in enumerate(zip(birds, spots, strict=True)):
        if scale <= 0:
            continue
        cut, mask = b["cut"], b["mask"]
        size_ = (max(1, int(cut.width * scale)), max(1, int(cut.height * scale)))
        fit = cut.resize(size_, Image.Resampling.LANCZOS)
        if mode in ("notebook", "drawer"):
            soft = mask.resize(size_).filter(ImageFilter.GaussianBlur(1.2))
            if b["rot"]:
                fit = fit.rotate(
                    b["rot"],
                    expand=True,
                    fillcolor=(255, 255, 255),
                    resample=Image.Resampling.BICUBIC,
                )
                soft = soft.rotate(
                    b["rot"], expand=True, resample=Image.Resampling.BILINEAR
                )
            sheet.paste(fit, (x, y), soft)
            if mode == "notebook":
                # two strips of tape over the top corners
                tape = Image.new(
                    "RGBA",
                    (max(8, fit.width // 4), max(6, H // 45)),
                    (226, 214, 170, 150),
                )
                for tx in (x - tape.width // 3, x + fit.width - tape.width * 2 // 3):
                    t = tape.rotate(-25 if tx < x else 25, expand=True)
                    sheet.paste(t, (tx, y - t.height // 3), t)
        else:
            region = sheet.crop((x, y, x + fit.width, y + fit.height))
            sheet.paste(ImageChops.multiply(region, fit), (x, y))
        fw, fh = fit.size
        ax, ay = anchors[i] if anchors else ((x + fw / 2) / W, (y + fh) / H)
        shown.append(
            {
                "name": b["name"],
                "sci": b["sci"],
                "box": [
                    round(x / W, 4),
                    round(y / H, 4),
                    round(fw / W, 4),
                    round(fh / H, 4),
                ],
                "at": [round(ax, 4), round(ay, 4)],
                "rot": b["rot"],
            }
        )
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=88)
    _write(sheet_path, out.getvalue())
    info = {
        "birds": shown,
        "w": W,
        "h": H,
        "ratio": round(ratio, 3),
        "label": label,
        "mode": mode,
    }
    _write(info_path, json.dumps(info).encode())
    with contextlib.suppress(OSError):
        for old in folder.glob("sheet_*"):
            if (
                not old.name.startswith(f"sheet_{tag}")
                and time.time() - old.stat().st_mtime > 86400
            ):
                old.unlink()


def _sheet(
    rows: list[dict[str, Any]],
    keys: list[str],
    ratio: float,
    size: int,
    data_dir: Path,
    levels: str,
    label: float = SHEET_LABEL,
    mode: str = "sheet",
) -> dict[str, Any] | None:
    """Today's painted birds as a specimen sheet: each painting cut out and
    printed on one page in an oval cluster, true to size against each
    other, named underneath. {src, birds: [{name, box, label}], paper}.
    Cached on the paintings it is made of."""
    folder = _gen_dir(data_dir)
    stamps = []
    for k in keys:
        with contextlib.suppress(OSError):
            stamps.append(f"{k}:{int((folder / f'{k}.jpg').stat().st_mtime)}")
    if len(stamps) != len(keys) or not keys:
        return None
    names = "|".join(r["name"] for r in rows)
    tag = hashlib.sha1(
        f"{'|'.join(stamps)}|{ratio:.3f}|{size}|{names}|{label:.4f}|{mode}|{SHEET_LAYOUT}".encode()
    ).hexdigest()[:16]
    sheet_path, info_path = folder / f"sheet_{tag}.jpg", folder / f"sheet_{tag}.json"
    info = _read_json(info_path, None)
    if not (sheet_path.exists() and isinstance(info, dict)):
        # cutting out and packing takes a few seconds, longer than Tesserae
        # gives a fetch on a small computer, so it is done in the background
        # and the newest earlier sheet of the same shape shows meanwhile
        job = f"sheet_{tag}"
        with _PAINTING_LOCK:
            fresh = job not in _PAINTING
            _PAINTING.add(job)
        if fresh:

            def run() -> None:
                try:
                    _build_sheet(rows, keys, ratio, size, label, folder, tag, mode)
                finally:
                    with _PAINTING_LOCK:
                        _PAINTING.discard(job)

            threading.Thread(target=run, name=f"birds-{job}", daemon=True).start()
        older = []
        with contextlib.suppress(OSError):
            for f in folder.glob("sheet_*.json"):
                old = _read_json(f, None)
                if (
                    isinstance(old, dict)
                    and abs(float(old.get("ratio") or 0) - round(ratio, 3)) < 0.01
                    and abs(float(old.get("label") or 0) - label) < 1e-4
                    and old.get("mode", "sheet") == mode
                    and f.with_suffix(".jpg").exists()
                ):
                    older.append((f.stat().st_mtime, f, old))
        if not older:
            return None
        _mt, f, info = max(older, key=lambda o: o[0])
        sheet_path, tag = f.with_suffix(".jpg"), f.stem.removeprefix("sheet_")
    try:
        raw = sheet_path.read_bytes()
    except OSError:
        return None
    src = _render(raw, size, None, folder / f"sheet_{tag}_{size}.jpg", levels)
    if not src:
        return None
    return {
        "src": src,
        "birds": info["birds"],
        "ratio": info["w"] / info["h"],
        "paper": _paper(src, folder / f"sheet_{tag}.paper", levels),
        "label": label,
        "mode": info.get("mode", "sheet"),
    }


# ----------------------------------------------------------------------
# Fonts
# ----------------------------------------------------------------------


def _font_css(groups: tuple[str, ...]) -> str:
    """@font-face rules for the bundled faces in ``groups``, as data URIs."""
    if not groups:
        return ""
    try:
        manifest = json.loads(
            (HERE / "fonts" / "fonts.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ""
    rules = []
    for file, face in manifest.items():
        if face.get("group") not in groups:
            continue
        try:
            data = base64.b64encode((HERE / "fonts" / file).read_bytes()).decode()
        except OSError:
            continue
        rules.append(
            f"@font-face{{font-family:'{face['family']}';font-style:{face['style']};"
            f"font-weight:{face['weight']};font-display:block;"
            f"src:url(data:font/woff2;base64,{data}) format('woff2');unicode-range:{face['range']};}}"
        )
    return "".join(rules)


# ----------------------------------------------------------------------
# fetch
# ----------------------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    base = _base_url(str(options.get("url") or ""))
    token = str(options.get("token") or "").strip()
    layout = _choice(options.get("layout"), LAYOUTS)
    caption = _choice(options.get("caption"), CAPTIONS)
    pictures = _choice(options.get("pictures"), PICTURES)
    photo_tone = _choice(options.get("photo_tone"), PHOTO_TONES)
    _CAP.set(_daily_cap(settings))
    art_style = _choice(options.get("art_style"), GEN_STYLE_IDS)
    cluster_style = _choice(options.get("cluster_style"), CLUSTER_STYLES)
    cluster_n = _int(
        options.get("cluster_size"),
        4 if cluster_style == "blend" else 12,
        2,
        CLUSTER_MAX if cluster_style == "blend" else SHEET_MAX,
    )
    cluster_every = _int(options.get("cluster_every"), 3, 1, 24)
    # the cluster's sheet is laid out in the cell's own orientation
    cw, ch = int(ctx.get("cell_w") or 0), int(ctx.get("cell_h") or 0)
    aspect = "3:4" if cw and ch and ch > cw else "4:3"
    levels = _choice(options.get("panel"), LEVELS)
    chart_labels = _choice(options.get("chart_labels"), CHART_LABELS)
    sort = "count" if options.get("sort") == "count" else "recent"
    want = _int(options.get("count"), 6, 1, 20)
    min_conf = _int(options.get("min_confidence"), 70, 10, 99)
    refresh_min = _int(options.get("refresh_min"), 5, 1, 120)
    label = str(options.get("label") or "").strip()
    fal_key = str((settings or {}).get("fal_api_key") or "").strip()
    now = datetime.now(_tz(options))

    data_dir = Path(ctx.get("data_dir") or ".")
    with contextlib.suppress(OSError):
        data_dir.mkdir(parents=True, exist_ok=True)

    if not base:
        raw = _demo(options, now)
    else:
        slug = hashlib.sha1(f"{base}|{min_conf}".encode()).hexdigest()[:12]
        cache = data_dir / f"result_{slug}.json"
        raw = None
        if cache.exists() and time.time() - cache.stat().st_mtime < refresh_min * 60:
            with contextlib.suppress(OSError, json.JSONDecodeError):
                raw = json.loads(cache.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw, err = _from_birdnet(base, token, min_conf)
            if err or raw is None:
                return {
                    "error": err or ERR_UNREACHABLE.format(url=base),
                    "label": label,
                }
            with contextlib.suppress(OSError):
                cache.write_text(json.dumps(raw), encoding="utf-8")

    species = [dict(s) for s in raw.get("species", [])]
    if sort == "count":
        species.sort(key=lambda s: (-s["count"], -(_minutes(s["last"]) or 0)))
    else:
        species.sort(key=lambda s: (-(_minutes(s["last"]) or 0), -s["count"]))
    shown = species[:want]
    latest = (
        dict(max(species, key=lambda s: _minutes(s["last"]) or 0)) if species else None
    )
    if latest:
        # which bird of the day this was, by first heard: the folio's plate number
        firsts = sorted(_minutes(s.get("first") or "") or 0 for s in species)
        latest["order"] = firsts.index(_minutes(latest.get("first") or "") or 0) + 1

    # Pictures only where the layout draws them: one big one for the single
    # bird layouts, a thumbnail per row for the list and gallery.
    notices: list[str] = []
    cluster = None
    if layout in ("latest", "plate", "bleed") and latest:
        notices = _attach_pictures(
            [latest],
            pictures,
            SIZE[layout],
            base,
            token,
            fal_key,
            data_dir,
            levels,
            art_style,
        )
    elif layout in ("list", "gallery"):
        notices = _attach_pictures(
            shown,
            pictures,
            SIZE[layout],
            base,
            token,
            fal_key,
            data_dir,
            levels,
            art_style,
        )
    elif layout == "cluster" and species:
        if cluster_style != "blend" and pictures in ("plate_gen", "gen"):
            ratio = cw / ch if cw and ch else 4 / 3
            if cluster_style == "numbered":
                # the numbered plate shares the cell with its key
                ratio = ratio * 0.64 / 0.92 if ratio >= 1 else ratio * 0.92 / 0.62
            # names at least about 13 px tall on the panel itself
            label_size = max(SHEET_LABEL, 13 / ch) if ch else SHEET_LABEL
            cluster, notices = _specimen_sheet(
                species,
                cluster_n,
                art_style,
                ratio,
                fal_key,
                data_dir,
                levels,
                label_size,
                cluster_style,
            )
        if cluster is None:
            cluster, more = _cluster(
                species,
                min(cluster_n, CLUSTER_MAX),
                cluster_every,
                pictures if cluster_style == "blend" else "plate_photo",
                art_style,
                aspect,
                base,
                token,
                fal_key,
                data_dir,
                levels,
            )
            notices += [m for m in more if m not in notices]

    hourly_total = [0] * 24
    for s in species:
        for h, n in enumerate(s.get("hourly", [])[:24]):
            hourly_total[h] += n
    for s in [*shown, *([latest] if latest else [])]:
        s.pop("thumb", None)
        s["ago_min"] = _ago_min(s.get("last", ""), now)

    groups: tuple[str, ...] = ()
    if layout == "bleed":
        groups = FONT_GROUPS.get(caption, ())
    elif layout in ("plate", "cluster"):
        groups = FONT_GROUPS["engraved"]
        if layout == "cluster" and cluster_style == "notebook":
            groups = (*groups, "caveat")
    credits = []
    pictured = [*shown, *([latest] if latest else [])]
    if cluster:
        pictured = [{"pic": cluster["pic"]}] if cluster["pic"] else cluster["birds"]
    for s in pictured:
        c = (s.get("pic") or {}).get("credit")
        if c and c not in credits:
            credits.append(c)

    return {
        "demo": not base,
        "label": label or ("Demo garden" if not base else "Backyard"),
        "region": raw.get("region", ""),
        "layout": layout,
        "caption": caption,
        "pictures": pictures,
        "photo_tone": photo_tone,
        "art_style": art_style,
        "cluster": cluster,
        "panel": levels,
        "chart_labels": chart_labels,
        "sort": sort,
        "show_sci": options.get("show_sci", True) is not False,
        "species": shown,
        "latest": latest,
        "recent": raw.get("recent", [])[:12],
        "new_today": [s["name"] for s in species if s.get("new")],
        "species_today": len(species),
        "detections_today": sum(s["count"] for s in species),
        "hourly_total": hourly_total,
        "now": now.strftime("%H:%M"),
        "now_hour": now.hour,
        "date": now.strftime("%a %d %b"),
        "date_iso": now.strftime("%Y-%m-%d"),
        "credits": credits,
        # what needs the owner (no key, a failure) is printed on the panel;
        # progress (a painting under way, the day's limit reached) is not,
        # and shows on the paintings page instead
        "notices": [n for n in notices if not _is_progress(n)],
        "status": [n for n in notices if _is_progress(n)],
        "font_css": _font_css(groups),
        "empty": not species,
    }


# ----------------------------------------------------------------------
# Admin page: the AI paintings
# ----------------------------------------------------------------------

_SLUG_RE = re.compile(r"[a-z0-9_]{1,60}(--[a-z]{1,20})?")
_ARCHIVED_RE = re.compile(r"([a-z0-9_]{1,60}(?:--[a-z]{1,20})?)__(\d{8}-\d{6})")


def _known_birds(data_dir: Path) -> list[tuple[str, str]]:
    """(common name, scientific name) for the admin page's picker: birds
    BirdNET-Go reported to this widget, then the demo gardens."""
    seen: dict[str, str] = {}
    with contextlib.suppress(OSError):
        for f in sorted(data_dir.glob("result_*.json")):
            raw = _read_json(f, {})
            for s in raw.get("species", []) if isinstance(raw, dict) else []:
                if isinstance(s, dict) and s.get("sci"):
                    seen.setdefault(str(s["sci"]), str(s.get("name") or s["sci"]))
    for birds in _DEMO.values():
        for name, sci, _w, _p in birds:
            seen.setdefault(sci, name)
    return sorted(((n, sci) for sci, n in seen.items()), key=lambda b: b[0].lower())


def _gallery(data_dir: Path) -> dict[str, Any]:
    """Everything the admin page shows, read from gen/: one entry per
    painting key, a bird in a style or a cluster blend in a style. The
    cut-out collages are made from the birds' paintings and are not listed."""
    folder = _gen_dir(data_dir)
    prefs = _read_json(folder / "prefs.json", {})
    prefs = prefs if isinstance(prefs, dict) else {}
    entries: dict[str, dict[str, Any]] = {}

    def entry(key: str) -> dict[str, Any]:
        base, _, style = key.partition("--")
        style = style if style in GEN_STYLES else "gould"
        return entries.setdefault(
            key,
            {
                "slug": key,
                "cluster": base == "cluster",
                "name": "Today's birds together"
                if base == "cluster"
                else base.replace("_", " ").capitalize(),
                "birds": [],
                "sci": "",
                "style": style,
                "style_label": GEN_STYLES[style][0],
                "active": None,
                "archive": [],
                "error": None,
                "prefer": bool((prefs.get(key) or {}).get("prefer")),
            },
        )

    def take(e: dict[str, Any], meta: Any) -> None:
        if not isinstance(meta, dict):
            return
        if meta.get("name") and not e["cluster"]:
            e["name"] = str(meta["name"])
        e["sci"] = e["sci"] or str(meta.get("sci") or "")
        if isinstance(meta.get("birds"), list) and not e["birds"]:
            e["birds"] = [
                b for b in meta["birds"] if isinstance(b, dict) and b.get("name")
            ]

    with contextlib.suppress(OSError):
        for f in folder.glob("*.jpg"):
            if (
                f.stem.startswith("collage_")
                or not _SLUG_RE.fullmatch(f.stem)
                or not (folder / f"{f.stem}.json").exists()
            ):
                continue  # a resized copy, not a painting
            meta = _read_json(folder / f"{f.stem}.json", {})
            e = entry(f.stem)
            take(e, meta)
            e["active"] = {
                "file": f.name,
                "credit": str(meta.get("credit") or ""),
                "made": time.strftime(
                    "%d %b %Y",
                    time.localtime(int(meta.get("made") or f.stat().st_mtime)),
                ),
            }
        for f in sorted((folder / "archive").glob("*.jpg"), reverse=True):
            m = _ARCHIVED_RE.fullmatch(f.stem)
            if not m:
                continue
            e = entry(m.group(1))
            take(e, _read_json(f.with_suffix(".json"), {}))
            e["archive"].append(
                {
                    "file": f.name,
                    "made": datetime.strptime(m.group(2), "%Y%m%d-%H%M%S").strftime(
                        "%d %b"
                    ),
                }
            )
        for f in folder.glob("*.err"):
            if _SLUG_RE.fullmatch(f.stem) and not f.stem.startswith("collage_"):
                err = _read_json(f, {})
                e = entry(f.stem)
                take(e, err)
                e["error"] = str(err.get("why") or "failed")
    with _PAINTING_LOCK:
        running = set(_PAINTING)
    # a bird with only an old failed attempt has no saved names: find them by slug
    known = {_slug(sci): (n, sci) for n, sci in _known_birds(data_dir)}
    for key, e in entries.items():
        e["painting"] = key in running
        base = key.partition("--")[0]
        if not e["cluster"] and base in known:
            name, sci = known[base]
            e["sci"] = e["sci"] or sci
            if e["name"].lower() == base.replace("_", " "):
                e["name"] = name
    ordered = sorted(
        entries.values(),
        key=lambda e: (
            e["active"] is None,
            e["name"].lower(),
            e["style"],
        ),
    )
    return {
        "birds": ordered,
        "used": _budget_used(folder),
        "cap": _CAP.get(),
        "running": sorted(running),
        "styles": [(k, v[0]) for k, v in GEN_STYLES.items()],
    }


def _job_for(e: dict[str, Any]) -> dict[str, Any] | None:
    """The painting job that repaints an admin-page entry, or None. A blend
    is not repainted from here: deleting it lets the widget blend again."""
    if e["cluster"] or not e["sci"]:
        return None
    return {"key": e["slug"], "style": e["style"], "name": e["name"], "sci": e["sci"]}


def blueprint() -> Any:
    """The admin page at /plugins/<id>/: every AI painting this widget has
    made, with earlier versions kept, and buttons to repaint, delete, bring
    back an earlier version, or use a painting instead of a bird's plate."""
    from flask import (
        Blueprint,
        abort,
        current_app,
        flash,
        redirect,
        render_template,
        request,
        send_from_directory,
        url_for,
    )

    bp = Blueprint("backyard_birds_admin", __name__, template_folder="templates")

    def plugin() -> Any:
        registry = current_app.config["PLUGIN_REGISTRY"]
        for p in registry.plugins.values():
            with contextlib.suppress(OSError, AttributeError):
                if Path(p.path).resolve() == HERE:
                    return p
        abort(404)

    def data_dir() -> Path:
        path = Path(plugin().data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def settings() -> dict[str, Any]:
        store = current_app.config.get("SETTINGS_STORE")
        if store is None:
            return {}
        p = plugin()
        got = store.get_for_runtime("plugins", p.id, p.manifest.get("settings", []))
        return got if isinstance(got, dict) else {}

    def fal_key() -> str:
        return str(settings().get("fal_api_key") or "").strip()

    @bp.before_request
    def daily_cap() -> None:
        _CAP.set(_daily_cap(settings()))

    def slug_or_404(slug: str) -> str:
        if not _SLUG_RE.fullmatch(slug):
            abort(404)
        return slug

    def back() -> Any:
        return redirect(url_for("backyard_birds_admin.index"))

    def start(job: dict[str, Any], label: str) -> None:
        key = fal_key()
        folder = _gen_dir(data_dir())
        if not key:
            flash("Add a fal.ai key in this widget's settings first.", "warn")
        elif _budget_used(folder) >= _CAP.get():
            flash(
                f"Today's limit of {_CAP.get()} paintings is used up. Raise it in this widget's settings.",
                "warn",
            )
        elif not _start_painting(job, data_dir(), key):
            flash(f"{label} is already being painted.", "info")
        else:
            with contextlib.suppress(OSError):
                (folder / f"{job['key']}.err").unlink()
            flash(
                f"Painting {label}. It takes up to a minute; reload this page to see it.",
                "success",
            )

    @bp.get("/")
    def index() -> str:
        dd = data_dir()
        return render_template(
            "backyard_birds/admin.html",
            g=_gallery(dd),
            known=_known_birds(dd),
            has_key=bool(fal_key()),
        )

    @bp.get("/painting/<path:name>")
    def image(name: str) -> Any:
        folder = _gen_dir(data_dir())
        stem, _, ext = name.rpartition(".")
        if ext != "jpg":
            abort(404)
        if _SLUG_RE.fullmatch(stem):
            return send_from_directory(folder, name)
        if stem.startswith("archive/") and _ARCHIVED_RE.fullmatch(stem[8:]):
            return send_from_directory(folder / "archive", name[8:])
        abort(404)

    @bp.post("/paint")
    def paint() -> Any:
        sci = request.form.get("sci", "").strip()
        name = request.form.get("name", "").strip()
        if not name:
            name = dict((s, n) for n, s in _known_birds(data_dir())).get(sci, sci)
        if not sci or not re.fullmatch(r"[A-Z][a-z]+ [a-z-]+( [a-z-]+)?", sci):
            flash("Give the scientific name, for example Turdus merula.", "warn")
            return back()
        style = _choice(request.form.get("style"), GEN_STYLE_IDS)
        job = {
            "key": _pkey(_slug(sci), style),
            "style": style,
            "name": name[:80],
            "sci": sci,
        }
        start(job, f"the {name[:80]}")
        return back()

    @bp.post("/<slug>/repaint")
    def repaint(slug: str) -> Any:
        slug = slug_or_404(slug)
        e = next((e for e in _gallery(data_dir())["birds"] if e["slug"] == slug), None)
        job = _job_for(e) if e else None
        if job is None:
            abort(404)
        start(job, f"the {e['name']}")
        return back()

    @bp.post("/<slug>/delete")
    def delete(slug: str) -> Any:
        slug = slug_or_404(slug)
        folder = _gen_dir(data_dir())
        for f in (
            folder / f"{slug}.jpg",
            folder / f"{slug}.json",
            folder / f"{slug}.err",
        ):
            with contextlib.suppress(OSError):
                f.unlink()
        _drop_derived(folder, slug)
        flash(
            "Painting deleted. With an AI picture option on, the bird is painted again the next time it is shown.",
            "success",
        )
        return back()

    def retire(keys: list[str]) -> int:
        """Move these paintings into their earlier versions, so the widget
        paints the birds again the next time they are shown."""
        folder = _gen_dir(data_dir())
        done = 0
        for key in keys:
            if not _SLUG_RE.fullmatch(key) or not (folder / f"{key}.jpg").exists():
                continue
            _archive(folder, key)
            with contextlib.suppress(OSError):
                (folder / f"{key}.err").unlink()
            done += 1
        return done

    def retired(n: int) -> None:
        if n:
            flash(
                f"{n} painting{'' if n == 1 else 's'} moved to earlier versions. "
                "The widget paints them again as the birds are shown, within the daily limit.",
                "success",
            )
        else:
            flash("No paintings were selected.", "info")

    @bp.post("/budget-reset")
    def budget_reset() -> Any:
        _write(
            _gen_dir(data_dir()) / "budget.json",
            json.dumps({time.strftime("%Y-%m-%d"): 0}).encode(),
        )
        flash("Today's count is back to 0.", "success")
        return back()

    @bp.post("/bulk-delete")
    def bulk_delete() -> Any:
        retired(retire(request.form.getlist("keys")))
        return back()

    @bp.post("/style-delete")
    def style_delete() -> Any:
        style = request.form.get("art", "")
        if style not in GEN_STYLES:
            abort(400)
        keys = [
            e["slug"]
            for e in _gallery(data_dir())["birds"]
            if e["style"] == style and e["active"]
        ]
        retired(retire(keys))
        return back()

    @bp.post("/<slug>/prefer")
    def prefer(slug: str) -> Any:
        slug = slug_or_404(slug)
        folder = _gen_dir(data_dir())
        prefs = _read_json(folder / "prefs.json", {})
        prefs = prefs if isinstance(prefs, dict) else {}
        on = request.form.get("on") == "1"
        if on:
            prefs[slug] = {"prefer": True}
        else:
            prefs.pop(slug, None)
        _write(folder / "prefs.json", json.dumps(prefs).encode())
        return back()

    @bp.post("/<slug>/clear-error")
    def clear_error(slug: str) -> Any:
        slug = slug_or_404(slug)
        with contextlib.suppress(OSError):
            (_gen_dir(data_dir()) / f"{slug}.err").unlink()
        return back()

    @bp.post("/archive/<stem>/use")
    def use_archived(stem: str) -> Any:
        m = _ARCHIVED_RE.fullmatch(stem)
        if not m:
            abort(404)
        folder = _gen_dir(data_dir())
        slug, src = m.group(1), folder / "archive" / f"{stem}.jpg"
        if not src.exists():
            abort(404)
        meta = _read_json(src.with_suffix(".json"), {})
        if (folder / f"{slug}.jpg").exists():
            _archive(folder, slug)
        os.replace(src, folder / f"{slug}.jpg")
        if not isinstance(meta, dict) or not meta:
            meta = {"credit": "AI painting", "made": int(time.time())}
        _write(folder / f"{slug}.json", json.dumps(meta).encode())
        with contextlib.suppress(OSError):
            src.with_suffix(".json").unlink()
        _drop_derived(folder, slug)
        flash("That version is the bird's painting again.", "success")
        return back()

    @bp.post("/archive/<stem>/delete")
    def delete_archived(stem: str) -> Any:
        if not _ARCHIVED_RE.fullmatch(stem):
            abort(404)
        arch = _gen_dir(data_dir()) / "archive"
        for f in (arch / f"{stem}.jpg", arch / f"{stem}.json"):
            with contextlib.suppress(OSError):
                f.unlink()
        return back()

    return bp
