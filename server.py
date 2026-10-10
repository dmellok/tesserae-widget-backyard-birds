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
             needs a fal key in the widget settings)
  photo      BirdNET-Go's own species thumbnail, or an openly licensed
             iNaturalist photo in demo mode

Every picture is resized for the layout and inlined as a data URI, as are the
caption fonts, because the render sandbox has no network.

Files in ctx["data_dir"]:
  result_<slug>.json    BirdNET-Go answer (TTL = refresh_min)
  img/                  fetched originals and resized copies
  gen/                  AI paintings, their credits and the daily budget
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import random
import re
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
GEN_TIMEOUT_S = 120

ERR_UNREACHABLE = "Couldn't reach BirdNET-Go at {url}."
ERR_AUTH = "BirdNET-Go asked for a login. Add an API token in this cell's settings."
NOTE_NO_KEY = "AI paintings need a fal.ai key in this widget's settings."

LAYOUTS = ("list", "latest", "gallery", "chart", "plate", "bleed")
CAPTIONS = ("band", "editorial", "notebook", "tag", "poster")
PICTURES = ("plate_photo", "plate_gen", "gen", "photo", "none")

# Longest edge, in pixels, of a picture for each layout. Big enough for a
# 13.3" panel at full bleed, small enough that a list stays a few hundred KB.
SIZE = {"list": 160, "gallery": 520, "latest": 1100, "plate": 1100, "bleed": 1600}

# Fonts each caption style needs, inlined only when that style is on screen.
FONT_GROUPS = {
    "editorial": ("playfair", "archivo_narrow"),
    "notebook": ("caveat",),
    "tag": ("special_elite",),
    "poster": ("archivo_black", "archivo_narrow"),
    "engraved": ("im_fell", "im_fell_sc"),
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
        if err.code in (401, 403):
            return None, "auth"
        return None, f"HTTP {err.code}"
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
    raw: bytes, size: int, box: list[float] | None = None, cache: Path | None = None
) -> str:
    """Crop to ``box`` (fractions), fit the longest edge to ``size`` and return a
    JPEG data URI. Cached per (source, box, size) when ``cache`` is given."""
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
    out = io.BytesIO()
    im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    data = out.getvalue()
    if cache is not None:
        _write(cache, data)
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def _paper(src: str, cache: Path) -> str:
    """The page colour around a plate (or a painting in the plate style), as
    #rrggbb: the median of a thin band along the picture's edges. Full bleed
    fills the panel with it so the whole plate fits without a visible edge."""
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
    sci: str, pack: dict[str, Any], size: int, data_dir: Path
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
    src = _render(raw, size, entry.get("box"), data_dir / "img" / f"p_{key}.jpg")
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
    paper = _paper(src, data_dir / "img" / f"p_{key}.paper")
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
    row: dict[str, Any], size: int, base: str, token: str, data_dir: Path
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
    src = _render(raw, size, None, data_dir / "img" / f"f_{key}.jpg")
    return (
        {"src": src, "kind": "photo", "credit": credit, "focus": [0.5, 0.42]}
        if src
        else None
    )


GEN_PROMPT = (
    "Repaint this bird as a plate from a nineteenth-century natural history book: a "
    "hand-coloured lithograph in the manner of John Gould, showing the {name} ({sci}) "
    "perched on a simple branch. Keep the plumage, markings, bill and proportions exactly "
    "as in the reference photo. Soft watercolour washes over fine engraved linework, plain "
    "cream paper background, generous margins. No text, no lettering, no border, no signature."
)


def _generated(
    row: dict[str, Any], size: int, data_dir: Path, key: str, allow_new: bool
) -> tuple[dict[str, Any] | None, bool]:
    """(picture, made_one). Reads a kept painting, or makes one when allowed.

    One new painting per fetch at most, and GEN_DAILY_CAP a day, so a busy
    morning fills in over a few refreshes instead of one slow, costly render.
    An exclusive lock file stops two renders paying for the same bird."""
    folder = data_dir / "gen"
    folder.mkdir(parents=True, exist_ok=True)
    slug = _slug(row["sci"] or row["name"])
    img, meta = folder / f"{slug}.jpg", folder / f"{slug}.json"
    if img.exists():
        credit = "AI painting"
        with contextlib.suppress(OSError, json.JSONDecodeError):
            credit = str(
                json.loads(meta.read_text(encoding="utf-8")).get("credit") or credit
            )
        src = _render(img.read_bytes(), size, None, folder / f"{slug}_{size}.jpg")
        if not src:
            return None, False
        paper = _paper(src, folder / f"{slug}.paper")
        return {
            "src": src,
            "kind": "generated",
            "credit": credit,
            "focus": [0.5, 0.45],
            "paper": paper,
        }, False
    if not (allow_new and key):
        return None, False

    budget_path = folder / "budget.json"
    today = time.strftime("%Y-%m-%d")
    budget: dict[str, int] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
    if budget.get(today, 0) >= GEN_DAILY_CAP:
        return None, False
    lock = folder / f"{slug}.lock"
    with contextlib.suppress(OSError):
        if time.time() - lock.stat().st_mtime > 600:
            lock.unlink()
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL))
    except OSError:
        return None, False
    try:
        ref_url, ref_credit = _inat_ref(row["sci"], data_dir)
        if not ref_url:
            return None, False
        body = json.dumps(
            {
                "prompt": GEN_PROMPT.format(name=row["name"], sci=row["sci"]),
                "image_urls": [ref_url],
                "num_images": 1,
                "output_format": "jpeg",
                "aspect_ratio": "3:4",
            }
        ).encode()
        answer, err = _json(
            FAL_EDIT, key, auth_scheme="Key", body=body, timeout=GEN_TIMEOUT_S
        )
        budget[today] = budget.get(today, 0) + 1
        with contextlib.suppress(OSError):
            budget_path.write_text(json.dumps({today: budget[today]}), encoding="utf-8")
        images = answer.get("images") if isinstance(answer, dict) and not err else None
        out_url = str(images[0].get("url") or "") if images else ""
        raw, _ = (
            _http(out_url, accept="image/*", timeout=60) if out_url else (None, None)
        )
        if not raw:
            return None, True
        credit = "AI painting (fal.ai nano-banana)" + (
            f" after {ref_credit}" if ref_credit else ""
        )
        _write(meta, json.dumps({"credit": credit, "made": int(time.time())}).encode())
        _write(img, raw)
        src = _render(raw, size, None, folder / f"{slug}_{size}.jpg")
        if not src:
            return None, True
        paper = _paper(src, folder / f"{slug}.paper")
        return {
            "src": src,
            "kind": "generated",
            "credit": credit,
            "focus": [0.5, 0.45],
            "paper": paper,
        }, True
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


def _attach_pictures(
    rows: list[dict[str, Any]],
    mode: str,
    size: int,
    base: str,
    token: str,
    fal_key: str,
    data_dir: Path,
) -> list[str]:
    """Give each row a ``pic`` per the pictures option. Returns notices."""
    notices: list[str] = []
    if mode == "none" or not rows:
        return notices
    pack = _pack() if mode.startswith("plate") else {}
    wants_gen = mode in ("plate_gen", "gen")
    if wants_gen and not fal_key:
        notices.append(NOTE_NO_KEY)

    def plate_or_photo(row: dict[str, Any]) -> dict[str, Any] | None:
        pic = _plate(row["sci"], pack, size, data_dir) if pack else None
        return pic

    # Plates and photos are independent fetches, so they run side by side; a
    # generated painting is at most one per fetch and runs after.
    with ThreadPoolExecutor(max_workers=6) as pool:
        plates = list(pool.map(plate_or_photo, rows)) if pack else [None] * len(rows)
    made = False
    for row, plate in zip(rows, plates, strict=True):
        pic = plate
        if pic is None and wants_gen:
            pic, did = _generated(row, size, data_dir, fal_key, allow_new=not made)
            made = made or did
        row["pic"] = pic
    missing = [r for r in rows if not r.get("pic")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        photos = list(
            pool.map(lambda r: _photo(r, size, base, token, data_dir), missing)
        )
    for row, pic in zip(missing, photos, strict=True):
        row["pic"] = pic
    return notices


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

    # Pictures only where the layout draws them: one big one for the single
    # bird layouts, a thumbnail per row for the list and gallery.
    notices: list[str] = []
    if layout in ("latest", "plate", "bleed") and latest:
        notices = _attach_pictures(
            [latest], pictures, SIZE[layout], base, token, fal_key, data_dir
        )
    elif layout in ("list", "gallery"):
        notices = _attach_pictures(
            shown, pictures, SIZE[layout], base, token, fal_key, data_dir
        )

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
    elif layout == "plate":
        groups = FONT_GROUPS["engraved"]
    credits = []
    for s in [*shown, *([latest] if latest else [])]:
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
        "notices": notices,
        "font_css": _font_css(groups),
        "empty": not species,
    }
