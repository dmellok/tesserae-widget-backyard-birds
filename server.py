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
from datetime import datetime, timedelta, timezone, tzinfo
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
# The background-removal model that cuts a bird from its paper once painted.
FAL_CUTOUT = "https://fal.run/fal-ai/birefnet/v2"
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

LAYOUTS = (
    "list",
    "latest",
    "gallery",
    "chart",
    "plate",
    "bleed",
    "cluster",
    "chorus",
    "atlas",
)
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
    "sonogram",
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
    "chorus": 320,
    "atlas": 240,
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
    "chorus": ("im_fell", "im_fell_sc"),
    "sonogram": ("im_fell", "im_fell_sc"),
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


def _rarity(
    data_dir: Path, species: list[dict[str, Any]], today: str
) -> dict[str, tuple[str, float, str]]:
    """How usual each of today's birds is in this garden, from a log the
    widget keeps of the days each species was heard (gen-free, in
    rarity.json). Until a week has been logged it says so plainly; after
    that: first visit, rare here, occasional, regular or daily, from the
    share of logged days in the last 30 on which the bird was heard."""
    path = data_dir / "rarity.json"
    log = _read_json(path, {})
    log = log if isinstance(log, dict) else {}
    days = log.get("days") if isinstance(log.get("days"), list) else []
    seen = log.get("seen") if isinstance(log.get("seen"), dict) else {}
    # the names go with the log too, so the atlas can name a bird not heard to-day
    names = log.get("names") if isinstance(log.get("names"), dict) else {}
    changed = False
    if today not in days:
        days.append(today)
        changed = True
    for s in species:
        key = s.get("sci") or s["name"]
        hits = seen.setdefault(key, [])
        if today not in hits:
            hits.append(today)
            changed = True
        if names.get(key) != s["name"]:
            names[key] = s["name"]
            changed = True
    days = sorted(days)[-400:]
    window = days[-30:]
    if changed:
        seen = {k: sorted(v)[-400:] for k, v in seen.items()}
        _write(path, json.dumps({"days": days, "seen": seen, "names": names}).encode())
    out: dict[str, tuple[str, float, str]] = {}
    for s in species:
        key = s.get("sci") or s["name"]
        hits = [d for d in seen.get(key, []) if d in window]
        n, span = len(hits), len(window)
        share = n / span if span else 1.0
        # a seasonal arrival: heard again after a long absence
        before = [d for d in seen.get(key, []) if d < today]
        back = ""
        if before:
            with contextlib.suppress(ValueError):
                away = (
                    datetime.strptime(today, "%Y-%m-%d")
                    - datetime.strptime(before[-1], "%Y-%m-%d")
                ).days
                if away >= RETURN_AFTER_DAYS:
                    months = round(away / 30)
                    back = (
                        f"back after {months} month{'' if months == 1 else 's'}"
                        if months >= 2
                        else f"back after {away} days"
                    )
        if span < 7:
            text = f"heard {n} of {span} day{'' if span == 1 else 's'} logged"
        elif n == 1 and len(seen.get(key, [])) == 1:
            text = "first visit"
        elif share <= 0.1:
            text = "rare here"
        elif share <= 0.35:
            text = "occasional"
        elif share <= 0.75:
            text = "regular"
        else:
            text = "daily visitor"
        out[key] = (text, round(share, 3), back)
    return out


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
    base: str, token: str, min_conf: int, window: str = "today", today: str = ""
) -> tuple[dict[str, Any] | None, str | None]:
    """BirdNET-Go's species for the day, or for the last seven days from its
    species summary (which has no hours, so the strips go quiet)."""
    if window == "week" and today:
        start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=6)).strftime(
            "%Y-%m-%d"
        )
        daily, err = _json(
            f"{base}/api/v2/analytics/species/summary?start_date={start}&end_date={today}&min_confidence={min_conf}",
            token,
        )
    else:
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
                "last": _hhmm(s.get("latest_heard") or s.get("last_heard")),
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
                "sci": str(d.get("scientificName") or ""),
                "time": _hhmm(d.get("time") or d.get("timestamp")),
                "conf": round(float(d.get("confidence") or 0), 2),
                # a detection with a clip (or a kept spectrogram) has a sonogram
                "id": int(d["id"])
                if str(d.get("id") or "").isdigit()
                and (d.get("clipName") or d.get("spectrogramOnly"))
                else None,
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


# Plates accepted on the paintings page, merged over the bundled pack. Set
# per fetch (and per admin request) as a context variable, like the cap.
_EXTRA_PLATES: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "birds_extra_plates", default={}
)


def _extra_plates(data_dir: Path) -> dict[str, Any]:
    found = _read_json(data_dir / "plates_extra.json", {})
    return found if isinstance(found, dict) else {}


def _pack() -> dict[str, Any]:
    plates: dict[str, Any] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        data = json.loads((HERE / "plates.json").read_text(encoding="utf-8"))
        if isinstance(data.get("plates"), dict):
            plates = dict(data["plates"])
    plates.update(_EXTRA_PLATES.get())
    return plates


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
    "anime": (
        "Anime",
        "a hand-drawn anime film illustration: clean confident line art, soft cel shading, large expressive eyes and gentle pastel colours, still true to the species, on plain pale paper",
    ),
    "origami": (
        "Origami",
        "a flat, colourful origami illustration of the bird: folded from bright paper in bold flat planes of the species' own colours, every facet a clean flat shape with crisp straight creases drawn as fine lines, no shading, no gradients and no studio light, graphic and poster-like, on a plain pale paper background",
    ),
    "felt": (
        "Needle felt",
        "a needle-felted wool figure of the bird, soft and fuzzy, in the species' true colours and markings, photographed on a plain pale background",
    ),
    "clay": (
        "Clay figure",
        "a hand-sculpted plasticine figure of the bird for a stop-motion film, chunky with a slight sheen and visible thumbprints, in the species' true colours, on a plain pale background",
    ),
    "kawaii": (
        "Kawaii sticker",
        "a cute kawaii sticker of the bird: round and chibi with a happy face, flat bright colours, a thick white die-cut border, on plain white paper",
    ),
    "pixel": (
        "Pixel art",
        "16-bit pixel art sprite of the bird with a limited palette, crisp square pixels and no anti-aliasing, true to the species' colours, on a plain pale background",
    ),
    "bricks": (
        "Brick-built",
        "a model of the bird built from small plastic toy bricks, studs showing, in the species' colours, on a plain pale background",
    ),
    "risograph": (
        "Risograph print",
        "a risograph print in two or three overprinted spot colours, grainy texture and slight misregistration, on plain off-white paper",
    ),
    "screenprint": (
        "Screen print poster",
        "a mid-century screen-printed nature poster: flat shapes of colour, simple bold forms and a slightly textured ink, on plain cream paper",
    ),
    "embroidery": (
        "Embroidery",
        "satin-stitch embroidery in silk thread on plain natural linen, every feather in stitches of the species' true colours",
    ),
    "stainedglass": (
        "Stained glass",
        "a stained-glass panel of the bird: dark lead lines around pieces of jewel-coloured glass in the species' colours, on a plain pale background",
    ),
    "mosaic": (
        "Mosaic",
        "a mosaic of small square tesserae of stone and glass in the species' colours, set in pale grout, on a plain pale background",
    ),
    "papercut": (
        "Paper cut",
        "a layered cut-paper collage of the bird, with sheets of coloured paper in the species' colours and soft shadows between the layers, on plain white paper",
    ),
    "sumie": (
        "Ink and wash",
        "a Japanese sumi-e ink and wash painting: a few confident brushstrokes of black ink with soft grey washes and a touch of colour, on plain rice paper",
    ),
    "pointillism": (
        "Pointillism",
        "a pointillist painting made entirely of small dots of pure colour that blend at a distance, on plain white paper",
    ),
    "artnouveau": (
        "Art Nouveau illustration",
        "an Art Nouveau illustration with sinuous flowing lines, a muted palette and decorative leaves curling round the branch, on plain cream paper",
    ),
    "watercolour": (
        "Modern watercolour",
        "a loose, airy modern watercolour with soft wet edges, a few precise details and plenty of white space, on plain white paper",
    ),
    "charcoal": (
        "Charcoal",
        "a dramatic charcoal drawing with rich blacks, smudged soft tones and a few white chalk highlights, on plain toned paper",
    ),
    "hats": (
        "Tiny hats",
        "a realistic hand-coloured lithograph in the manner of John Gould on warm cream paper, except that the bird wears a small hat that suits its personality, painted with the same care",
    ),
    "superhero": (
        "Superhero",
        "a realistic natural history painting on plain cream paper of the bird striking a heroic pose with a small flowing cape, dramatic light, plumage still true to life",
    ),
    "portrait": (
        "Victorian portrait",
        "a stiff, dignified Victorian oil portrait of the bird as a family ancestor, in a small gilt oval frame, plumage painted true to life, on plain cream paper",
    ),
    "pirate": (
        "Pirate",
        "a realistic hand-coloured lithograph in the manner of John Gould on warm cream paper, except that the bird is a pirate with an eye patch, a small tricorn hat and a cross scowl",
    ),
    "artsandcrafts": (
        "Arts and Crafts (Morris)",
        "an Arts and Crafts illustration in the manner of William Morris: the bird set among dense, repeating stylised foliage and flowers like a wallpaper or tapestry panel, flat muted earth, olive and indigo colours with a firm dark outline, on plain unbleached paper",
    ),
    "jugendstil": (
        "Jugendstil",
        "a Jugendstil illustration, the German Art Nouveau: squarer, more geometric ornament, a bold even outline, flat areas of muted colour, the bird framed in a decorative cartouche of stylised leaves, on plain cream paper",
    ),
    "glasgow": (
        "Glasgow style",
        "a Glasgow style illustration in the manner of Charles Rennie Mackintosh and the Macdonald sisters: a tall elongated composition, stylised roses and fine grids, pale washed colours with a few strong dark lines and a great deal of empty space, on plain pale paper",
    ),
    "secession": (
        "Vienna Secession",
        "a Vienna Secession illustration in the manner of Klimt and Moser: the bird's plumage true to life but its surroundings made of flat decorative shapes, gold, black and jewel colours, mosaic-like patterned areas, spirals and squares, on plain cream paper",
    ),
    "deco": (
        "Art Deco",
        "an Art Deco illustration: stepped geometry, sunbursts and fans, the bird simplified into clean curves and chevrons, a palette of gold, black, teal and cream with flat colour and crisp edges, on plain cream paper",
    ),
    "fairytale": (
        "Golden Age fairy-tale plate",
        "a Golden Age illustration in the manner of Arthur Rackham and Edmund Dulac: fine ink line with soft muted watercolour wash, gnarled branches and twisted roots as a frame, a faintly magical atmosphere, on plain toned paper",
    ),
    "mucha": (
        "Mucha poster",
        "an Art Nouveau poster in the manner of Alphonse Mucha: the bird in a halo roundel, a flowing ornamental border, pastel flat colour with a crisp dark outline, and empty panels where lettering would go, on plain cream paper",
    ),
    "beardsley": (
        "Beardsley ink",
        "a black-and-white ink illustration in the manner of Aubrey Beardsley: bold sweeping lines, large flat black masses against white, ornamental dots and fine hatching, no colour at all, on plain white paper",
    ),
    "nouveauglass": (
        "Nouveau stained window",
        "a Tiffany-style Art Nouveau stained-glass window of the bird: leaded curving lines around pieces of opalescent coloured glass, wisteria and dragonfly ornament, soft light through the glass, on a plain pale background",
    ),
    "japonisme": (
        "Japonisme",
        "a Japonisme illustration, Western Art Nouveau borrowing from ukiyo-e: a flat bird on a branch, an asymmetric composition with an ornamented border and a small seal-like roundel, soft flat colours and a fine keyline, on plain cream paper",
    ),
    "chinoiserie": (
        "Chinoiserie",
        "a hand-painted chinoiserie wallpaper panel: the bird on a flowering branch with delicate blossom and butterflies, soft flat colours on a pale ground, fine brushwork, on plain pale paper",
    ),
    "persian": (
        "Persian miniature",
        "a Persian miniature painting: flat jewel colours, gold leaf, flattened perspective, the bird among tulips and stylised flowers under a patterned sky, with a gold and blue illuminated border, on plain cream paper",
    ),
    "bestiary": (
        "Medieval bestiary",
        "a page from a medieval bestiary: the bird painted in red, blue and green tempera with gold leaf on a vellum ground, drawn as a manuscript illuminator would, with a vine and acanthus border, no text",
    ),
    "byzantine": (
        "Byzantine icon",
        "a Byzantine icon panel of the bird: a burnished gold ground, a formal frontal pose with a halo-like circle behind the head, deep reds and blues in egg tempera, fine gold lines in the feathers",
    ),
    "folkart": (
        "Folk art",
        "a folk-art painting of the bird in the manner of Pennsylvania Dutch fraktur and Scandinavian rosemaling: symmetric scrolls, hearts and tulips, flat bright colours with a dark outline, on plain cream paper",
    ),
    "harper": (
        "Mid-century modern (Harper)",
        "a mid-century modern illustration in the manner of Charley Harper: the bird reduced to circles, triangles and clean geometric shapes, flat colour in a handful of tones, no outlines, true to the species' markings, on plain cream paper",
    ),
    "cigarette": (
        "Edwardian cigarette card",
        "an Edwardian cigarette card chromolithograph of the bird: a small framed picture with a decorative border, slightly faded printed inks, fine detail, and a blank panel below for the name, on plain cream card",
    ),
    "delft": (
        "Delft tile",
        "a Dutch Delft tile painting of the bird: cobalt blue on white tin-glazed earthenware, the bird in a tiled square with corner ornaments, brushy blue washes and a fine crackle in the glaze",
    ),
}
# "custom" is the owner's own prompt (the art_prompt option), painted under a
# tag made from the prompt's words so each prompt keeps its own paintings.
GEN_STYLE_IDS = (*GEN_STYLES, "custom")
CUSTOM_EXAMPLE = (
    "an Art Nouveau illustration with sinuous flowing lines, a muted palette of "
    "sage, ochre and dusty rose, and a border of curling leaves and lilies, on "
    "plain cream paper"
)
_CUSTOM_RE = re.compile(r"custom[a-z]{8}")
_CUSTOM: contextvars.ContextVar[dict[str, tuple[str, str]] | None] = (
    contextvars.ContextVar("bb_custom", default=None)
)


def _custom_tag(how: str) -> str:
    """The style tag for a custom prompt: 'custom' and eight letters from its
    words, so the same prompt always finds its paintings."""
    n = int(hashlib.sha1(" ".join(how.lower().split()).encode()).hexdigest()[:16], 16)
    letters = ""
    for _ in range(8):
        n, r = divmod(n, 26)
        letters += chr(97 + r)
    return "custom" + letters


def _style(style: str | None) -> tuple[str, str]:
    """(label, prompt words) for a style id or custom tag. A custom tag is
    known through the request that named it; outside one (the admin page)
    the label alone, and the painting's own meta carries its words."""
    if style in GEN_STYLES:
        return GEN_STYLES[style]
    known = _CUSTOM.get() or {}
    if style in known:
        return known[style]
    if style and _CUSTOM_RE.fullmatch(style):
        return ("Custom style", "")
    return GEN_STYLES["gould"]


# The styles in the groups the pickers show them in.
GEN_STYLE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Plates and prints",
        (
            "gould",
            "audubon",
            "woodblock",
            "engraving",
            "linocut",
            "sketch",
            "risograph",
            "screenprint",
            "harper",
        ),
    ),
    (
        "Decorative",
        (
            "artnouveau",
            "mucha",
            "jugendstil",
            "glasgow",
            "secession",
            "deco",
            "artsandcrafts",
            "japonisme",
            "fairytale",
            "beardsley",
            "nouveauglass",
        ),
    ),
    (
        "Period and ornament",
        (
            "chinoiserie",
            "persian",
            "bestiary",
            "byzantine",
            "folkart",
            "delft",
            "cigarette",
        ),
    ),
    ("Fine art", ("watercolour", "sumie", "pointillism", "charcoal")),
    (
        "Crafts",
        (
            "origami",
            "papercut",
            "embroidery",
            "felt",
            "clay",
            "bricks",
            "stainedglass",
            "mosaic",
        ),
    ),
    ("Playful", ("anime", "kawaii", "pixel")),
    ("Jokes", ("grumpy", "hats", "superhero", "portrait", "pirate")),
)
GEN_BLEND = (
    "This picture is a collage of {n} birds, each painted on its own: {birds}. Repaint it "
    "as one seamless picture, {style}, with all the birds together in a single natural "
    "scene: {scene}, one continuous background, the same light and paper throughout. "
    "{fidelity} No text, no labels, no numbers, no border, no signature."
)
GEN_ONE = (
    "Paint the bird in the reference photo, the {name} ({sci}), as {style}. Keep the "
    "plumage, markings, bill and proportions exactly as in the photo, {pose} alone on "
    "plain, empty paper with a clear margin all round. No other birds, no scenery, no "
    "text, no lettering, no border, no signature."
)
# The two poses a bird is painted in; a cluster can mix them so a page is not
# eight profiles on branches. Perched is the key every painting had before.
POSES = {
    "perched": "perched on a simple branch, the whole bird and its perch",
    "flight": "in flight with its wings spread, seen from the side a little below, the whole bird",
}
CLUSTER_POSES = ("perched", "mixed")
# What the page covers: to-day, or the last seven days (no hours in that case).
WINDOWS = ("today", "week")
# Most birds the blended cluster shows (the specimen sheet takes more).
CLUSTER_MAX = 6
# The cluster as a specimen sheet (each bird printed as painted) or blended
# into one scene by a final AI pass.
CLUSTER_STYLES = ("sheet", "numbered", "wreath", "notebook", "drawer", "blend")
# How much text the cluster page carries: everything, the labels under each
# bird alone, or none.
CLUSTER_TEXTS = ("key", "under", "none")
# The line at the foot of a page: status and credits, credits alone, or nothing.
FOOTERS = ("show", "credits", "hide")
# Type size on the cluster page, per cent of the look's own.
TEXT_SCALES = ("100", "60", "80", "125", "150")
# Where the birds go on a printed cluster page; "auto" is the look's own.
ARRANGEMENTS = ("auto", "oval", "circle", "ring", "rows", "scatter", "heap", "fal")
# Where fal.ai puts the birds when it composes the scene (arrangement "fal").
SCENES = {
    "auto": "the birds gathered in an oval cluster, like a Victorian vignette, on branches and foliage that join them",
    "branch": "all perched along one shared branch",
    "tree": "perched through the branches of one tree",
    "birdbath": "gathered round a stone bird bath in a garden",
    "wire": "sitting in a row along a telephone wire against the sky",
    "feeder": "gathered at and around a hanging bird feeder",
    "meadow": "on the ground and low stems of a wildflower meadow",
    "sky": "all in flight together across an open sky",
    "wetland": "among reeds and still water at the edge of a wetland",
    "garden": "in a cottage garden, on a fence, shrubs and a path",
}
# Which bird the single-bird layouts show.
HEROES = ("latest", "rare", "busy", "quiet", "new")
# What the panel does after dark.
NIGHTS = ("off", "asleep", "owls")
# Languages iNaturalist can give common names in ("birdnet" keeps BirdNET-Go's).
NAME_LOCALES = (
    "birdnet",
    "en",
    "de",
    "fr",
    "es",
    "it",
    "nl",
    "pt",
    "sv",
    "da",
    "nb",
    "fi",
    "pl",
    "cs",
    "ru",
    "ja",
    "zh-CN",
)
# A bird away this many days or more counts as returning when it is heard again.
RETURN_AFTER_DAYS = 45
FIDELITY = {
    "exact": "Keep every bird's species, plumage, markings, pose and size as painted, and keep each about where it is.",
    "free": "Keep every bird's species, plumage and markings, but re-pose and move the birds freely to make the scene natural.",
}
# The type on the printed looks (book plate, full-bleed captions, cluster
# pages): bundled font groups and the faces the client uses for them.
FONTS = {
    "classic": (),
    "playfair": ("playfair",),
    "typewriter": ("special_elite",),
    "hand": ("caveat",),
    "modern": ("archivo_black", "archivo_narrow"),
    "theme": (),
}
# How big each bird is drawn: true to size, rarest or busiest biggest, alike.
SIZINGS = ("true", "rare", "busy", "same")
# What may be printed under each bird on a cluster page, in this order.
LABEL_LINES = ("name", "sci", "count", "last", "rarity", "new")
# The longest text each line can take, for reserving room when packing.
_LABEL_ROOM = {
    "sci": None,
    "count": "999 calls to-day",
    "last": "last heard 23:59",
    "rarity": "heard 30 of 30 days",
    "new": "new",
}
# Layout version of the specimen sheet, part of its cache tag.
SHEET_LAYOUT = "sheet7"
# Most birds a specimen sheet shows.
SHEET_MAX = 24
# Most stamps the atlas shows.
ATLAS_MAX = 48
# Label type size as a fraction of the sheet's height.
SHEET_LABEL = 0.017

# Paintings being made right now, by key, so two renders never start the same
# one. Paintings run in daemon threads that outlive the fetch that started
# them: fal takes 15 to 60 seconds and Tesserae gives a fetch about six.
_PAINTING: set[str] = set()
# For the paintings page's live ticker: when each painting started, its bird
# and style, and the last few to finish.
_PAINTING_SINCE: dict[str, tuple[float, str, str]] = {}
_FINISHED: list[tuple[str, float]] = []
_PAINTING_LOCK = threading.Lock()


def _pkey(slug: str, style: str, pose: str = "perched") -> str:
    """File key for a bird's painting in a style and pose. The Gould style
    keeps the bare slug and perched keeps the bare key, so paintings made
    before styles and poses existed are still found."""
    base = slug if style == "gould" else f"{slug}--{style}"
    return base if pose == "perched" else f"{slug}--{style}--{pose}"


def _blend_key(style: str, scene: str = "auto", fidelity: str = "exact") -> str:
    """File key for the cluster's blended picture in a style; each scene and
    fidelity keeps its own, the first (auto, exact) under the old bare key."""
    base = (
        "cluster"
        if (scene, fidelity) == ("auto", "exact")
        else f"cluster_{scene}_{fidelity}"
    )
    return f"{base}--{style}"


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
    label, how = _style(job.get("style") or "gould")
    how = str(job.get("how") or how)

    def fail(why: str) -> str:
        info = {k: job.get(k) for k in ("name", "sci", "style", "birds")}
        _write(
            err_path, json.dumps({"why": why, "at": int(time.time()), **info}).encode()
        )
        return why

    if not how:
        return fail(
            "the custom prompt behind this painting is not known; paint it again from the widget"
        )
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
            scene=SCENES.get(job.get("scene") or "auto", SCENES["auto"]),
            fidelity=FIDELITY.get(job.get("fidelity") or "exact", FIDELITY["exact"]),
        )
        ref_credit = ""
    else:
        image_url, ref_credit = (
            _inat_ref(job["sci"], data_dir) if job.get("sci") else ("", "")
        )
        if not image_url:
            return fail("no openly licensed reference photo on iNaturalist")
        prompt = GEN_ONE.format(
            name=job["name"],
            sci=job["sci"],
            style=how,
            pose=POSES.get(str(job.get("pose") or "perched"), POSES["perched"]),
        )
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
    if _CUSTOM_RE.fullmatch(str(job.get("style") or "")):
        meta["how"] = how  # a custom prompt's words travel with the painting
    if job.get("pose") and job.get("pose") != "perched":
        meta["pose"] = job["pose"]
    if job.get("collage"):
        meta["birds"] = job.get("birds") or []
        meta["tag"] = job.get("tag", "")
        # where each bird sat on the collage, for labels over the blend
        meta["boxes"] = job.get("boxes") or []
    _write(folder / f"{key}.json", json.dumps(meta).encode())
    _write(img, raw)
    _drop_derived(folder, key)
    with contextlib.suppress(OSError):
        err_path.unlink()
    if not job.get("collage"):
        # the cut-out: the painting with its paper removed, from the model's own
        # copy of the picture; best effort, the sheet cuts it out itself without
        cut = _cut_out(out_url, fal_key)
        if cut:
            _write(folder / f"{key}.cut.png", cut)
    return None


def _cut_out(image_url: str, fal_key: str) -> bytes | None:
    """The painting at ``image_url`` with its paper removed, as PNG bytes with
    alpha, from the BiRefNet model on fal.ai; None when it declined."""
    body = json.dumps(
        {
            "image_url": image_url,
            "model": "General Use (Light)",
            "operating_resolution": "1024x1024",
            "output_format": "png",
            "refine_foreground": True,
        }
    ).encode()
    answer, err = _json(
        FAL_CUTOUT, fal_key, auth_scheme="Key", body=body, timeout=GEN_TIMEOUT_S
    )
    if err or not isinstance(answer, dict):
        return None
    url = str((answer.get("image") or {}).get("url") or "")
    if not url:
        return None
    raw, _err = _http(url, accept="image/*", timeout=60)
    return raw if raw and raw[:2] == b"\x89P" else None


def _archive(folder: Path, key: str) -> None:
    """Move a painting (and its credit) into gen/archive/."""
    arch = folder / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with contextlib.suppress(OSError):
        os.replace(folder / f"{key}.jpg", arch / f"{key}__{stamp}.jpg")
    with contextlib.suppress(OSError):
        os.replace(folder / f"{key}.cut.png", arch / f"{key}__{stamp}.cut.png")
    with contextlib.suppress(OSError):
        os.replace(folder / f"{key}.json", arch / f"{key}__{stamp}.json")
    _drop_derived(folder, key)


def _start_painting(job: dict[str, Any], data_dir: Path, fal_key: str) -> bool:
    """Start a painting in the background. False if one is already running
    under this key. The thread carries the caller's context, so the widget's
    network capability scope still applies to it."""
    key = job["key"]
    if not job.get("how"):
        job["how"] = _style(job.get("style"))[1]
    with _PAINTING_LOCK:
        if key in _PAINTING:
            return False
        _PAINTING.add(key)
        _PAINTING_SINCE[key] = (
            time.time(),
            str(job.get("name") or key),
            str(job.get("style") or "gould"),
        )

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
                _PAINTING_SINCE.pop(key, None)
                _FINISHED.append((key, time.time()))
                del _FINISHED[:-20]

    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(run,), name=f"birds-paint-{key}", daemon=True
    ).start()
    return True


_BATCH: set[str] = set()


def _start_batch(jobs: list[dict[str, Any]], data_dir: Path, fal_key: str) -> bool:
    """Paint ``jobs`` one after another in one background thread, stopping at
    the daily cap. False when a batch in this style is already running. Each
    painting goes through _start_painting, so the admin page and the widget
    see it as a painting under way like any other."""
    if not jobs:
        return False
    tag = jobs[0].get("style") or "gould"
    with _PAINTING_LOCK:
        if tag in _BATCH:
            return False
        _BATCH.add(tag)
    folder = _gen_dir(data_dir)

    def run() -> None:
        try:
            for job in jobs:
                if _budget_used(folder) >= _CAP.get():
                    break
                if not _start_painting(job, data_dir, fal_key):
                    continue
                while True:
                    time.sleep(0.5)
                    with _PAINTING_LOCK:
                        if job["key"] not in _PAINTING:
                            break
        finally:
            with _PAINTING_LOCK:
                _BATCH.discard(tag)

    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(run,), name=f"birds-batch-{tag}", daemon=True
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
    pose: str = "perched",
) -> tuple[dict[str, Any] | None, bool, str | None]:
    """(picture, started_one, notice). Reads a kept painting, or starts one in
    the background when allowed: one new painting per fetch at most, and
    the daily limit, so a busy morning fills in over a few refreshes. A
    bird whose painting failed is left alone for GEN_RETRY_S and the failure
    is shown as a notice."""
    key = _pkey(_slug(row["sci"] or row["name"]), style, pose)
    pic = _painting(key, size, data_dir, levels)
    if pic is not None:
        return pic, False, None
    go, note = _may_paint(data_dir, key, f"the {row['name']}", fal_key, allow_new)
    if not go:
        return None, False, note
    job = {"key": key, "style": style, "name": row["name"], "sci": row["sci"]}
    if pose != "perched":
        job["pose"] = pose
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
    pictures: str = "gen",
    lines: tuple[str, ...] = ("name",),
    arrange: str = "auto",
    sizing: str = "true",
    poses: str = "perched",
) -> tuple[dict[str, Any] | None, list[str]]:
    """The cluster as a printed page: today's busiest birds cut out and laid
    out together. With an AI picture option each bird is its own AI
    painting (one new one per refresh); with classic plates it is cut from
    the bird's plate, with no fal at all, and birds without a plate wait
    (or use a painting kept from before). None until a bird has a source."""
    birds = [dict(b) for b in sorted(species, key=lambda s: -s["count"])[:n]]
    notices: list[str] = []
    ai = pictures in ("plate_gen", "gen")
    folder = _gen_dir(data_dir)
    pack = _pack() if pictures.startswith("plate") else {}
    credits: list[str] = []
    if ai and not fal_key:
        notices.append(NOTE_NO_KEY)
    started = False
    for i, b in enumerate(birds):
        slug = _slug(b["sci"] or b["name"])
        # mixed: every other bird in flight, so the page is not all profiles on branches
        pose = "flight" if poses == "mixed" and i % 2 == 1 else "perched"
        painting = _pkey(slug, style, pose)
        b["key"] = None
        if ai:
            pic, did, note = _generated(
                b,
                SIZE["gallery"],
                data_dir,
                fal_key,
                allow_new=not started,
                levels=levels,
                style=style,
                pose=pose,
            )
            started = started or did
            if note and note not in notices:
                notices.append(note)
            if pic is not None:
                b["key"] = painting
                credits.append(
                    "AI paintings, "
                    + _style(style)[0].split(" (")[0].lower()
                    + " (fal.ai nano-banana)"
                )
            continue
        # classic plates: the plate's crop, kept in gen/ as <slug>--plate.jpg
        # (no record file beside it, so the paintings page does not list it)
        plate_key = f"{slug}--plate"
        if pack.get(b["sci"]):
            if not (folder / f"{plate_key}.jpg").exists():
                pic = _plate(b["sci"], pack, 1200, data_dir)
                if pic:
                    _write(
                        folder / f"{plate_key}.jpg",
                        base64.b64decode(pic["src"].split(",", 1)[1]),
                    )
            if (folder / f"{plate_key}.jpg").exists():
                b["key"] = plate_key
                src = str((pack[b["sci"]] or {}).get("source") or "Classic plate")
                credits.append(f"{src} · public domain")
                continue
        if (folder / f"{painting}.jpg").exists():
            b["key"] = painting
            credits.append("AI painting (fal.ai nano-banana)")
    ready = [b for b in birds if b["key"]]
    if not ready:
        return None, notices
    for b in ready:
        room = []
        for line in lines:
            if line == "name":
                room.append(b["name"])
            elif line == "sci":
                room.append(b["sci"] or "")
            elif line in _LABEL_ROOM:
                room.append(_LABEL_ROOM[line])
        b["label_room"] = [t for t in room if t]
    # how big each bird is drawn: true to size (from the length table), the
    # rarest or the busiest biggest, or all alike
    if sizing == "rare":
        for b in ready:
            b["factor"] = round(1.75 - 1.0 * float(b.get("rarity_share") or 0), 2)
    elif sizing == "busy":
        # the busiest biggest on a gentle curve: area to the power 0.65 of
        # the count as a share of the top bird's, so the top three do not
        # all land on the same size
        top = max(b["count"] for b in ready) or 1
        for b in ready:
            b["factor"] = round(0.65 + (b["count"] / top) ** 0.325, 2)
    elif sizing == "same":
        for b in ready:
            b["factor"] = 1.0
    made = _sheet(
        ready,
        [b["key"] for b in ready],
        ratio,
        SIZE["cluster"],
        data_dir,
        levels,
        label,
        mode,
        arrange,
    )
    if not made:
        return None, notices
    by_name = {b["name"]: b for b in birds}
    shown = []
    for m in made["birds"]:
        b = by_name.get(m["name"], {})
        shown.append(
            {
                **m,
                "count": b.get("count", 0),
                "last": b.get("last", ""),
                "new": bool(b.get("new")),
                "rarity": b.get("rarity", ""),
            }
        )
    return {
        "sheet": True,
        "pic": {
            "src": made["src"],
            "kind": "sheet",
            "paper": made["paper"],
            "credit": " · ".join(dict.fromkeys(credits)),
        },
        "birds": shown,
        "lines": list(lines),
        "ratio": made["ratio"],
        "label": made["label"],
        "mode": made["mode"],
        "waiting": [b["name"] for b in birds if not b["key"]],
        "waiting_verb": "paint" if ai else "find a plate for",
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
    scene: str = "auto",
    fidelity: str = "exact",
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
        bkey = _blend_key(style, scene, fidelity)
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
                        "scene": scene,
                        "fidelity": fidelity,
                        "collage": made["sheet"],
                        "tag": made["tag"],
                        "boxes": made["boxes"],
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
            boxes = bmeta.get("boxes") or []
            return {
                "pic": blend,
                "birds": shown,
                "boxes": boxes if len(boxes) == len(shown) else [],
                "montage": False,
                "waiting": [],
                "aspect": aspect,
            }, notices
        if made:
            label = _style(style)[0].split(" (")[0].lower()
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
                "boxes": made["boxes"],
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


def _cutout_masked(
    raw: bytes, cut_png: bytes | None = None
) -> tuple[Any, Any, tuple[int, int, int]] | None:
    """(the bird cropped from its painting with the paper turned white and
    everything outside its outline whitened too, its mask, the paper
    colour). Whitening outside a soft mask drops any wash or vignette the
    painter left round the bird, so only the bird and its perch are printed
    on the sheet. With ``cut_png``, the painting as the cut-out model
    returned it, its alpha is the mask and nothing is guessed."""
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
    hard = None
    if cut_png:
        with contextlib.suppress(Exception):
            alpha = Image.open(io.BytesIO(cut_png)).convert("RGBA").getchannel("A")
            hard = alpha.resize((w, h), Image.Resampling.BILINEAR).point(
                lambda v: 255 if v > 127 else 0
            )
    if hard is None:
        try:
            hard = _bird_mask(im, paper)
        except ImportError:  # pragma: no cover - numpy ships with Tesserae
            diff = ImageChops.difference(im, Image.new("RGB", im.size, paper)).convert(
                "L"
            )
            hard = diff.point(lambda v: 255 if v > 30 else 0)
            hard = hard.filter(ImageFilter.MaxFilter(7)).filter(
                ImageFilter.MinFilter(5)
            )
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
    arrange: str = "auto",
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
    if arrange == "auto":
        arrange = {"wreath": "ring", "notebook": "rows"}.get(mode, "oval")
    cx, cy, rx, ry = gw / 2, gh * 0.47, gw * 0.48, gh * 0.45
    if arrange in ("circle", "ring"):
        cy = gh / 2
        rx = ry = min(gw * 0.48, gh * 0.47)
        if arrange == "ring" and mode == "wreath":
            rx, ry = gw * 0.48, gh * 0.45
    oval = ((xs + 0.5 - cx) / rx) ** 2 + ((ys + 0.5 - cy) / ry) ** 2
    top = 0.1 if mode == "notebook" else 0.05
    left = 0.12 if mode == "notebook" else 0.04
    page = (xs >= gw * left) & (xs < gw * 0.96) & (ys >= gh * top) & (ys < gh * 0.93)
    if arrange == "ring":
        inside = (oval <= 1) & (oval >= 0.3**2)
    else:
        # the oval and circle are soft targets: birds are placed from the
        # middle out and pulled inward, but only the page margin is a wall,
        # so a tall plate does not cap how big everything can be drawn
        inside = page
    label_rows = max(2, int(label * 1.5 * H / cell))
    gap = 1

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
        # the label's strip under the bird, as wide as the bird or its
        # longest line, as tall as its lines
        lines = ["00"] if mode == "numbered" else (b.get("lines") or [b["name"]])
        rows = label_rows * max(1, len(lines)) if lines else 0
        # the first line is the name in small caps; the rest are smaller italic
        name_w = (
            int(
                max(len(t) * (0.74 if i == 0 else 0.5) for i, t in enumerate(lines))
                * label
                * H
                / cell
            )
            + 3
            if lines
            else 0
        )
        fw = max(tw, name_w)
        fp = np.zeros((th + rows + gap, fw), dtype=bool)
        off = (fw - tw) // 2
        fp[:th, off : off + tw] = bird
        if lines:
            fp[th + 1 : th + rows, (fw - name_w) // 2 : (fw - name_w) // 2 + name_w] = (
                True
            )
        return fp, off

    seed = int(
        hashlib.sha1("|".join(b["name"] for b in birds).encode()).hexdigest()[:8], 16
    )

    def order(ty: Any, tx: Any, fh: int, fw: int) -> tuple[Any, Any]:
        """(candidate order, the distance used to stop looking)."""
        flat = np.zeros(ty.shape, dtype=float)
        if arrange == "rows":
            return np.argsort(ty * gw + tx, axis=None, kind="stable"), flat
        if arrange == "scatter":
            # a steady shuffle, so the page does not jump between refreshes
            return np.random.default_rng(seed).permutation(ty.size), flat
        if arrange == "heap":
            # the biggest settle at the bottom middle, the rest pile on top
            d = ((tx + fw / 2 - cx) / (gw * 0.6)) ** 2 + (
                (gh * 0.93 - (ty + fh)) / gh
            ) ** 2
            return np.argsort(d, axis=None), flat
        d = ((tx + fw / 2 - cx) / rx) ** 2 + ((ty + fh / 2 - cy) / ry) ** 2
        if arrange == "ring":
            # nearest the ring's line (65% of the way out), not the middle
            return np.argsort(np.abs(np.sqrt(d) - 0.65), axis=None), d
        return np.argsort(d, axis=None), d

    def pull(x: int, y: int, fh: int, fw: int) -> tuple[int, int]:
        """Which way this bird wants to move, as a unit step, or (0, 0)."""
        bx, by = x + fw / 2, y + fh / 2
        if arrange == "rows":
            return (-1 if bx > gw * 0.14 else 0, -1 if by > gh * (top + 0.05) else 0)
        if arrange == "heap":
            return (int(np.sign(cx - bx)) if abs(cx - bx) > 2 else 0, 1)
        if arrange == "ring":
            # toward the ring's line, not the middle
            r = (((bx - cx) / rx) ** 2 + ((by - cy) / ry) ** 2) ** 0.5
            if abs(r - 0.65) < 0.03:
                return (0, 0)
            d = -1 if r > 0.65 else 1
            return (int(np.sign(bx - cx)) * d, int(np.sign(by - cy)) * d)
        return (
            int(np.sign(cx - bx)) if abs(cx - bx) > 2 else 0,
            int(np.sign(cy - by)) if abs(cy - by) > 2 else 0,
        )

    def compact(occ: Any, placed: list[Any]) -> None:
        """Nudge every bird one cell at a time toward where it is pulled,
        while the move keeps it clear of the others and inside the page,
        until nothing moves: the loose first packing becomes a tight group,
        like birds cut from plates and pasted close together."""
        for _ in range(600):
            moved = False
            for i in range(len(placed) - 1, -1, -1):
                y, x, fp, off, scale = placed[i]
                fh, fw = fp.shape
                dx, dy = pull(x, y, fh, fw)
                if not dx and not dy:
                    continue
                occ[y : y + fh, x : x + fw] &= ~fp
                for sx, sy in ((dx, dy), (dx, 0), (0, dy)):
                    if not sx and not sy:
                        continue
                    nx, ny = x + sx, y + sy
                    if nx < 0 or ny < 0 or nx + fw > gw or ny + fh > gh:
                        continue
                    if (occ[ny : ny + fh, nx : nx + fw] & fp).any():
                        continue
                    if not inside[ny : ny + fh, nx : nx + fw][fp].all():
                        continue
                    x, y, moved = nx, ny, True
                    break
                occ[y : y + fh, x : x + fw] |= fp
                placed[i] = (y, x, fp, off, scale)
            if not moved:
                break

    def attempt(unit: float) -> list[tuple[int, int, float]] | None:
        occ = np.zeros((gh, gw), dtype=bool)
        placed: list[Any] = []
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
                if arrange in ("oval", "circle") and d.flat[k] > 2.2:
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
            placed.append((y, x, fp, off, scale))
            # close up after each bird, so the next finds room nearer the middle
            if arrange != "scatter":
                compact(occ, placed)
        return [((x + off) * cell, y * cell, scale) for y, x, _fp, off, scale in placed]

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
    for _ in range(16):  # then grow while it still fits, to fill the page
        bigger = attempt(unit * 1.05)
        if not bigger:
            break
        unit, best = unit * 1.05, bigger
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
    arrange: str = "auto",
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
            cut_png = None
            with contextlib.suppress(OSError):
                cut_png = (folder / f"{k}.cut.png").read_bytes()
            got = _cutout_masked((folder / f"{k}.jpg").read_bytes(), cut_png)
            if got:
                cut, mask, paper = got
                birds.append(
                    {
                        "name": r["name"],
                        "sci": r["sci"],
                        "cut": cut,
                        "mask": mask,
                        "paper": paper,
                        "factor": r.get("factor") or _size_factor(r["sci"], lengths),
                        "rot": _rot_of(r["name"], mode),
                        "lines": r.get("label_room"),
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
        n_lines = max((len(b.get("lines") or [1]) for b in birds), default=1)
        tag_h = label * (1.4 * n_lines + 1.2) * H
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
        spots = _pack_sheet(birds, W, H, label, mode, arrange)
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
        "arrange": arrange,
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
    arrange: str = "auto",
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
    names = "|".join(
        r["name"]
        + "/"
        + "/".join(r.get("label_room") or [])
        + f"/{r.get('factor') or 0:.2f}"
        for r in rows
    )
    tag = hashlib.sha1(
        f"{'|'.join(stamps)}|{ratio:.3f}|{size}|{names}|{label:.4f}|{mode}|{arrange}|{SHEET_LAYOUT}".encode()
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
                    _build_sheet(
                        rows, keys, ratio, size, label, folder, tag, mode, arrange
                    )
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
                    and old.get("arrange", "auto") == arrange
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
# The sky: sunrise, sunset and the moon, for night mode
# ----------------------------------------------------------------------


def _atlas(data_dir: Path, limit: int = ATLAS_MAX) -> dict[str, Any]:
    """Every bird ever heard here, from the rarity log: name, first and last
    day, days heard; the ones heard most days first. {birds, since, days}."""
    log = _read_json(data_dir / "rarity.json", {})
    log = log if isinstance(log, dict) else {}
    days = sorted(log.get("days") or []) if isinstance(log.get("days"), list) else []
    seen = log.get("seen") if isinstance(log.get("seen"), dict) else {}
    names = log.get("names") if isinstance(log.get("names"), dict) else {}
    birds = []
    for key, hits in seen.items():
        hits = sorted(h for h in hits if isinstance(h, str))
        if not hits:
            continue
        birds.append(
            {
                "name": str(names.get(key) or key),
                "sci": key if " " in key else "",
                "first": hits[0],
                "last": hits[-1],
                "days": len(hits),
            }
        )
    birds.sort(key=lambda b: (-b["days"], b["first"], b["name"]))
    return {"birds": birds[:limit], "since": days[0] if days else "", "days": len(days)}


def _sun_times(lat: float, lon: float, now: datetime) -> tuple[int, int] | None:
    """(sunrise, sunset) as minutes of the local day, by the usual NOAA
    approximation; None where the sun does not rise or set that day."""
    import math

    n = now.timetuple().tm_yday
    offset = (now.utcoffset() or timedelta(0)).total_seconds() / 3600
    out = []
    for rising in (True, False):
        t = n + (((6 if rising else 18) - lon / 15) / 24)
        m = 0.9856 * t - 3.289
        lng = (
            m
            + 1.916 * math.sin(math.radians(m))
            + 0.020 * math.sin(math.radians(2 * m))
            + 282.634
        ) % 360
        ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(lng)))) % 360
        ra += (math.floor(lng / 90) - math.floor(ra / 90)) * 90
        ra /= 15
        sin_dec = 0.39782 * math.sin(math.radians(lng))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (
            math.cos(math.radians(90.833)) - sin_dec * math.sin(math.radians(lat))
        ) / (cos_dec * math.cos(math.radians(lat)))
        if cos_h > 1 or cos_h < -1:
            return None
        h = (
            360 - math.degrees(math.acos(cos_h))
            if rising
            else math.degrees(math.acos(cos_h))
        ) / 15
        ut = (h + ra - 0.06571 * t - 6.622 - lon / 15) % 24
        out.append(int(round(((ut + offset) % 24) * 60)))
    return out[0], out[1]


def _moon(now: datetime) -> tuple[float, str]:
    """(phase 0..1 from new moon, its name)."""
    epoch = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    phase = (((now - epoch).total_seconds() / 86400) / 29.530588853) % 1
    names = (
        "new moon",
        "waxing crescent",
        "first quarter",
        "waxing gibbous",
        "full moon",
        "waning gibbous",
        "last quarter",
        "waning crescent",
    )
    return round(phase, 3), names[int((phase * 8 + 0.5) % 8)]


def _night(options: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Sunrise, sunset, the moon and whether it is dark now, for the cell's
    (or the app's) location; a garden without one is never dark."""
    try:
        lat, lon = float(options.get("latitude")), float(options.get("longitude"))
    except (TypeError, ValueError):
        return {"dark": False}
    times = _sun_times(lat, lon, now)
    phase, moon = _moon(now)
    if times is None:
        return {"dark": False, "moon": phase, "moon_name": moon}
    rise, set_ = times
    mins = now.hour * 60 + now.minute
    dark = mins < rise or mins > set_
    return {
        "dark": dark,
        "sunrise": f"{rise // 60:02d}:{rise % 60:02d}",
        "sunset": f"{set_ // 60:02d}:{set_ % 60:02d}",
        "moon": phase,
        "moon_name": moon,
    }


# ----------------------------------------------------------------------
# Sonogram and song line
# ----------------------------------------------------------------------


def _song_line(png: bytes) -> list[list[float]]:
    """The call's shape: the brightest frequency in each slice of the
    sonogram, as up to 64 points of (time, pitch) in 0..1, pitch upward.
    Quiet slices (background only) are left out, so a call with two notes
    gives two strokes."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:  # pragma: no cover
        return []
    try:
        im = Image.open(io.BytesIO(png)).convert("L")
    except Exception:  # noqa: BLE001
        return []
    w, h = im.size
    mx, my = int(w * 0.06), int(h * 0.06)  # axes and margins, if any
    a = np.asarray(im.crop((mx, my, w - mx, h - my)), dtype=np.float32)
    if a.size == 0:
        return []
    a -= np.percentile(a, 60)  # the background
    a = np.clip(a, 0, None)
    peak = float(a.max()) or 1.0
    cols = a.shape[1]
    step = max(1, cols // 64)
    pts: list[list[float]] = []
    for x0 in range(0, cols - step + 1, step):
        col = a[:, x0 : x0 + step].mean(axis=1)
        if col.max() < 0.35 * peak:
            continue
        y = int(col.argmax())
        pts.append([round(x0 / cols, 3), round(1 - y / a.shape[0], 3)])
    return pts


def _sonogram_fetch(base: str, token: str, det_id: int, data_dir: Path) -> bytes | None:
    """BirdNET-Go's spectrogram for a detection, kept once fetched; a miss
    (still being made, or gone) is retried after five minutes."""
    folder = data_dir / "sono"
    folder.mkdir(parents=True, exist_ok=True)
    path, miss = folder / f"{det_id}.png", folder / f"{det_id}.miss"
    if path.exists():
        with contextlib.suppress(OSError):
            return path.read_bytes()
    if miss.exists() and time.time() - miss.stat().st_mtime < 300:
        return None
    body, err = _http(f"{base}/api/v2/spectrogram/{det_id}", token, accept="image/*")
    if err or not body:
        with contextlib.suppress(OSError):
            miss.touch()
        return None
    _write(path, body)
    with contextlib.suppress(OSError):
        for old in folder.glob("*.png"):
            if time.time() - old.stat().st_mtime > 7 * 86400:
                old.unlink()
    return body


def _demo_sonogram(name: str) -> bytes:
    """A made-up sonogram for the demo garden: a few chirps on a dark
    ground, the same for a bird every time."""
    import numpy as np
    from PIL import Image

    rng = random.Random(name)
    w, h = 600, 220
    yy, xx = np.mgrid[0:h, 0:w]
    img = (
        rng.random() * 0
        + 18
        + 22 * (1 - yy / h)
        + np.asarray([[rng.random() * 14 for _ in range(w)] for _ in range(h)])
    )
    for _ in range(rng.randint(3, 6)):
        x0 = rng.uniform(0.05, 0.8) * w
        span = rng.uniform(0.06, 0.2) * w
        y0 = rng.uniform(0.25, 0.75) * h
        bend = rng.uniform(-0.5, 0.5) * h
        for t in np.linspace(0, 1, int(span)):
            x = int(x0 + t * span)
            y = int(y0 + bend * (t - 0.5) ** 2 * 4 - bend * 0.2 * t)
            if 0 <= x < w and 3 <= y < h - 3:
                img[y - 3 : y + 4, x] = np.maximum(
                    img[y - 3 : y + 4, x], [120, 200, 250, 255, 250, 200, 120]
                )
    arr = np.clip(img, 0, 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(arr).save(out, "PNG")
    return out.getvalue()


def _sonogram(
    latest: dict[str, Any],
    recent: list[dict[str, Any]],
    base: str,
    token: str,
    data_dir: Path,
    levels: str,
    demo: bool,
) -> dict[str, Any] | None:
    """The hero bird's latest call: {src, song, at}. From BirdNET-Go's
    spectrogram of its most recent detection with a clip, or a made-up one
    in the demo garden."""
    if demo:
        png = _demo_sonogram(latest["name"])
        at = latest.get("last", "")
    else:
        hit = next(
            (r for r in recent if r.get("id") and r.get("name") == latest["name"]), None
        )
        if not hit:
            return None
        got = _sonogram_fetch(base, token, int(hit["id"]), data_dir)
        if not got:
            return None
        png, at = got, hit.get("time", "")
    key = hashlib.sha1(png[:4096] + str(len(png)).encode()).hexdigest()[:16]
    src = _render(png, 900, None, data_dir / "sono" / f"s_{key}.jpg", levels)
    if not src:
        return None
    return {"src": src, "song": _song_line(png), "at": at}


# ----------------------------------------------------------------------
# Names in another language or region
# ----------------------------------------------------------------------

_NAMING: set[str] = set()


def _names_file(data_dir: Path, locale: str, place: str) -> Path:
    folder = data_dir / "names"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{_slug(locale)}_{_slug(place or 'any')}.json"


def _names(
    data_dir: Path, species: list[dict[str, Any]], locale: str, place: str
) -> None:
    """Give each species its common name in ``locale`` (and, for English,
    the region ``place``) from iNaturalist, looked up once per species and
    kept. Missing names are fetched in a background thread, so a fresh
    garden shows BirdNET-Go's names until the next refresh."""
    if locale == "birdnet":
        return
    path = _names_file(data_dir, locale, place)
    have = _read_json(path, {})
    have = have if isinstance(have, dict) else {}
    missing = [s["sci"] for s in species if s.get("sci") and s["sci"] not in have]
    for s in species:
        got = have.get(s.get("sci") or "")
        if got:
            s["name_birdnet"] = s["name"]
            s["name"] = got
    if not missing:
        return
    job = str(path)
    with _PAINTING_LOCK:
        if job in _NAMING:
            return
        _NAMING.add(job)

    def run() -> None:
        try:
            found = _read_json(path, {})
            found = found if isinstance(found, dict) else {}
            for sci in missing[:40]:
                q = {"q": sci, "rank": "species", "per_page": 1, "locale": locale}
                if place:
                    q["preferred_place_id"] = place
                data, err = _json(f"{INAT_TAXA}?{urllib.parse.urlencode(q)}")
                rows = (
                    data.get("results") if isinstance(data, dict) and not err else None
                )
                name = (
                    str((rows[0] or {}).get("preferred_common_name") or "")
                    if rows
                    else ""
                )
                # "" is kept too, so a bird iNaturalist has no name for is asked once
                found[sci] = name
                time.sleep(0.4)
            _write(path, json.dumps(found, ensure_ascii=False).encode())
        finally:
            with _PAINTING_LOCK:
                _NAMING.discard(job)

    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(run,), name="birds-names", daemon=True
    ).start()


# ----------------------------------------------------------------------
# Growing the plate pack from the paintings page
# ----------------------------------------------------------------------

_FINDING: set[str] = set()


def _plate_tool() -> Any:
    """tools/build_plates.py, loaded by path, its cache moved under data."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bb_build_plates", HERE / "tools" / "build_plates.py"
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _find_plates(sci: str, data_dir: Path) -> None:
    """Look for public-domain plates of a species on Wikimedia Commons, in
    the background, and keep the best few with their crops for the
    paintings page to offer."""
    with _PAINTING_LOCK:
        if sci in _FINDING:
            return
        _FINDING.add(sci)

    def run() -> None:
        store = data_dir / "plates_found.json"
        try:
            tool = _plate_tool()
            if tool is None:
                return
            tool.OUT = data_dir / "plates_tool"
            tool.CACHE = tool.OUT / "cache"
            tool.CACHE.mkdir(parents=True, exist_ok=True)
            cands = tool.candidates(sci)[:4]
            for c in cands:
                try:
                    c["box"] = list(tool.autocrop(tool._image(c["thumb"])))
                except Exception:  # noqa: BLE001
                    c["box"] = [0, 0, 1, 1]
            found = _read_json(store, {})
            found = found if isinstance(found, dict) else {}
            found[sci] = {"at": int(time.time()), "candidates": cands}
            _write(store, json.dumps(found, ensure_ascii=False).encode())
        except Exception as err:  # noqa: BLE001
            found = _read_json(store, {})
            found = found if isinstance(found, dict) else {}
            found[sci] = {
                "at": int(time.time()),
                "candidates": [],
                "error": str(err)[:200],
            }
            _write(store, json.dumps(found, ensure_ascii=False).encode())
        finally:
            with _PAINTING_LOCK:
                _FINDING.discard(sci)

    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(run,), name=f"birds-plates-{_slug(sci)}", daemon=True
    ).start()


def _plateless(data_dir: Path) -> list[dict[str, Any]]:
    """Birds this widget has reported that have no plate, with any plates
    found for them so far."""
    pack = _pack()
    found = _read_json(data_dir / "plates_found.json", {})
    found = found if isinstance(found, dict) else {}
    with _PAINTING_LOCK:
        finding = set(_FINDING)
    out = []
    for name, sci in _known_birds(data_dir):
        if not sci or sci in pack:
            continue
        got = found.get(sci) if isinstance(found.get(sci), dict) else None
        out.append(
            {
                "name": name,
                "sci": sci,
                "finding": sci in finding,
                "candidates": (got or {}).get("candidates") or [],
                "searched": got is not None,
                "error": (got or {}).get("error"),
            }
        )
    return out


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
    """The widget's data. The daily limit and the plates accepted on the
    paintings page ride on context variables for the call (and the painting
    threads it starts, which copy the context), and are reset after."""
    data_dir = Path(ctx.get("data_dir") or ".")
    cap_token = _CAP.set(_daily_cap(settings))
    plates_token = _EXTRA_PLATES.set(_extra_plates(data_dir))
    custom_token = _CUSTOM.set(None)
    try:
        return _fetch(options, settings, ctx=ctx)
    finally:
        _CUSTOM.reset(custom_token)
        _EXTRA_PLATES.reset(plates_token)
        _CAP.reset(cap_token)


def _fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    base = _base_url(str(options.get("url") or ""))
    token = str(options.get("token") or "").strip()
    layout = _choice(options.get("layout"), LAYOUTS)
    caption = _choice(options.get("caption"), CAPTIONS)
    pictures = _choice(options.get("pictures"), PICTURES)
    photo_tone = _choice(options.get("photo_tone"), PHOTO_TONES)
    art_style = _choice(options.get("art_style"), GEN_STYLE_IDS)
    if art_style == "custom":
        how = (
            " ".join(str(options.get("art_prompt") or "").split())[:800]
            or CUSTOM_EXAMPLE
        )
        art_style = _custom_tag(how)
        _CUSTOM.set({art_style: ("Custom style", how)})
    cluster_style = _choice(options.get("cluster_style"), CLUSTER_STYLES)
    picked = options.get("cluster_labels")
    if isinstance(picked, str):
        picked = [v.strip() for v in picked.split(",") if v.strip()]
    if not isinstance(picked, list):
        picked = ["name"]
    cluster_lines = tuple(v for v in LABEL_LINES if v in picked)
    cluster_arrange = _choice(options.get("cluster_arrange"), ARRANGEMENTS)
    cluster_text = _choice(options.get("cluster_text"), CLUSTER_TEXTS)
    cluster_poses = _choice(options.get("cluster_poses"), CLUSTER_POSES)
    window = _choice(options.get("window"), WINDOWS)
    text_scale = int(_choice(options.get("cluster_text_scale"), TEXT_SCALES)) / 100
    footer = _choice(options.get("footer"), FOOTERS)
    cluster_sizing = _choice(options.get("cluster_sizing"), SIZINGS)
    cluster_scene = _choice(options.get("cluster_scene"), tuple(SCENES))
    cluster_fidelity = _choice(options.get("cluster_fidelity"), tuple(FIDELITY))
    # the blended scene is the "fal" arrangement; cells saved with the old
    # Blended scene style still get it
    if cluster_style == "blend":
        cluster_arrange = "fal"
    elif cluster_arrange == "fal":
        cluster_style = "blend"
    font = _choice(options.get("font"), tuple(FONTS))
    hero = _choice(options.get("hero"), HEROES)
    night_mode = _choice(options.get("night"), NIGHTS)
    name_locale = _choice(options.get("names"), NAME_LOCALES)
    name_place = str(options.get("names_place") or "").strip()
    if not name_place.isdigit():
        name_place = ""
    song_line = options.get("song_line", True) is not False
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
        slug = hashlib.sha1(f"{base}|{min_conf}|{window}".encode()).hexdigest()[:12]
        cache = data_dir / f"result_{slug}.json"
        raw = None
        if cache.exists() and time.time() - cache.stat().st_mtime < refresh_min * 60:
            with contextlib.suppress(OSError, json.JSONDecodeError):
                raw = json.loads(cache.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw, err = _from_birdnet(
                base, token, min_conf, window, now.strftime("%Y-%m-%d")
            )
            if err or raw is None:
                return {
                    "error": err or ERR_UNREACHABLE.format(url=base),
                    "label": label,
                }
            with contextlib.suppress(OSError):
                cache.write_text(json.dumps(raw), encoding="utf-8")

    species = [dict(s) for s in raw.get("species", [])]
    if window == "week" and not base:
        # the demo garden's week: a few days' worth of the day's calls
        for s_ in species:
            s_["count"] = s_["count"] * (4 + len(s_["name"]) % 3)
    rarity = _rarity(data_dir, species, now.strftime("%Y-%m-%d")) if species else {}
    for s_ in species:
        s_["rarity"], s_["rarity_share"], s_["returning"] = rarity.get(
            s_.get("sci") or s_["name"], ("", 0.0, "")
        )
    _names(data_dir, species, name_locale, name_place)
    sky = _night(options, now)
    asleep = None
    if night_mode != "off" and sky.get("dark") and species:
        last_min = max(_minutes(s["last"]) or 0 for s in species)
        quiet_for = now.hour * 60 + now.minute - last_min
        if night_mode == "owls":
            # after dark, only the birds heard since sunset
            sunset = _minutes(sky.get("sunset") or "") or 0
            night_birds = [s for s in species if (_minutes(s["last"]) or 0) >= sunset]
            if night_birds:
                species = night_birds
            else:
                asleep = {"quiet_min": quiet_for}
        elif quiet_for >= 90:
            asleep = {"quiet_min": quiet_for}
    if sort == "count":
        species.sort(key=lambda s: (-s["count"], -(_minutes(s["last"]) or 0)))
    else:
        species.sort(key=lambda s: (-(_minutes(s["last"]) or 0), -s["count"]))
    # the chorus is the whole day's arrivals, not the first few
    shown = species[: (SHEET_MAX if layout == "chorus" else want)]
    latest = (
        dict(max(species, key=lambda s: _minutes(s["last"]) or 0)) if species else None
    )
    hero_kick = ""
    if species and hero != "latest":
        pick = {
            "rare": min(
                species, key=lambda s: (s.get("rarity_share") or 0, -s["count"])
            ),
            "busy": max(species, key=lambda s: s["count"]),
            "quiet": min(species, key=lambda s: s["count"]),
            "new": next(
                (s for s in species if s.get("new") or s.get("returning")), None
            ),
        }.get(hero)
        if pick is not None:
            latest = dict(pick)
            hero_kick = {
                "rare": "Rarest to-day",
                "busy": "Most calls to-day",
                "quiet": "Fewest calls to-day",
                "new": "New to-day" if pick.get("new") else "Returned to-day",
            }[hero]
    if latest:
        # which bird of the day this was, by first heard: the folio's plate number
        firsts = sorted(_minutes(s.get("first") or "") or 0 for s in species)
        latest["order"] = firsts.index(_minutes(latest.get("first") or "") or 0) + 1

    # Pictures only where the layout draws them: one big one for the single
    # bird layouts (the list is one too now: its hero to the edge of the page,
    # the rest named beside it), a thumbnail per card for the gallery.
    notices: list[str] = []
    cluster = None
    atlas = None
    if layout in ("latest", "plate", "bleed", "list") and latest:
        notices = _attach_pictures(
            [latest],
            pictures,
            SIZE["latest" if layout == "list" else layout],
            base,
            token,
            fal_key,
            data_dir,
            levels,
            art_style,
        )
    elif layout == "atlas":
        atlas = _atlas(data_dir)
        notices = _attach_pictures(
            atlas["birds"],
            pictures,
            SIZE["atlas"],
            base,
            token,
            fal_key,
            data_dir,
            levels,
            art_style,
            allow_new=False,
        )
    elif layout in ("gallery", "chorus"):
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
    sono = None
    if latest and (
        (layout == "bleed" and caption == "sonogram")
        or (song_line and layout in ("bleed", "plate", "latest"))
    ):
        sono = _sonogram(
            latest, raw.get("recent", []), base, token, data_dir, levels, not base
        )
        if sono and not (layout == "bleed" and caption == "sonogram"):
            sono = {"song": sono["song"], "at": sono["at"]}  # the line alone
    elif layout == "cluster" and species:
        # names at least about 13 px tall on the panel itself
        label_size = max(SHEET_LABEL, 13 / ch) if ch else SHEET_LABEL
        if cluster_style != "blend" and pictures in ("plate_gen", "gen", "plate_photo"):
            ratio = cw / ch if cw and ch else 4 / 3
            if cluster_style == "numbered" and cluster_text == "key":
                # the numbered plate shares the cell with its key
                ratio = ratio * 0.64 / 0.92 if ratio >= 1 else ratio * 0.92 / 0.62
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
                pictures,
                cluster_lines,
                cluster_arrange,
                cluster_sizing,
                poses=cluster_poses,
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
                cluster_scene,
                cluster_fidelity,
            )
            notices += [m for m in more if m not in notices]
        if cluster is not None:
            cluster.setdefault("label", label_size)
            cluster.setdefault("lines", list(cluster_lines))

    hourly_total = [0] * 24
    for s in species:
        for h, n in enumerate(s.get("hourly", [])[:24]):
            hourly_total[h] += n
    for s in [*shown, *([latest] if latest else [])]:
        s.pop("thumb", None)
        s["ago_min"] = _ago_min(s.get("last", ""), now)

    # every page is printed matter now: the engraved faces go with all of
    # them, the full-bleed captions add their own, the notebook its hand
    groups: tuple[str, ...] = FONT_GROUPS["engraved"]
    if layout == "bleed":
        groups = tuple(dict.fromkeys((*groups, *FONT_GROUPS.get(caption, ()))))
    elif layout == "cluster" and cluster_style == "notebook":
        groups = (*groups, "caveat")
    groups = tuple(dict.fromkeys((*groups, *FONTS[font])))
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
        "font": font,
        "hero": hero,
        "hero_kick": hero_kick,
        "sono": sono,
        "song_line": song_line,
        "sky": sky,
        "asleep": asleep,
        "names": name_locale,
        "cluster": cluster,
        "cluster_text": cluster_text,
        "text_scale": text_scale,
        "footer": footer,
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
        "window": window,
        "period": "this week" if window == "week" else "to-day",
        "atlas": atlas,
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

_SLUG_RE = re.compile(
    r"[a-z0-9_]{1,60}(--[a-z]{1,20}){0,2}"
)  # slug, cluster_<scene>_<fidelity>, with --style and --pose
_ARCHIVED_RE = re.compile(r"([a-z0-9_]{1,60}(?:--[a-z]{1,20}){0,2})__(\d{8}-\d{6})")


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
        base, _, rest = key.partition("--")
        style, _, pose = rest.partition("--")
        style = style if style in GEN_STYLES or _CUSTOM_RE.fullmatch(style) else "gould"
        pose = pose if pose in POSES else "perched"
        scene = base.split("_")[1] if base.startswith("cluster_") else ""
        return entries.setdefault(
            key,
            {
                "slug": key,
                "cluster": base.startswith("cluster"),
                "name": ("Today's birds together" + (f", {scene}" if scene else ""))
                if base.startswith("cluster")
                else base.replace("_", " ").capitalize(),
                "birds": [],
                "sci": "",
                "style": style,
                "style_label": _style(style)[0]
                + (" · in flight" if pose == "flight" else ""),
                "pose": pose,
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
        if meta.get("how") and not e.get("how"):
            e["how"] = str(meta["how"])
            words = e["how"]
            e["style_label"] = "Custom: " + (
                words[:44].rstrip() + "…" if len(words) > 45 else words
            )
        if isinstance(meta.get("birds"), list) and not e["birds"]:
            e["birds"] = [
                b for b in meta["birds"] if isinstance(b, dict) and b.get("name")
            ]

    with contextlib.suppress(OSError):
        for f in folder.glob("*.jpg"):
            if (
                f.stem.startswith(("collage_", "sheet_"))
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
            if _SLUG_RE.fullmatch(f.stem) and not f.stem.startswith(
                ("collage_", "sheet_")
            ):
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
        "custom_example": CUSTOM_EXAMPLE,
        "style_groups": [
            (g, [(k, GEN_STYLES[k][0]) for k in ids]) for g, ids in GEN_STYLE_GROUPS
        ],
    }


def _job_for(e: dict[str, Any]) -> dict[str, Any] | None:
    """The painting job that repaints an admin-page entry, or None. A blend
    is not repainted from here: deleting it lets the widget blend again."""
    if e["cluster"] or not e["sci"]:
        return None
    job = {"key": e["slug"], "style": e["style"], "name": e["name"], "sci": e["sci"]}
    if e.get("how"):
        job["how"] = e["how"]
    if e.get("pose") and e["pose"] != "perched":
        job["pose"] = e["pose"]
    return job


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
        _EXTRA_PLATES.set(_extra_plates(data_dir()))

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
            plateless=_plateless(dd),
            extra_plates=_extra_plates(dd),
        )

    @bp.get("/progress")
    def progress() -> Any:
        """What is painting right now, for the page's live ticker: each
        painting under way with its bird, style and seconds so far, the
        batches running, today's count against the limit, and the last
        paintings to finish (so the page knows when to redraw)."""
        from flask import jsonify

        folder = _gen_dir(data_dir())
        now = time.time()
        with _PAINTING_LOCK:
            running = [
                {
                    "key": k,
                    "name": name,
                    "style": _style(style)[0],
                    "seconds": int(now - since),
                    "blend": k.startswith("cluster"),
                }
                for k, (since, name, style) in _PAINTING_SINCE.items()
            ]
            batches = [_style(t)[0] for t in sorted(_BATCH)]
            finished = [{"key": k, "at": int(at)} for k, at in _FINISHED]
        return jsonify(
            {
                "running": running,
                "batches": batches,
                "finished": finished,
                "used": _budget_used(folder),
                "cap": _CAP.get(),
                "has_key": bool(fal_key()),
            }
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
        if style == "custom":
            how = " ".join(request.form.get("how", "").split())[:800] or CUSTOM_EXAMPLE
            job["style"] = _custom_tag(how)
            job["key"] = _pkey(_slug(sci), job["style"])
            job["how"] = how
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
            folder / f"{slug}.cut.png",
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

    @bp.post("/plates/find")
    def plates_find() -> Any:
        sci = request.form.get("sci", "").strip()
        if not re.fullmatch(r"[A-Z][a-z]+ [a-z-]+( [a-z-]+)?", sci):
            abort(400)
        _find_plates(sci, data_dir())
        flash(
            f"Looking for plates of {sci} on Wikimedia Commons. It takes a minute; reload to see them.",
            "success",
        )
        return back()

    @bp.post("/plates/accept")
    def plates_accept() -> Any:
        sci = request.form.get("sci", "").strip()
        try:
            idx = int(request.form.get("idx", "0"))
        except ValueError:
            abort(400)
        dd = data_dir()
        found = _read_json(dd / "plates_found.json", {})
        cands = (
            ((found.get(sci) or {}).get("candidates") or [])
            if isinstance(found, dict)
            else []
        )
        if not (0 <= idx < len(cands)):
            abort(404)
        c = cands[idx]
        x0, y0, x1, y1 = c["box"]
        extra = _extra_plates(dd)
        extra[sci] = {
            "file": c["file"],
            "box": c["box"],
            "focus": [round((x0 + x1) / 2, 3), round(y0 + (y1 - y0) * 0.42, 3)],
            "source": c.get("source") or "illustration",
            "artist": c.get("artist") or "",
            "license": c.get("license") or "Public domain",
        }
        _write(dd / "plates_extra.json", json.dumps(extra, ensure_ascii=False).encode())
        # the sheet's cut of this bird is made again from the new plate
        with contextlib.suppress(OSError):
            (_gen_dir(dd) / f"{_slug(sci)}--plate.jpg").unlink()
        flash(f"{sci} now has a plate. It shows on the next refresh.", "success")
        return back()

    @bp.post("/plates/remove")
    def plates_remove() -> Any:
        sci = request.form.get("sci", "").strip()
        dd = data_dir()
        extra = _extra_plates(dd)
        if sci in extra:
            del extra[sci]
            _write(
                dd / "plates_extra.json", json.dumps(extra, ensure_ascii=False).encode()
            )
            with contextlib.suppress(OSError):
                (_gen_dir(dd) / f"{_slug(sci)}--plate.jpg").unlink()
        return back()

    @bp.post("/style-paint")
    def style_paint() -> Any:
        """Paint every bird BirdNET-Go has reported (the demo gardens' birds
        when none has been) in one style, skipping the ones already painted,
        one after another within the daily limit."""
        style = request.form.get("art", "")
        if style not in GEN_STYLES:
            abort(400)
        key = fal_key()
        if not key:
            flash("Add a fal.ai key in this widget's settings first.", "warn")
            return back()
        folder = _gen_dir(data_dir())
        birds = _known_birds(data_dir())
        jobs = [
            {"key": _pkey(_slug(sci), style), "style": style, "name": name, "sci": sci}
            for name, sci in birds
            if not (folder / f"{_pkey(_slug(sci), style)}.jpg").exists()
        ]
        label = GEN_STYLES[style][0]
        room = max(0, _CAP.get() - _budget_used(folder))
        if not jobs:
            flash(f"Every known bird already has a painting in {label}.", "info")
        elif not room:
            flash(
                f"Today's limit of {_CAP.get()} paintings is used up. Raise it in this widget's settings.",
                "warn",
            )
        elif not _start_batch(jobs, data_dir(), key):
            flash(f"{label} is already being painted for every bird.", "info")
        else:
            n = min(len(jobs), room)
            flash(
                f"Painting {n} bird{'s' if n != 1 else ''} in {label}, one after another"
                + (
                    f"; {len(jobs) - n} more wait for tomorrow's limit"
                    if len(jobs) > n
                    else ""
                )
                + ". Reload this page to watch them arrive.",
                "success",
            )
        return back()

    @bp.post("/style-delete")
    def style_delete() -> Any:
        style = request.form.get("art", "")
        if style not in GEN_STYLES and not _CUSTOM_RE.fullmatch(style):
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
