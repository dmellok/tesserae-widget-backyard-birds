"""Propose classic plates for the pack, for a person to review.

Not used at runtime. For each scientific name it lists Wikimedia Commons'
"Category:<name> (illustrations)", keeps public-domain still images, scores
them (a known plate series beats an unknown print, a big scan beats a small
one), crops the best few to the bird, and writes:

  tools/out/candidates.json   every scored candidate with its crop box
  tools/out/review.html       a contact sheet, top three per species

A reviewer then copies the chosen candidate into plates.json, or runs
``--accept`` to take the top pick for every species they have looked at.

    python tools/build_plates.py --species "Turdus merula" "Parus major"
    python tools/build_plates.py --file species.txt
    python tools/build_plates.py --accept "Turdus merula=2" "Parus major"

Commons asks for a descriptive User-Agent and polite pacing; the script
sleeps between calls and caches API answers under tools/out/cache.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
CACHE = OUT / "cache"
API = "https://commons.wikimedia.org/w/api.php"
UA = "tesserae-backyard-birds-plate-builder/0.1 (https://github.com/dmellok/tesserae)"

# Plate series worth preferring, matched against title, description and
# categories. Weighted by how consistently they give one clean, coloured bird.
SERIES = [
    (r"birds of america", "Audubon, The Birds of America", 9),
    (r"audubon", "Audubon", 7),
    (
        r"birds of australia|synopsis of the birds of australia",
        "Gould, The Birds of Australia",
        8,
    ),
    (r"birds of asia", "Gould, The Birds of Asia", 8),
    (r"birds of (great )?britain|birds of europe", "Gould", 7),
    (r"\bgould\b", "Gould", 6),
    (r"naumann", "Naumann, Naturgeschichte der Vögel Mitteleuropas", 8),
    (r"fauna japonica", "Siebold, Fauna Japonica", 8),
    (r"iconographia zoologica", "Iconographia Zoologica (Artis Library)", 6),
    (r"keulemans", "Keulemans", 7),
    (r"thorburn", "Thorburn", 7),
    (r"lilford", "Lilford, Coloured Figures of the Birds of the British Islands", 7),
    (r"dresser", "Dresser, A History of the Birds of Europe", 7),
    (r"birds of la plata", "Hudson, Birds of La Plata", 6),
    (r"monograph of the", "monograph plate", 5),
    (r"catalogue of the birds", "Catalogue of the Birds in the British Museum", 5),
    (r"wolf\b", "Joseph Wolf", 5),
    (r"plate|tafel|taf\.|pl\.", "plate", 2),
    (r"print|lithograph|engraving|chromolitho", "print", 2),
]
REJECT = re.compile(
    r"graffiti|egg|skeleton|skull|map|caption|sketch ?book|fleuron|stamp|coin|"
    r"logo|diagram|anatom|feather|nest only|\.(ogg|oga|wav|mp3|svg|pdf|djvu|webm)$",
    re.I,
)
PD = re.compile(r"public domain|\bpd\b|cc0|pd-|no restrictions", re.I)


def _get(params: dict[str, str]) -> dict:
    params = {**params, "format": "json", "formatversion": "2"}
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cached = CACHE / f"{key}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    time.sleep(0.6)
    req = urllib.request.Request(
        API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA}
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.load(resp)
    cached.write_text(json.dumps(data), encoding="utf-8")
    return data


def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").strip()


def candidates(sci: str) -> list[dict]:
    data = _get(
        {
            "action": "query",
            "generator": "categorymembers",
            "gcmtitle": f"Category:{sci} (illustrations)",
            "gcmtype": "file",
            "gcmlimit": "60",
            "prop": "imageinfo|categories",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": "1600",
            "cllimit": "max",
        }
    )
    out = []
    for page in (data.get("query") or {}).get("pages", []):
        title = page.get("title", "")
        info = (page.get("imageinfo") or [{}])[0]
        md = info.get("extmetadata") or {}
        lic = _strip((md.get("LicenseShortName") or {}).get("value", ""))
        desc = _strip((md.get("ImageDescription") or {}).get("value", ""))[:400]
        cats = " ".join(c.get("title", "") for c in page.get("categories", []))
        if REJECT.search(title) or not PD.search(lic + " " + cats):
            continue
        if info.get("mime") not in ("image/jpeg", "image/png", "image/tiff"):
            continue
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        if min(w, h) < 600:
            continue
        hay = f"{title} {desc} {cats}".lower()
        score, source = 0, ""
        for pattern, name, weight in SERIES:
            if re.search(pattern, hay):
                if weight > score:
                    score, source = weight, name
        # Multi-species plates crop worse; a title listing "1." "2." usually is one.
        if re.search(r"\b2\.\s", title) or " and " in title.lower():
            score -= 2
        score += min(3, max(w, h) // 1500)
        out.append(
            {
                "sci": sci,
                "file": title.removeprefix("File:"),
                "score": score,
                "source": source or "illustration",
                "license": lic,
                "artist": _strip((md.get("Artist") or {}).get("value", ""))[:120],
                "date": _strip((md.get("DateTimeOriginal") or {}).get("value", ""))[
                    :40
                ],
                "thumb": info.get("thumburl", ""),
                "width": w,
                "height": h,
            }
        )
    out.sort(key=lambda c: -c["score"])
    return out


def _bbox(im: Image.Image) -> tuple[float, float, float, float]:
    """One pass: the box around everything that differs from the dominant
    paper colour, after an opening that erases thin lines (plate marks,
    borders, caption hairlines), ignoring the outer 5% where scans carry the
    book edge and colour bars."""
    import numpy as np

    a = np.asarray(im.convert("RGB")).astype(int)
    h, w, _ = a.shape
    m = int(0.05 * min(w, h))
    inner = a[m : h - m, m : w - m]
    q = (inner // 16).reshape(-1, 3)
    vals, counts = np.unique(q, axis=0, return_counts=True)
    paper = vals[counts.argmax()] * 16 + 8
    mask = np.abs(inner - paper).sum(axis=2) > 90
    k = max(5, (min(w, h) // 220) | 1)
    mk = Image.fromarray((mask * 255).astype("uint8"))
    mk = mk.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k * 3))
    mask = np.asarray(mk) > 0
    rows, cols = (
        np.where(mask.mean(axis=1) > 0.03)[0],
        np.where(mask.mean(axis=0) > 0.03)[0],
    )
    if not len(rows) or not len(cols):
        return (0.0, 0.0, 1.0, 1.0)
    pad = 0.025 * max(w, h)
    x0, x1 = max(0, cols[0] + m - pad), min(w, cols[-1] + m + pad)
    y0, y1 = max(0, rows[0] + m - pad), min(h, rows[-1] + m + pad)
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def autocrop(im: Image.Image) -> tuple[float, float, float, float]:
    """Crop box (fractions of the whole scan) around the drawing. Two passes:
    the first finds the printed area, the second the drawing inside it, which
    matters for small birds on big pages."""
    x0, y0, x1, y1 = _bbox(im)
    w, h = im.size
    inner = im.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    a0, b0, a1, b1 = _bbox(inner)
    cw, ch = x1 - x0, y1 - y0
    box = (x0 + a0 * cw, y0 + b0 * ch, x0 + a1 * cw, y0 + b1 * ch)
    return tuple(round(v, 4) for v in box)  # type: ignore[return-value]


def _image(url: str) -> Image.Image:
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    path = CACHE / f"{key}.img"
    if not path.exists():
        time.sleep(0.6)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            path.write_bytes(resp.read())
    return Image.open(io.BytesIO(path.read_bytes()))


def build(species: list[str], top: int = 3) -> None:
    OUT.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    store = OUT / "candidates.json"
    found = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}
    for sci in species:
        try:
            cands = candidates(sci)[:top]
        except Exception as err:  # noqa: BLE001 - a tool run should carry on
            print(f"{sci}: {err}", file=sys.stderr)
            continue
        for c in cands:
            try:
                c["box"] = list(autocrop(_image(c["thumb"])))
            except Exception as err:  # noqa: BLE001
                c["box"] = [0, 0, 1, 1]
                print(f"{sci}: crop failed {err}", file=sys.stderr)
        found[sci] = cands
        print(
            f"{sci}: {len(cands)} candidate(s)"
            + (f", best {cands[0]['source']}" if cands else "")
        )
    store.write_text(json.dumps(found, indent=1, ensure_ascii=False), encoding="utf-8")
    _review(found)


def _review(found: dict) -> None:
    cells = []
    for sci, cands in found.items():
        tiles = (
            "".join(
                f"""<figure><div class="im" style="background-image:url('{c["thumb"]}');
              background-size:{100 / max(0.01, c["box"][2] - c["box"][0]):.1f}% {100 / max(0.01, c["box"][3] - c["box"][1]):.1f}%;
              background-position:{c["box"][0] / max(0.01, 1 - (c["box"][2] - c["box"][0])) * 100:.1f}% {c["box"][1] / max(0.01, 1 - (c["box"][3] - c["box"][1])) * 100:.1f}%"></div>
              <figcaption><b>{i}</b> · {c["score"]} · {c["source"]}<br>{c["file"][:70]}</figcaption></figure>"""
                for i, c in enumerate(cands)
            )
            or "<p>none</p>"
        )
        cells.append(f"<section><h2>{sci}</h2><div class=row>{tiles}</div></section>")
    (OUT / "review.html").write_text(
        """<meta charset=utf-8><style>body{font:13px system-ui;margin:16px;background:#f4f2ee}
        section{margin:0 0 18px}h2{font-size:16px;margin:0 0 6px;font-style:italic}.row{display:flex;gap:10px}
        figure{margin:0;width:230px}.im{width:230px;height:230px;background-repeat:no-repeat;background-color:#fff;border:1px solid #999}
        figcaption{font-size:11px;margin-top:3px}</style>"""
        + "".join(cells),
        encoding="utf-8",
    )
    print(f"review sheet: {OUT / 'review.html'}")


def accept(picks: list[str]) -> None:
    found = json.loads((OUT / "candidates.json").read_text(encoding="utf-8"))
    pack_path = ROOT / "plates.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    for pick in picks:
        sci, _, idx = pick.partition("=")
        c = found[sci][int(idx or 0)]
        x0, y0, x1, y1 = c["box"]
        pack["plates"][sci] = {
            "file": c["file"],
            "box": c["box"],
            "focus": [round((x0 + x1) / 2, 3), round(y0 + (y1 - y0) * 0.42, 3)],
            "source": c["source"],
            "artist": c["artist"],
            "license": c["license"],
        }
        print(f"accepted {sci}: {c['file']}")
    pack["plates"] = dict(sorted(pack["plates"].items()))
    pack_path.write_text(
        json.dumps(pack, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--species", nargs="*", default=[])
    ap.add_argument("--file")
    ap.add_argument("--accept", nargs="*")
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()
    if args.accept:
        accept(args.accept)
    else:
        names = list(args.species)
        if args.file:
            names += [
                ln.strip()
                for ln in Path(args.file).read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
        build(names, args.top)
