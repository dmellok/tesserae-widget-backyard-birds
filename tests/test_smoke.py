"""backyard_birds: demo data, BirdNET-Go parsing, the picture fallback chain,
fonts, and a render smoke test of client.js. No network: every outbound call is
patched."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("bb_server", ROOT / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


server = _load()

MELBOURNE = {"latitude": -37.8, "longitude": 145.0, "timezone": "Australia/Melbourne"}
BERLIN = {"latitude": 52.5, "longitude": 13.4, "timezone": "Europe/Berlin"}


def _at(hour: int, minute: int = 14, tz: str = "Australia/Melbourne"):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz_=None):  # noqa: ARG003
            return datetime(2026, 10, 11, hour, minute, tzinfo=ZoneInfo(tz))

    return patch.object(server, "datetime", Frozen)


def _settle(timeout: float = 10.0) -> None:
    """Wait for background paintings to finish."""
    end = time.time() + timeout
    while server._PAINTING and time.time() < end:
        time.sleep(0.02)
    assert not server._PAINTING, "a painting never finished"


def _jpeg(w: int = 300, h: int = 400) -> bytes:
    """A bird-ish blot on cream paper, like a plate or a painting."""
    from PIL import ImageDraw

    im = Image.new("RGB", (w, h), (243, 234, 214))
    ImageDraw.Draw(im).ellipse(
        (w * 0.3, h * 0.25, w * 0.7, h * 0.7), fill=(200, 120, 40)
    )
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def _png_alpha() -> bytes:
    """A cut-out as the model returns it: a bird-shaped blob on transparency."""
    from PIL import ImageDraw

    w, h = 300, 400
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((80, 100, 220, 300), fill=(90, 60, 30, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def offline():
    """No network: plates, photos and fal all answer from here."""

    class Calls(list):
        bodies: list[bytes]

    calls = Calls()
    calls.bodies = []

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        calls.append(url)
        # the painting requests alone; the cut-out model's follow the paintings
        if "nano-banana" in url and kw.get("body"):
            calls.bodies.append(kw["body"])
        if "inaturalist.org/v1/taxa?" in url:
            return json.dumps({"results": [{"id": 7}]}).encode(), None
        if "inaturalist.org/v1/taxa/7" in url:
            photo = {
                "url": "https://inat.example/photos/1/square.jpg",
                "license_code": "cc-by",
                "attribution_name": "A. Birder",
            }
            return json.dumps(
                {"results": [{"taxon_photos": [{"photo": photo}]}]}
            ).encode(), None
        if "birefnet" in url:
            return json.dumps(
                {"image": {"url": "https://fal.example/cut.png"}}
            ).encode(), None
        if "fal.run" in url:
            return json.dumps(
                {"images": [{"url": "https://fal.example/out.jpg"}]}
            ).encode(), None
        if accept == "image/*" and url.endswith(".png"):
            return _png_alpha(), None
        if accept == "image/*":
            return _jpeg(), None
        return None, "unreachable"

    with patch.object(server, "_http", side_effect=fake_http):
        yield calls


def test_demo_is_regional_and_stable(tmp_path):
    with _at(8):
        a = server.fetch(
            {**MELBOURNE, "pictures": "none"}, {}, ctx={"data_dir": str(tmp_path)}
        )
        b = server.fetch(
            {**MELBOURNE, "pictures": "none"}, {}, ctx={"data_dir": str(tmp_path)}
        )
    assert a["demo"] and a["region"] == "au"
    assert a["species"] == b["species"], "the same minute gives the same garden"
    names = {s["name"] for s in a["species"]}
    assert "Australian Magpie" in names or "Noisy Miner" in names
    assert (
        a["detections_today"] == sum(s["count"] for s in a["species"])
        or len(a["species"]) < a["species_today"]
    )
    with _at(8, tz="Europe/Berlin"):
        eu = server.fetch(
            {**BERLIN, "pictures": "none"}, {}, ctx={"data_dir": str(tmp_path)}
        )
    assert eu["region"] == "eu"


def test_demo_owls_call_at_night(tmp_path):
    with _at(23, tz="Europe/Berlin"):
        r = server.fetch(
            {**BERLIN, "pictures": "none", "count": 12},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert r["latest"]["name"] == "Tawny Owl"


def test_birdnet_parse_and_cache(tmp_path):
    daily = [
        {
            "scientific_name": "Turdus merula",
            "common_name": "Amsel",
            "count": 41,
            "hourly_counts": [0] * 6 + [9, 12] + [0] * 16,
            "first_heard": "06:02:11",
            "latest_heard": "07:58:40",
            "max_confidence": 0.96,
            "is_new_species": True,
            "thumbnail_url": "/api/v2/media/species-image/Turdus%20merula",
        }
    ]
    recent = [
        {"commonName": "Amsel", "time": "07:58:40", "confidence": 0.93},
        {"commonName": "Kohlmeise", "time": "07:57:02", "confidence": 0.41},
    ]
    seen = []

    def fake_json(url, token="", **kw):  # noqa: ARG001
        seen.append((url, token))
        return (daily if "species/daily" in url else recent), None

    with (
        _at(8, tz="Europe/Berlin"),
        patch.object(server, "_json", side_effect=fake_json),
    ):
        opts = {
            **BERLIN,
            "url": "birdnet.local:8080",
            "token": "t0k",
            "pictures": "none",
        }
        r = server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
        server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
    assert not r["demo"]
    assert seen[0][0].startswith(
        "http://birdnet.local:8080/api/v2/analytics/species/daily?min_confidence=70"
    )
    assert seen[0][1] == "t0k"
    assert len(seen) == 2, "the second fetch inside refresh_min is served from cache"
    s = r["species"][0]
    assert (s["name"], s["count"], s["first"], s["last"], s["new"]) == (
        "Amsel",
        41,
        "06:02",
        "07:58",
        True,
    )
    assert s["ago_min"] == 16
    assert [x["name"] for x in r["recent"]] == ["Amsel"], (
        "low-confidence detections are dropped"
    )
    assert r["new_today"] == ["Amsel"]


def test_birdnet_errors_are_friendly(tmp_path):
    with patch.object(server, "_json", return_value=(None, "auth")):
        r = server.fetch({"url": "http://x"}, {}, ctx={"data_dir": str(tmp_path)})
    assert "API token" in r["error"]
    with patch.object(server, "_json", return_value=(None, "unreachable")):
        r = server.fetch(
            {"url": "http://x:1"}, {}, ctx={"data_dir": str(tmp_path / "b")}
        )
    assert "Couldn't reach BirdNET-Go" in r["error"]


def test_plate_first_then_toned_photo(tmp_path, offline):
    pack = {
        "Gymnorhina tibicen": {
            "file": "Magpie.jpg",
            "box": [0.1, 0.1, 0.9, 0.9],
            "focus": [0.5, 0.4],
            "source": "Gould, The Birds of Australia",
        }
    }
    with _at(8), patch.object(server, "_pack", return_value=pack):
        r = server.fetch(
            {**MELBOURNE, "layout": "gallery", "count": 12},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    pics = {s["sci"]: s["pic"] for s in r["species"]}
    assert pics["Gymnorhina tibicen"]["kind"] == "plate"
    assert pics["Gymnorhina tibicen"]["src"].startswith("data:image/jpeg;base64,")
    others = [p for sci, p in pics.items() if sci != "Gymnorhina tibicen"]
    assert others and all(p["kind"] == "photo" for p in others)
    assert any("Gould" in c for c in r["credits"]) and any(
        "iNaturalist" in c for c in r["credits"]
    )
    assert any("Special:FilePath/Magpie.jpg" in u for u in offline)


def test_token_only_goes_to_birdnet(tmp_path):
    sent = []

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        sent.append((url, token))
        return _jpeg(), None

    rows = [
        {
            "sci": "Turdus merula",
            "name": "Amsel",
            "thumb": "https://upload.wikimedia.org/x.jpg",
        },
        {
            "sci": "Parus major",
            "name": "Kohlmeise",
            "thumb": "/api/v2/media/species-image/Parus",
        },
    ]
    with patch.object(server, "_http", side_effect=fake_http):
        for row in rows:
            server._photo(row, 160, "http://birdnet.local", "secret", tmp_path)
    assert ("https://upload.wikimedia.org/x.jpg", "") in sent
    assert ("http://birdnet.local/api/v2/media/species-image/Parus", "secret") in sent


def test_resized_for_the_layout(tmp_path, offline):
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "layout": "gallery", "pictures": "photo"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    raw = base64.b64decode(r["species"][0]["pic"]["src"].split(",", 1)[1])
    assert max(Image.open(io.BytesIO(raw)).size) <= server.SIZE["gallery"]
    # the list is a hero page now: one big picture of the hero bird, none per row
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "layout": "list", "pictures": "photo"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    raw = base64.b64decode(r["latest"]["pic"]["src"].split(",", 1)[1])
    assert max(Image.open(io.BytesIO(raw)).size) <= server.SIZE["latest"]
    assert not any(s.get("pic") for s in r["species"])


def test_generated_needs_a_key(tmp_path, offline):
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "layout": "bleed", "pictures": "gen"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert server.NOTE_NO_KEY in r["notices"]
    assert r["latest"]["pic"]["kind"] == "photo"
    assert not any("fal.run" in u for u in offline)


def test_generated_once_per_species(tmp_path, offline):
    opts = {**MELBOURNE, "layout": "bleed", "pictures": "gen"}
    with _at(8):
        first = server.fetch(
            opts, {"fal_api_key": "k"}, ctx={"data_dir": str(tmp_path)}
        )
        _settle()
        for _ in range(2):
            r = server.fetch(
                opts, {"fal_api_key": "k"}, ctx={"data_dir": str(tmp_path)}
            )
    # the first render does not wait for fal: it shows a photo and says so
    assert first["latest"]["pic"]["kind"] == "photo"
    assert any(n.startswith("Painting the ") for n in first["status"])
    assert r["latest"]["pic"]["kind"] == "generated"
    assert "AI painting" in r["latest"]["pic"]["credit"]
    assert not r["notices"] and not r["status"]
    assert sum("nano-banana" in u for u in offline) == 1, "painted once, then kept"


def test_generated_failure_is_shown_and_not_retried(tmp_path, offline):
    calls: list[str] = []
    real = server._http.side_effect

    def refusing(url, token="", accept="application/json", **kw):
        calls.append(url)
        if "fal.run" in url:
            return None, "HTTP 403: Exhausted balance"
        return real(url, token, accept, **kw)

    opts = {**MELBOURNE, "layout": "bleed", "pictures": "gen"}
    with _at(8), patch.object(server, "_http", side_effect=refusing):
        server.fetch(opts, {"fal_api_key": "k"}, ctx={"data_dir": str(tmp_path)})
        _settle()
        r = server.fetch(opts, {"fal_api_key": "k"}, ctx={"data_dir": str(tmp_path)})
        _settle()
    assert r["latest"]["pic"]["kind"] == "photo"
    assert any("Exhausted balance" in n for n in r["notices"]), r["notices"]
    assert sum("fal.run" in u for u in calls) == 1


def test_repaint_keeps_the_old_painting(tmp_path, offline):
    job = {
        "key": "manorina_melanocephala",
        "style": "gould",
        "name": "Noisy Miner",
        "sci": "Manorina melanocephala",
    }
    with _at(8):
        server._start_painting(job, tmp_path, "k")
        _settle()
        server._start_painting(job, tmp_path, "k")
        _settle()
    g = server._gallery(tmp_path)
    (bird,) = g["birds"]
    assert bird["name"] == "Noisy Miner" and bird["active"]
    assert len(bird["archive"]) == 1
    assert g["used"] == 2


def test_painting_marked_on_admin_page_replaces_the_plate(tmp_path, offline):
    opts = {**MELBOURNE, "layout": "bleed", "pictures": "plate_photo"}
    with _at(8):
        before = server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
        L = before["latest"]
        assert L["pic"]["kind"] == "plate"
        slug = server._slug(L["sci"])
        server._start_painting(
            {"key": slug, "style": "gould", "name": L["name"], "sci": L["sci"]},
            tmp_path,
            "k",
        )
        _settle()
        server._write(
            tmp_path / "gen" / "prefs.json",
            json.dumps({slug: {"prefer": True}}).encode(),
        )
        after = server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
    assert after["latest"]["pic"]["kind"] == "generated"


def test_photo_tone_option(tmp_path, offline):
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "photo_tone": "natural", "pictures": "none"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        bad = server.fetch(
            {**MELBOURNE, "photo_tone": "sepia", "pictures": "none"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert r["photo_tone"] == "natural"
    assert bad["photo_tone"] == "toned"


def test_generated_one_new_painting_per_fetch(tmp_path, offline):
    with _at(8):
        server.fetch(
            {**MELBOURNE, "layout": "gallery", "pictures": "gen", "count": 6},
            {"fal_api_key": "k"},
            ctx={"data_dir": str(tmp_path)},
        )
        _settle()
    assert sum("nano-banana" in u for u in offline) == 1


def test_fonts_only_for_the_style_on_screen(tmp_path, offline):
    with _at(8):
        notebook = server.fetch(
            {**MELBOURNE, "layout": "bleed", "caption": "notebook", "pictures": "none"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        lst = server.fetch(
            {**MELBOURNE, "layout": "list", "pictures": "none"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert "font-family:'Caveat'" in notebook["font_css"]
    assert "Playfair" not in notebook["font_css"]
    # every page is printed matter now: the list carries the engraved faces, not the notebook's hand
    assert "IM Fell English" in lst["font_css"]
    assert "Caveat" not in lst["font_css"]


def test_panel_levels_convert_pictures_and_keep_separate_caches(tmp_path, offline):
    # A picture asked for by a greyscale or black-and-white panel comes back
    # converted, from its own cache file, and the colour one is untouched.
    pics = {}
    for levels in ("colour", "gray16", "gray4", "mono"):
        with _at(8):
            r = server.fetch(
                {
                    **MELBOURNE,
                    "layout": "gallery",
                    "pictures": "photo",
                    "panel": levels,
                },
                {},
                ctx={"data_dir": str(tmp_path)},
            )
        assert r["panel"] == levels
        raw = base64.b64decode(r["species"][0]["pic"]["src"].split(",", 1)[1])
        pics[levels] = Image.open(io.BytesIO(raw))
    assert pics["colour"].mode == "RGB"
    assert all(pics[k].mode == "L" for k in ("gray16", "gray4", "mono"))
    cached = sorted(p.name for p in (tmp_path / "img").glob("f_*.jpg"))
    assert len(cached) == 4 and sum("_mono" in n for n in cached) == 1


def test_mono_paper_is_white_and_options_land_in_the_payload(tmp_path, offline):
    pack = {
        "Gymnorhina tibicen": {
            "file": "Magpie.jpg",
            "box": [0, 0, 1, 1],
            "focus": [0.5, 0.4],
            "source": "Gould, The Birds of Australia",
        }
    }
    with _at(8), patch.object(server, "_pack", return_value=pack):
        r = server.fetch(
            {
                **MELBOURNE,
                "layout": "latest",
                "panel": "mono",
                "chart_labels": "peak",
                "count": 12,
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert r["chart_labels"] == "peak"
    plate = next(s["pic"] for s in [r["latest"]] if s["pic"]["kind"] == "plate")
    assert plate["paper"] == "#ffffff"
    with _at(8):
        bad = server.fetch(
            {**MELBOURNE, "panel": "sepia", "chart_labels": "all", "pictures": "none"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert (bad["panel"], bad["chart_labels"]) == ("colour", "none")


def test_pack_entries_are_well_formed():
    pack = json.loads((ROOT / "plates.json").read_text(encoding="utf-8"))["plates"]
    assert pack
    for sci, e in pack.items():
        assert " " in sci and sci[0].isupper(), sci
        x0, y0, x1, y1 = e["box"]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, sci
        assert e["file"] and e["source"], sci


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    ("layout", "caption"),
    [
        (lay, "band")
        for lay in ("list", "latest", "gallery", "chart", "plate", "cluster", "chorus")
    ]
    + [("chart", "mono-counts"), ("list", "gray4-peak")]
    + [("bleed", cap) for cap in server.CAPTIONS],
)
def test_client_renders_every_layout(tmp_path, layout, caption):
    with _at(8):
        panel, _, labels = (
            caption.partition("-") if "-" in caption else ("colour", "", "none")
        )
        data = server.fetch(
            {
                **MELBOURNE,
                "layout": layout,
                "caption": caption if "-" not in caption else "band",
                "pictures": "none",
                "panel": panel,
                "chart_labels": labels,
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    script = (
        f"import('file://{ROOT}/client.js').then(m => {{ const s = {{innerHTML: ''}};"
        f" m.default(s, {{data: {json.dumps(data)}, cell: {{size: 'md'}}}});"
        " if (!s.innerHTML.includes('class=\"bb lay-')) throw new Error('no render');"
        " console.log('ok'); })"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_art_styles_keep_their_own_paintings(tmp_path, offline):
    base = {**MELBOURNE, "layout": "bleed", "pictures": "gen"}
    with _at(8):
        for style in ("gould", "woodblock"):
            server.fetch(
                {**base, "art_style": style},
                {"fal_api_key": "k"},
                ctx={"data_dir": str(tmp_path)},
            )
            _settle()
        a = server.fetch(
            {**base, "art_style": "woodblock"},
            {"fal_api_key": "k"},
            ctx={"data_dir": str(tmp_path)},
        )
    assert a["latest"]["pic"]["kind"] == "generated"
    assert "woodblock" in a["latest"]["pic"]["credit"].lower()
    sent = [json.loads(b) for b in offline.bodies]
    assert "Ohara Koson" in sent[-1]["prompt"] and "Gould" in sent[0]["prompt"]
    keys = {e["slug"] for e in server._gallery(tmp_path)["birds"]}
    assert len(keys) == 2 and any(k.endswith("--woodblock") for k in keys)


def test_cluster_montage_without_ai(tmp_path, offline):
    with _at(8):
        r = server.fetch(
            {
                **MELBOURNE,
                "layout": "cluster",
                "pictures": "plate_photo",
                "cluster_size": 5,
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    c = r["cluster"]
    assert c["montage"] and c["pic"] is None and len(c["birds"]) == 5
    assert all(b["pic"] for b in c["birds"])
    counts = [b["count"] for b in c["birds"]]
    assert counts == sorted(counts, reverse=True), "the busiest birds"
    assert not any("fal.run" in u for u in offline)


def test_cluster_paints_each_bird_then_blends_them(tmp_path, offline):
    opts = {
        **MELBOURNE,
        "layout": "cluster",
        "cluster_style": "blend",
        "pictures": "gen",
        "cluster_size": 3,
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 480, "cell_h": 800}
    kinds = []
    with _at(8):
        for _ in range(5):
            r = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
            c = r["cluster"]
            kinds.append("montage" if c["montage"] else c["pic"]["kind"])
    # three birds painted one per refresh, then one blend of the three
    assert kinds == ["montage", "collage", "collage", "collage", "generated"], kinds
    assert len(offline.bodies) == 4
    blend = json.loads(offline.bodies[-1])
    assert blend["image_urls"][0].startswith("data:image/jpeg;base64,")
    assert "collage of 3 birds" in blend["prompt"] and blend["aspect_ratio"] == "3:4"
    assert [b["name"] for b in c["birds"]] == [b["name"] for b in r["cluster"]["birds"]]
    assert len(c["birds"]) == 3 and "AI painting" in c["pic"]["credit"]
    g = server._gallery(tmp_path)
    assert sum(e["cluster"] for e in g["birds"]) == 1, "the blend is listed once"
    assert not any(e["slug"].startswith(("collage_", "sheet_")) for e in g["birds"])


def test_cluster_reblends_only_after_the_interval(tmp_path, offline):
    opts = {
        **MELBOURNE,
        "layout": "cluster",
        "pictures": "gen",
        "cluster_size": 2,
        "cluster_style": "blend",
        "cluster_every": 3,
    }
    ctx = {"data_dir": str(tmp_path)}
    with _at(8):
        for _ in range(4):
            server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
    assert len(offline.bodies) == 3, "two birds and one blend"
    with _at(8):
        for _ in range(3):  # a third bird joins: painted, but too soon to re-blend
            r = server.fetch({**opts, "cluster_size": 3}, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
    assert len(offline.bodies) == 4
    assert (
        r["cluster"]["pic"]["kind"] == "generated" and len(r["cluster"]["birds"]) == 2
    )
    meta_path = tmp_path / "gen" / "cluster--gould.json"
    meta = json.loads(meta_path.read_text())
    meta["made"] -= 4 * 3600
    meta_path.write_text(json.dumps(meta))
    with _at(8):
        server.fetch({**opts, "cluster_size": 3}, {"fal_api_key": "k"}, ctx=ctx)
        _settle()
        r = server.fetch({**opts, "cluster_size": 3}, {"fal_api_key": "k"}, ctx=ctx)
    assert len(offline.bodies) == 5
    assert len(r["cluster"]["birds"]) == 3
    (blend,) = [e for e in server._gallery(tmp_path)["birds"] if e["cluster"]]
    assert len(blend["archive"]) == 1, "the earlier blend is kept"


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
@pytest.mark.parametrize("portrait", [False, True])
def test_collage_is_an_oval_cluster(n, portrait):
    slots = server._oval_slots(n, portrait)
    assert len(slots) == n
    for x, y, w, h in slots:
        assert 0 <= x and 0 <= y and x + w <= 1.0001 and y + h <= 1.0001
    centres = [(x + w / 2, y + h / 2) for x, y, w, h in slots]
    if n >= 4:
        # the busiest bird sits in the middle, largest, the rest round it
        cx, cy = centres[0]
        assert abs(cx - 0.5) < 0.01 and abs(cy - 0.5) < 0.01
        assert slots[0][2] * slots[0][3] >= max(w * h for _x, _y, w, h in slots[1:])
        ring = centres[1:]
    else:
        ring = centres
    # ring birds sit roughly on one ellipse round the centre
    radii = [((x - 0.5) / 0.33) ** 2 + ((y - 0.5) / 0.31) ** 2 for x, y in ring]
    assert max(radii) - min(radii) < 0.6, radii


def test_specimen_sheet_prints_each_painted_bird(tmp_path, offline):
    opts = {**MELBOURNE, "layout": "cluster", "pictures": "gen", "cluster_size": 4}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    with _at(8):
        # four refreshes paint the four birds; the fifth builds the sheet in
        # the background (showing the three-bird one), the sixth shows it
        for _ in range(5):
            server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
        t0 = time.time()
        r = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
        assert time.time() - t0 < 2, "a cached sheet is quick to serve"
    c = r["cluster"]
    assert c["sheet"] and c["pic"]["kind"] == "sheet" and not c["waiting"]
    assert len(c["birds"]) == 4 and abs(c["ratio"] - 800 / 480) < 0.02
    # four paintings, no blend: the sheet costs nothing more
    assert len(offline.bodies) == 4
    # boxes may overlap (the packing is tight: shapes touch, boxes cross),
    # but every bird is on the page and the group fills a good part of it
    boxes = [b["box"] for b in c["birds"]]
    for x, y, w, h in boxes:
        assert 0 <= x and 0 <= y and x + w <= 1 and y + h <= 1
    span_w = max(x + w for x, y, w, h in boxes) - min(x for x, y, w, h in boxes)
    span_h = max(y + h for x, y, w, h in boxes) - min(y for x, y, w, h in boxes)
    assert span_w > 0.45 and span_h > 0.45, (span_w, span_h)
    assert c["label"] >= 13 / 480 - 1e-9, "names at least 13 px on the panel"


def test_specimen_sheet_sizes_birds_by_length():
    lengths = server._lengths()
    assert server._size_factor("Corvus corone", lengths) > server._size_factor(
        "Troglodytes troglodytes", lengths
    )
    assert server._size_factor("Unknown bird", lengths) == 1.0


def test_daily_limit_is_a_setting(tmp_path, offline):
    gen = tmp_path / "gen"
    gen.mkdir()
    (gen / "budget.json").write_text(json.dumps({time.strftime("%Y-%m-%d"): 12}))
    opts = {**MELBOURNE, "layout": "bleed", "pictures": "gen"}
    with _at(8):
        at_default = server.fetch(
            opts, {"fal_api_key": "k"}, ctx={"data_dir": str(tmp_path)}
        )
        _settle()
        raised = server.fetch(
            opts,
            {"fal_api_key": "k", "gen_daily_cap": "24"},
            ctx={"data_dir": str(tmp_path)},
        )
        _settle()
    assert any("limit of 12" in n for n in at_default["status"])
    assert any(n.startswith("Painting the ") for n in raised["status"])
    assert len(offline.bodies) == 1
    assert server._daily_cap({"gen_daily_cap": "7"}) == 12, "only the offered limits"


def test_photo_tone_light(tmp_path, offline):
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "photo_tone": "light", "pictures": "none"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert r["photo_tone"] == "light"


@pytest.mark.parametrize("mode", ["sheet", "numbered", "wreath", "notebook", "drawer"])
def test_cluster_looks_build_a_page(tmp_path, offline, mode):
    opts = {
        **MELBOURNE,
        "layout": "cluster",
        "pictures": "gen",
        "cluster_style": mode,
        "cluster_size": 3,
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    with _at(8):
        for _ in range(4):
            server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
        r = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
    c = r["cluster"]
    assert c["sheet"] and c["mode"] == mode and len(c["birds"]) == 3
    for b in c["birds"]:
        x, y = b["at"]
        assert 0 <= x <= 1 and 0 <= y <= 1
    if mode == "notebook":
        assert "font-family:'Caveat'" in r["font_css"]
    assert len(offline.bodies) == 3, "no blend for a printed page"


def test_progress_is_not_printed_on_the_panel(tmp_path, offline):
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "layout": "bleed", "pictures": "gen"},
            {"fal_api_key": "k"},
            ctx={"data_dir": str(tmp_path)},
        )
        _settle()
    assert any(n.startswith("Painting the ") for n in r["status"])
    assert not any(n.startswith("Painting") for n in r["notices"])


def test_white_bird_is_cut_out_whole_and_a_wash_dropped():
    from PIL import ImageDraw, ImageFilter

    paper = (243, 234, 214)
    im = Image.new("RGB", (600, 800), paper)
    d = ImageDraw.Draw(im)
    for y in range(520, 640):  # a grey wash under the bird
        t = (y - 520) / 120
        d.line((140, y, 480, y), fill=tuple(int(p * (1 - t) + 180 * t) for p in paper))
    d.ellipse((200, 240, 420, 510), fill=(252, 250, 244), outline=(90, 80, 70), width=3)
    d.line((120, 530, 520, 490), fill=(110, 80, 50), width=12)
    buf = io.BytesIO()
    im.filter(ImageFilter.GaussianBlur(0.8)).save(buf, "JPEG", quality=90)
    cut, mask, _paper = server._cutout_masked(buf.getvalue())
    import numpy as np

    m = np.asarray(mask) > 128
    # the crop is the bird and perch, not the wash below them
    assert cut.height < 360, cut.size
    # the white body is inside the mask, not just its outline
    assert (
        m[
            m.shape[0] // 2 - 40 : m.shape[0] // 2,
            m.shape[1] // 2 - 20 : m.shape[1] // 2 + 20,
        ].mean()
        > 0.95
    )


def _perched_painting(paper, branch, sky=None):
    """A bird on a branch on plain paper (or on a print's pale sky inside a
    ruled frame), and the cut-out model's mask of the bird alone: the
    branch, which runs in front of the bird's lower body, is cut away."""
    from PIL import ImageDraw, ImageFilter

    im = Image.new("RGB", (600, 800), paper)
    d = ImageDraw.Draw(im)
    if sky:
        d.rectangle((40, 40, 560, 760), fill=sky, outline=(60, 50, 40), width=3)
    d.ellipse((200, 200, 400, 520), fill=(120, 110, 100), outline=(50, 40, 30), width=3)
    # the branch, light and thin at one end, across the bird and out both sides
    d.line((70, 470, 300, 440), fill=branch, width=22)
    d.line((300, 440, 520, 410), fill=branch, width=14)
    d.line((520, 410, 545, 395), fill=(205, 190, 168), width=5)
    buf = io.BytesIO()
    im.filter(ImageFilter.GaussianBlur(0.8)).save(buf, "JPEG", quality=92)
    model = Image.new("L", im.size, 0)
    md = ImageDraw.Draw(model)
    md.ellipse((200, 200, 400, 520), fill=255)
    md.line((70, 470, 300, 440), fill=0, width=24)
    md.line((300, 440, 520, 410), fill=0, width=16)
    cut = Image.new("RGBA", im.size, (0, 0, 0, 0))
    cut.putalpha(model)
    png = io.BytesIO()
    cut.save(png, "PNG")
    return buf.getvalue(), png.getvalue()


@pytest.mark.parametrize("sky", [None, (196, 214, 222)])
def test_the_branch_a_bird_perches_on_is_kept_with_the_model_mask(sky):
    import numpy as np

    paper = (243, 234, 214)
    branch = (110, 80, 50)
    raw, cut_png = _perched_painting(paper, branch, sky)
    cut, mask, _paper = server._cutout_masked(raw, cut_png)
    page = Image.open(io.BytesIO(raw)).convert("RGB")
    whole = server._bird_mask(
        page, paper, Image.open(io.BytesIO(cut_png)).getchannel("A")
    )
    assert whole.getbbox() is not None and mask.getbbox() is not None
    # the crop's place on the page, from the same mask before and after it
    ox = whole.getbbox()[0] - mask.getbbox()[0]
    oy = whole.getbbox()[1] - mask.getbbox()[1]
    m = np.asarray(mask) > 127
    rgb = np.asarray(cut.convert("RGB")).astype(int)
    # the branch inside the bird's outline, off both sides of it, and the
    # light thin twig at its end: in the mask and printed, never whitened
    # into a pale stub
    for px, py in ((300, 440), (250, 447), (130, 462), (470, 417), (536, 401)):
        x, y = px - ox, py - oy
        assert 0 <= x < cut.width and 0 <= y < cut.height, (px, py, cut.size)
        assert m[y, x], f"the branch at {(px, py)} is in the mask"
        assert rgb[y, x].sum() < 3 * 232, f"the branch at {(px, py)} is printed"
    if sky:
        # the print's sky and its frame are not part of the perch
        assert m.mean() < 0.5, m.mean()
        assert cut.height < 520, cut.size


def test_every_art_style_makes_a_key_and_a_prompt():
    manifest = json.loads((ROOT / "plugin.json").read_text())
    offered = next(o for o in manifest["cell_options"] if o["name"] == "art_style")
    # offered in groups (test_every_style_is_in_exactly_one_group checks the order)
    assert sorted(c["value"] for c in offered["choices"]) == sorted(
        [*server.GEN_STYLES, "custom"]
    )
    for style, (label, how) in server.GEN_STYLES.items():
        key = server._pkey("turdus_merula", style)
        assert server._SLUG_RE.fullmatch(key), key
        assert label and how
        prompt = server.GEN_ONE.format(
            name="Eurasian Blackbird",
            sci="Turdus merula",
            style=how,
            pose=server.POSES["perched"],
        )
        assert "plain" in prompt and "Eurasian Blackbird" in prompt


def test_fal_arrangement_takes_a_scene_and_fidelity(tmp_path, offline):
    opts = {
        **MELBOURNE,
        "layout": "cluster",
        "pictures": "gen",
        "cluster_arrange": "fal",
        "cluster_scene": "wire",
        "cluster_fidelity": "free",
        "cluster_size": 2,
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    with _at(8):
        for _ in range(4):
            r = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
    blend = json.loads(offline.bodies[-1])
    assert "telephone wire" in blend["prompt"] and "re-pose" in blend["prompt"]
    assert r["cluster"]["pic"]["kind"] == "generated"
    assert (tmp_path / "gen" / "cluster_wire_free--gould.jpg").exists()
    (entry,) = [e for e in server._gallery(tmp_path)["birds"] if e["cluster"]]
    assert "wire" in entry["name"]


def test_font_choice_inlines_its_faces(tmp_path, offline):
    with _at(8):
        tw = server.fetch(
            {**MELBOURNE, "layout": "plate", "pictures": "none", "font": "typewriter"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        theme = server.fetch(
            {**MELBOURNE, "layout": "plate", "pictures": "none", "font": "theme"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        lst = server.fetch(
            {**MELBOURNE, "layout": "list", "pictures": "none", "font": "typewriter"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert tw["font"] == "typewriter" and "Special Elite" in tw["font_css"]
    assert "Special Elite" not in theme["font_css"]
    assert "Special Elite" in lst["font_css"], "the list takes the chosen face too"


def test_sun_and_moon_are_plausible():
    from datetime import datetime as dt

    melb = dt(2026, 10, 13, 12, 0, tzinfo=ZoneInfo("Australia/Melbourne"))
    rise, set_ = server._sun_times(-37.8, 145.0, melb)
    assert 5 * 60 < rise < 7 * 60 and 19 * 60 < set_ < 20 * 60 + 30, (rise, set_)
    phase, name = server._moon(melb)
    assert 0 <= phase < 1 and name
    assert (
        server._sun_times(80, 20, dt(2026, 6, 21, 12, tzinfo=ZoneInfo("UTC"))) is None
    )


def test_night_mode_shows_the_garden_asleep(tmp_path, offline):
    opts = {**MELBOURNE, "pictures": "none", "night": "asleep"}
    with _at(23, 30):
        r = server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
    assert r["sky"]["dark"] and r["sky"]["sunrise"] and r["sky"]["moon_name"]
    # the demo garden's owl calls through the night, so with 'asleep' the page
    # sleeps only once nothing has called for 90 minutes; owls mode keeps the owl
    with _at(23, 30):
        owls = server.fetch(
            {**opts, "night": "owls"}, {}, ctx={"data_dir": str(tmp_path)}
        )
    assert owls["sky"]["dark"]
    sunset = server._minutes(owls["sky"]["sunset"])
    assert owls["asleep"] or all(
        (server._minutes(s["last"]) or 0) >= sunset for s in owls["species"]
    ), "after dark, only birds heard since sunset"
    with _at(12):
        day = server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
    assert not day["sky"]["dark"] and day["asleep"] is None


def test_hero_picks_the_rarest_or_busiest(tmp_path, offline):
    with _at(8):
        busy = server.fetch(
            {
                **MELBOURNE,
                "layout": "plate",
                "pictures": "none",
                "hero": "busy",
                "count": 12,
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        quiet = server.fetch(
            {
                **MELBOURNE,
                "layout": "plate",
                "pictures": "none",
                "hero": "quiet",
                "count": 12,
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert busy["latest"]["count"] == max(s["count"] for s in busy["species"])
    assert quiet["latest"]["count"] == min(s["count"] for s in quiet["species"])
    assert busy["hero_kick"] == "Most calls to-day" and quiet["hero_kick"]


def test_returning_bird_after_a_long_absence(tmp_path, offline):
    sci = "Trichoglossus moluccanus"
    gen = tmp_path
    (gen / "rarity.json").write_text(
        json.dumps(
            {"days": ["2026-07-01", "2026-07-02"], "seen": {sci: ["2026-07-01"]}}
        )
    )
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "pictures": "none", "count": 12},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    lorikeet = next(s for s in r["species"] if s["sci"] == sci)
    assert lorikeet["returning"].startswith("back after 3 months"), lorikeet[
        "returning"
    ]
    others = [s for s in r["species"] if s["sci"] != sci]
    assert all(not s["returning"] for s in others)


def test_demo_sonogram_gives_a_song_line(tmp_path, offline):
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "layout": "bleed", "caption": "sonogram", "pictures": "none"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        plate = server.fetch(
            {**MELBOURNE, "layout": "plate", "pictures": "none"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        off = server.fetch(
            {**MELBOURNE, "layout": "plate", "pictures": "none", "song_line": False},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert (
        r["sono"]["src"].startswith("data:image/jpeg") and len(r["sono"]["song"]) >= 4
    )
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in r["sono"]["song"])
    assert "src" not in plate["sono"] and plate["sono"]["song"], (
        "the plate takes the line only"
    )
    assert off["sono"] is None


def test_names_in_another_language_come_from_inaturalist(tmp_path, offline):
    real = server._http.side_effect

    def with_names(url, token="", accept="application/json", **kw):
        if "inaturalist.org/v1/taxa?" in url and "locale=de" in url:
            return json.dumps(
                {"results": [{"id": 7, "preferred_common_name": "Amsel"}]}
            ).encode(), None
        return real(url, token, accept, **kw)

    opts = {**BERLIN, "pictures": "none", "names": "de"}
    with (
        _at(8, tz="Europe/Berlin"),
        patch.object(server, "_http", side_effect=with_names),
    ):
        first = server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
        _settle()
        while server._NAMING:
            time.sleep(0.02)
        second = server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
    assert any(s["name"] == "Eurasian Blackbird" for s in first["species"]), (
        "BirdNET's names until the lookup lands"
    )
    assert all(s["name"] == "Amsel" for s in second["species"]), (
        "the fixture names every bird Amsel"
    )
    assert second["species"][0]["name_birdnet"]


def test_plates_accepted_on_the_page_join_the_pack(tmp_path, offline):
    extra = {
        "Manorina melanocephala": {
            "file": "x.jpg",
            "box": [0, 0, 1, 1],
            "source": "test plate",
            "license": "Public domain",
        }
    }
    (tmp_path / "plates_extra.json").write_text(json.dumps(extra))
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "layout": "gallery", "pictures": "plate_photo"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    miner = next(s for s in r["species"] if s["sci"] == "Manorina melanocephala")
    assert miner["pic"]["kind"] == "plate" and "test plate" in miner["pic"]["credit"]
    assert "Manorina melanocephala" not in server._pack(), (
        "the overlay is per fetch, not global"
    )


def test_built_pages_are_not_listed_as_paintings(tmp_path, offline):
    opts = {
        **MELBOURNE,
        "layout": "cluster",
        "pictures": "plate_photo",
        "cluster_size": 3,
    }
    with _at(8):
        for _ in range(3):
            server.fetch(
                opts, {}, ctx={"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
            )
            _settle()
    assert list((tmp_path / "gen").glob("sheet_*.json")), "a page was built"
    assert server._gallery(tmp_path)["birds"] == []


def test_cluster_text_modes_and_footer_reach_the_client(tmp_path, offline):
    with _at(8):
        r = server.fetch(
            {
                **MELBOURNE,
                "layout": "cluster",
                "pictures": "plate_photo",
                "cluster_size": 4,
                "cluster_text": "under",
                "cluster_text_scale": "150",
                "cluster_labels": ["name", "count"],
                "footer": "hide",
            },
            {},
            ctx={"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480},
        )
    assert r["cluster_text"] == "under" and r["text_scale"] == 1.5
    assert r["footer"] == "hide"
    assert r["cluster"]["lines"] == ["name", "count"]
    assert r["cluster"]["label"] >= server.SHEET_LABEL
    # anything else is the defaults: everything shown, the look's own size
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "layout": "cluster", "cluster_text": "huge"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert r["cluster_text"] == "key" and r["text_scale"] == 1 and r["footer"] == "show"


def test_blend_remembers_where_each_bird_sat(tmp_path, offline):
    opts = {
        **MELBOURNE,
        "layout": "cluster",
        "cluster_style": "blend",
        "pictures": "gen",
        "cluster_size": 2,
        "cluster_text": "under",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    with _at(8):
        for _ in range(4):
            r = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
    c = r["cluster"]
    assert c["pic"]["kind"] == "generated" and len(c["birds"]) == 2
    # one box per bird, as fractions of the collage the blend was painted from
    assert len(c["boxes"]) == 2
    for x, y, w, h in c["boxes"]:
        assert 0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
    assert c["lines"] == ["name"]


def test_every_style_is_in_exactly_one_group():
    grouped = [k for _, ids in server.GEN_STYLE_GROUPS for k in ids]
    assert sorted(grouped) == sorted(server.GEN_STYLES)
    assert len(grouped) == len(set(grouped))
    manifest = json.loads((server.HERE / "plugin.json").read_text(encoding="utf-8"))
    opt = next(o for o in manifest["cell_options"] if o["name"] == "art_style")
    assert [c["value"] for c in opt["choices"]] == [*grouped, "custom"]
    groups = [c["group"] for c in opt["choices"]]
    order = [*(g for g, _ in server.GEN_STYLE_GROUPS), "Your own"]
    assert groups == sorted(groups, key=order.index)


def test_style_batch_paints_the_unpainted_birds_one_after_another(tmp_path, offline):
    folder = server._gen_dir(tmp_path)
    jobs = [
        {
            "key": server._pkey(server._slug(sci), "sumie"),
            "style": "sumie",
            "name": n,
            "sci": sci,
        }
        for n, sci in [
            ("Eurasian Blackbird", "Turdus merula"),
            ("Great Tit", "Parus major"),
        ]
    ]
    server._CAP.set(12)
    assert server._start_batch(jobs, tmp_path, "k")
    assert not server._start_batch(jobs, tmp_path, "k"), "one batch per style at a time"
    for _ in range(50):
        if not server._BATCH and not server._PAINTING:
            break
        time.sleep(0.1)
    assert len(offline.bodies) == 2
    assert all((folder / f"{j['key']}.jpg").exists() for j in jobs)


def test_custom_prompt_paints_with_its_own_words(tmp_path, offline):
    words = "a mosaic of pressed autumn leaves on linen, soft and warm"
    opts = {
        **MELBOURNE,
        "layout": "latest",
        "pictures": "gen",
        "art_style": "custom",
        "art_prompt": words,
    }
    ctx = {"data_dir": str(tmp_path)}
    with _at(8):
        server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
        _settle()
        r = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
    sent = json.loads(offline.bodies[0])
    assert words in sent["prompt"] and "reference photo" in sent["prompt"]
    tag = server._custom_tag(words)
    assert server._CUSTOM_RE.fullmatch(tag) and r["art_style"] == tag
    assert r["latest"]["pic"]["kind"] == "generated"
    assert "custom style" in r["latest"]["pic"]["credit"]
    # the words travel with the painting, so the admin page can repaint it
    (meta,) = [
        json.loads(f.read_text())
        for f in (tmp_path / "gen").glob("*.json")
        if f.stem.endswith(tag)
    ]
    assert meta["how"] == words
    (entry,) = [e for e in server._gallery(tmp_path)["birds"] if e["style"] == tag]
    assert entry["style_label"].startswith("Custom: a mosaic of pressed")
    assert server._job_for(entry)["how"] == words
    # another wording is another style with its own paintings; blank is the example
    assert server._custom_tag("something else") != tag
    assert (
        server._custom_tag(
            " A  Mosaic of pressed autumn leaves on linen, soft and warm "
        )
        == tag
    )
    with _at(8):
        r = server.fetch({**opts, "art_prompt": ""}, {"fal_api_key": "k"}, ctx=ctx)
    assert r["art_style"] == server._custom_tag(server.CUSTOM_EXAMPLE)


def test_painting_comes_with_its_cut_out_and_the_sheet_uses_it(tmp_path, offline):
    opts = {**MELBOURNE, "layout": "cluster", "pictures": "gen", "cluster_size": 2}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    with _at(8):
        for _ in range(3):
            server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
    cuts = sorted(p.name for p in (tmp_path / "gen").glob("*.cut.png"))
    assert len(cuts) == 2, "each painting was cut out by the model"
    assert any("birefnet" in u for u in offline)
    # the cut-out's alpha is the mask: the blob, not a guess at the paper
    key = cuts[0][: -len(".cut.png")]
    cut, mask, _paper = server._cutout_masked(
        (tmp_path / "gen" / f"{key}.jpg").read_bytes(),
        (tmp_path / "gen" / cuts[0]).read_bytes(),
    )
    assert (
        0.3 < (sum(1 for v in mask.getdata() if v) / (mask.width * mask.height)) < 0.95
    )


def test_mixed_poses_paint_every_other_bird_in_flight(tmp_path, offline):
    opts = {
        **MELBOURNE,
        "layout": "cluster",
        "pictures": "gen",
        "cluster_size": 2,
        "cluster_poses": "mixed",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    with _at(8):
        for _ in range(3):
            server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
    sent = [json.loads(b)["prompt"] for b in offline.bodies]
    assert len(sent) == 2
    assert sum("in flight with its wings spread" in p for p in sent) == 1
    assert sum("perched on a simple branch" in p for p in sent) == 1
    keys = sorted(p.stem for p in (tmp_path / "gen").glob("*.jpg"))
    assert any(k.endswith("--flight") for k in keys)
    g = server._gallery(tmp_path)
    assert any(
        e["pose"] == "flight" and "in flight" in e["style_label"] for e in g["birds"]
    )
    assert (
        server._pkey("turdus_merula", "gould", "flight")
        == "turdus_merula--gould--flight"
    )
    assert server._pkey("turdus_merula", "gould") == "turdus_merula"


def test_the_week_window_covers_seven_days(tmp_path, offline):
    with _at(9):
        day = server.fetch(
            {**MELBOURNE, "layout": "list"}, {}, ctx={"data_dir": str(tmp_path)}
        )
        week = server.fetch(
            {**MELBOURNE, "layout": "list", "window": "week"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert day["period"] == "to-day" and week["period"] == "this week"
    assert week["window"] == "week"
    assert week["detections_today"] > day["detections_today"] * 3
    # a live garden asks the summary endpoint for the seven days ending to-day
    calls = []
    with patch.object(
        server,
        "_json",
        side_effect=lambda url, *a, **k: (calls.append(url), ([], None))[1],
    ):
        server._from_birdnet("https://b.example", "", 70, "week", "2026-10-13")
    assert any(
        "species/summary?start_date=2026-10-07&end_date=2026-10-13" in u for u in calls
    )


def test_the_atlas_lists_every_bird_ever_heard(tmp_path, offline):
    with _at(9):
        server.fetch(
            {**MELBOURNE, "layout": "list"}, {}, ctx={"data_dir": str(tmp_path)}
        )
    # a bird heard a month ago, no longer in to-day's list
    log = json.loads((tmp_path / "rarity.json").read_text())
    log["days"].insert(0, "2026-09-10")
    log["seen"]["Strix aluco"] = ["2026-09-10", "2026-09-12"]
    log["names"]["Strix aluco"] = "Tawny Owl"
    (tmp_path / "rarity.json").write_text(json.dumps(log))
    with _at(9):
        r = server.fetch(
            {**MELBOURNE, "layout": "atlas", "pictures": "plate_photo"},
            {},
            ctx={"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480},
        )
    a = r["atlas"]
    assert a["since"] == "2026-09-10" and a["days"] >= 2
    owl = next(b for b in a["birds"] if b["name"] == "Tawny Owl")
    assert (
        owl["first"] == "2026-09-10"
        and owl["days"] == 2
        and owl["sci"] == "Strix aluco"
    )
    assert len(a["birds"]) == len(log["seen"])
    assert all("pic" in b for b in a["birds"])
    assert not any("fal.run" in u for u in offline), "the atlas never paints"


def test_a_place_without_a_zone_still_has_a_sky(tmp_path, offline):
    """A cell with coordinates but no timezone (#1): the clock falls back to
    the server's zone instead of a naive time the moon arithmetic rejects."""
    r = server.fetch(
        {"latitude": -37.81, "longitude": 144.96, "pictures": "none"},
        {},
        ctx={"data_dir": str(tmp_path)},
    )
    assert r["sky"]["moon_name"]
    assert server._moon(datetime(2026, 10, 11, 8, 14))[1]


def test_pending_paintings_are_counted_for_a_warm_ahead(tmp_path):
    import server

    r = server.fetch({}, {}, ctx={"data_dir": str(tmp_path), "preview": False})
    assert r["live_pending"] == 0 and not r.get("live_refresh_s"), (
        "the demo garden has nothing on the way"
    )
    with server._PAINTING_LOCK:
        server._PAINTING.add("x")
    try:
        r = server.fetch({}, {}, ctx={"data_dir": str(tmp_path), "preview": False})
        assert r["live_pending"] == 1 and r["live_refresh_s"] == 6
    finally:
        with server._PAINTING_LOCK:
            server._PAINTING.discard("x")


def _demo_sheet_birds(sizing: str = "true") -> list[dict]:
    """The demo garden's birds as _build_sheet hands them to a packer: a cut-out
    mask each (a body, a tail and a perch, its shape steady per name), the
    sizing option's factor, the label lines, biggest first."""
    import hashlib

    from PIL import ImageDraw

    with _at(8):
        now = server.datetime.now()
    species = server._demo(MELBOURNE, now)["species"]
    lengths = server._lengths()
    top = max(s["count"] for s in species) or 1
    birds = []
    for s in sorted(species, key=lambda s: -s["count"])[:12]:
        h = hashlib.sha1(s["name"].encode()).digest()
        w, ht = 260 + h[0], 300 + h[1]
        mask = Image.new("L", (w, ht), 0)
        d = ImageDraw.Draw(mask)
        d.ellipse((w * 0.2, ht * 0.15, w * 0.75, ht * 0.65), fill=255)
        d.polygon(
            [(w * 0.6, ht * 0.5), (w * 0.98, ht * 0.9), (w * 0.7, ht * 0.95)], 255
        )
        d.rectangle((0, ht * 0.7, w * (0.5 + h[2] / 512), ht * 0.76), fill=255)
        factor = {
            "true": server._size_factor(s["sci"], lengths),
            "busy": round(0.65 + (s["count"] / top) ** 0.325, 2),
            "same": 1.0,
        }[sizing]
        birds.append(
            {
                "name": s["name"],
                "sci": s["sci"],
                "mask": mask,
                "factor": factor,
                "rot": 0.0,
                "lines": [s["name"], s["sci"]],
            }
        )
    birds.sort(key=lambda b: -(b["factor"] ** 2) * b["mask"].width * b["mask"].height)
    return birds


@pytest.mark.parametrize("arrange", ["spiral", "voids"])
@pytest.mark.parametrize("size", [(1600, 960), (900, 1600)])
@pytest.mark.parametrize("sizing", ["true", "busy", "same"])
def test_fugleramme_arrangements_pack_without_overlap(arrange, size, sizing):
    import numpy as np

    W, H = size
    label = 0.017
    birds = _demo_sheet_birds(sizing)
    got = server._pack_fugleramme(birds, W, H, label, "sheet", arrange)
    assert got is not None, "the demo garden fits"
    spots, anchors = got
    assert len(spots) == len(anchors) == len(birds)
    page = np.zeros((H, W), dtype=np.int16)
    fpx = label * H
    for b, (x, y, scale), (ax, ay) in zip(birds, spots, anchors, strict=True):
        assert scale > 0, f"{b['name']} was left off"
        m = b["mask"]
        fit = m.resize(
            (max(1, int(m.width * scale)), max(1, int(m.height * scale))),
            Image.Resampling.BILINEAR,
        )
        # the whole cut-out, not eroded: no bird's outline may run into
        # another's, so ink never meets ink
        body = np.asarray(fit.point(lambda v: 255 if v > 127 else 0), dtype=bool)
        h, w = body.shape
        assert 0 <= x and 0 <= y and x + w <= W and y + h <= H, (
            "the bird is on the page"
        )
        page[y : y + h, x : x + w] += body
        # its label as the client sets it, from where the packer anchored it
        lw = (
            int(
                max(
                    len(t) * (0.74 if i == 0 else 0.5) for i, t in enumerate(b["lines"])
                )
                * fpx
            )
            + 4
        )
        lh = int(np.ceil(1.15 * len(b["lines"]) * fpx))
        lx0, ly0 = round(ax * W - lw / 2), round(ay * H + 0.15 * lh)
        assert 0 <= lx0 and 0 <= ly0 and lx0 + lw <= W + 1 and ly0 + lh <= H + 1, (
            "the label is on the page"
        )
        assert ly0 >= y, "the label is under its bird"
        page[max(0, ly0 + 1) : ly0 + lh - 1, max(0, lx0 + 1) : lx0 + lw - 1] += 1
    assert page.max() <= 1, "no bird or label overlaps another"
    # the cluster is centred on the page
    ys, xs = np.nonzero(page)
    assert abs((xs.min() + xs.max()) / 2 - W / 2) < W * 0.06
    assert abs((ys.min() + ys.max()) / 2 - H * 0.49) < H * 0.06


def test_fugleramme_spiral_puts_the_big_birds_in_the_middle():
    W, H = 1600, 960
    birds = _demo_sheet_birds("busy")
    spots, _anchors = server._pack_fugleramme(birds, W, H, 0.017, "sheet", "spiral")

    def off_centre(i):
        x, y, s = spots[i]
        m = birds[i]["mask"]
        return (
            abs(x + m.width * s / 2 - W / 2) / W + abs(y + m.height * s / 2 - H / 2) / H
        )

    assert off_centre(0) < sum(off_centre(i) for i in range(len(birds))) / len(birds)


def test_fugleramme_arrangements_build_a_sheet_and_key_the_cache(tmp_path, offline):
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    tags = {}
    for arrange in ("spiral", "voids"):
        opts = {
            **MELBOURNE,
            "layout": "cluster",
            "pictures": "gen",
            "cluster_size": 3,
            "cluster_arrange": arrange,
        }
        with _at(8):
            for _ in range(5):
                server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
                _settle()
            r = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
        c = r["cluster"]
        assert c["sheet"] and len(c["birds"]) == 3 and c["mode"] == "sheet"
        for b in c["birds"]:
            x, y = b["at"]
            assert 0 <= x <= 1 and 0 <= y <= 1
        made = {
            p.stem
            for p in (tmp_path / "gen").glob("sheet_*.json")
            if json.loads(p.read_text(encoding="utf-8")).get("arrange") == arrange
            and len(json.loads(p.read_text(encoding="utf-8"))["birds"]) == 3
        }
        assert len(made) == 1, "the three-bird sheet in this arrangement"
        tags[arrange] = made.pop()
    assert tags["spiral"] != tags["voids"]
    assert len(offline.bodies) == 3, "the second arrangement paints nothing new"
    assert server._choice("voids", server.ARRANGEMENTS) == "voids"


# ----------------------------------------------------------------------
# Fugleramme's windows, rankings and spotlight
# ----------------------------------------------------------------------


def _det(i, name, sci, at, conf=0.9):
    return {
        "id": i,
        "commonName": name,
        "scientificName": sci,
        "timestamp": at,
        "date": at[:10],
        "time": at[11:19],
        "confidence": conf,
    }


def test_a_window_under_a_day_is_counted_off_the_feed(tmp_path):
    feed = [
        _det(5, "Amsel", "Turdus merula", "2026-10-11T07:58:00+11:00"),
        _det(4, "Amsel", "Turdus merula", "2026-10-11T07:40:00+11:00"),
        _det(3, "Kohlmeise", "Parus major", "2026-10-11T07:30:00+11:00"),
        _det(2, "Kohlmeise", "Parus major", "2026-10-11T07:20:00+11:00", 0.4),
        _det(1, "Rotkehlchen", "Erithacus rubecula", "2026-10-11T06:50:00+11:00"),
    ]
    seen = []

    def fake_json(url, token="", **kw):  # noqa: ARG001
        seen.append(url)
        return (feed if "detections/recent" in url else []), None

    with _at(8), patch.object(server, "_json", side_effect=fake_json):
        r = server.fetch(
            {**MELBOURNE, "url": "b.local", "pictures": "none", "window": "1"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert any(f"detections/recent?limit={server.FEED_SCAN}" in u for u in seen)
    assert not any("species/" in u for u in seen), "the feed covers the hour"
    got = {s["name"]: s["count"] for s in r["species"]}
    # the robin is before the hour, the unsure great tit below the confidence
    assert got == {"Amsel": 2, "Kohlmeise": 1}
    amsel = next(s for s in r["species"] if s["name"] == "Amsel")
    assert amsel["hourly"][7] == 2 and (amsel["first"], amsel["last"]) == (
        "07:40",
        "07:58",
    )
    assert r["period"] == "in the last hour" and r["strips"] is True
    assert r["latest"]["name"] == "Amsel"


def test_a_feed_that_runs_out_falls_back_to_the_summary(tmp_path):
    # a thousand calls in the last few minutes: they do not reach 12 hours back
    feed = [
        _det(i, "Amsel", "Turdus merula", "2026-10-11T07:59:00+11:00")
        for i in range(server.FEED_SCAN)
    ]
    summary = [
        {
            "common_name": "Amsel",
            "scientific_name": "Turdus merula",
            "count": 1400,
            "last_heard": "2026-10-11T07:59:00+11:00",
        },
        {
            "common_name": "Waldkauz",
            "scientific_name": "Strix aluco",
            "count": 3,
            "last_heard": "2026-10-10T18:00:00+11:00",
        },
    ]
    seen = []

    def fake_json(url, token="", **kw):  # noqa: ARG001
        seen.append(url)
        return (feed if "detections/recent" in url else summary), None

    with _at(8), patch.object(server, "_json", side_effect=fake_json):
        r = server.fetch(
            {**MELBOURNE, "url": "b.local", "pictures": "none", "window": "12"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert any(
        "species/summary?start_date=2026-10-10&end_date=2026-10-11" in u for u in seen
    ), "the dates the window touches, rounded outward"
    # the owl's last call was before the cutoff (20:14 the day before)
    assert [s["name"] for s in r["species"]] == ["Amsel"]


@pytest.mark.parametrize(
    ("window", "query"),
    [
        ("72", "start_date=2026-10-08&end_date=2026-10-11"),
        ("720", "start_date=2026-09-11&end_date=2026-10-11"),
        ("week", "start_date=2026-10-05&end_date=2026-10-11"),
        ("0", "summary?min_confidence=70"),
    ],
)
def test_longer_windows_read_the_summary(tmp_path, window, query):
    summary = [
        {
            "common_name": "Amsel",
            "scientific_name": "Turdus merula",
            "count": 40,
            "last_heard": "2026-10-11T07:00:00+11:00",
        },
        {
            "common_name": "Star",
            "scientific_name": "Sturnus vulgaris",
            "count": 5,
            "last_heard": "2026-09-01T07:00:00+10:00",
        },
    ]
    seen = []

    def fake_json(url, token="", **kw):  # noqa: ARG001
        seen.append(url)
        return (summary if "summary" in url else []), None

    with _at(8), patch.object(server, "_json", side_effect=fake_json):
        r = server.fetch(
            {**MELBOURNE, "url": "b.local", "pictures": "none", "window": window},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert any(query in u for u in seen), seen
    names = [s["name"] for s in r["species"]]
    # a bird last heard before a rolling window began is not in it
    assert names == (["Amsel"] if window in ("72", "720") else ["Amsel", "Star"])
    assert r["strips"] is False, "no hours over days"
    # how long ago counts the days, not only the clock
    ages = {s["name"]: s["ago_min"] for s in r["species"]}
    assert ages["Amsel"] == 74
    if "Star" in ages:
        assert ages["Star"] > 40 * 1440
    assert r["period"] == server.PERIODS[window]


def test_only_the_birds_heard_to_day_are_logged_as_heard_to_day(tmp_path):
    summary = [
        {
            "common_name": "Amsel",
            "scientific_name": "Turdus merula",
            "count": 40,
            "last_heard": "2026-10-11T07:00:00+11:00",
        },
        {
            "common_name": "Star",
            "scientific_name": "Sturnus vulgaris",
            "count": 5,
            "last_heard": "2026-10-08T07:00:00+11:00",
        },
    ]
    with (
        _at(8),
        patch.object(
            server,
            "_json",
            side_effect=lambda url, *a, **k: (
                (summary if "summary" in url else []),
                None,
            ),
        ),
    ):
        server.fetch(
            {**MELBOURNE, "url": "b.local", "pictures": "none", "window": "week"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    log = json.loads((tmp_path / "rarity.json").read_text(encoding="utf-8"))
    assert log["seen"] == {"Turdus merula": ["2026-10-11"]}
    assert log["names"]["Sturnus vulgaris"] == "Star", "still named for the atlas"


def _birds(*rows):
    return [
        {
            "name": n,
            "sci": n.lower(),
            "count": c,
            "last_at": f"2026-10-11T{t}:00",
            "last": t,
        }
        for n, c, t in rows
    ]


def test_fugleramme_rankings_pick_the_birds_a_page_keeps():
    species = _birds(
        ("Amsel", 40, "07:00"),
        ("Star", 2, "07:50"),
        ("Meise", 9, "06:00"),
        ("Kauz", 2, "05:00"),
        ("Fink", 20, "07:30"),
    )
    ever = {"amsel": ["d"] * 30, "star": ["d"] * 12, "meise": ["d"], "kauz": ["d"] * 4}

    def pick(r, n=2, **k):
        return [s["name"] for s in server._ranked(species, r, n, ever, **k)]

    assert pick("heard") == ["Amsel", "Fink"]
    assert pick("rarest") == ["Kauz", "Star"], "fewest calls, ties on the name"
    assert pick("rarest_ever") == ["Meise", "Fink"], (
        "fewest days (an unlogged bird as one), then fewest calls"
    )
    assert pick("last_heard") == ["Star", "Fink"]
    # the spotlit bird takes a place ahead of the ranking
    assert pick("heard", lead="Kauz") == ["Kauz", "Amsel"]
    assert pick("heard", 9) == ["Amsel", "Fink", "Meise", "Kauz", "Star"]


def test_cluster_and_list_keep_the_ranked_birds(tmp_path, offline):
    with _at(9):
        busy = server.fetch(
            {**MELBOURNE, "layout": "cluster", "pictures": "photo", "cluster_size": 3},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        rare = server.fetch(
            {
                **MELBOURNE,
                "layout": "cluster",
                "pictures": "photo",
                "cluster_size": 3,
                "ranking": "rarest",
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        listed = server.fetch(
            {**MELBOURNE, "layout": "gallery", "pictures": "none", "count": 4},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        listed_rare = server.fetch(
            {
                **MELBOURNE,
                "layout": "gallery",
                "pictures": "none",
                "count": 4,
                "pick": "rarest",
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    counts = sorted((s["count"], s["name"]) for s in listed["species"])
    every = sorted(
        (s["count"], s["name"])
        for s in server._demo(MELBOURNE, server.datetime(2026, 10, 11, 9, 14))[
            "species"
        ]
    )
    assert sorted(b["count"] for b in busy["cluster"]["birds"]) == sorted(
        c for c, _n in every[-3:]
    )
    assert sorted(b["count"] for b in rare["cluster"]["birds"]) == sorted(
        c for c, _n in every[:3]
    )
    # as ordered: the four latest heard; rarest: the four fewest calls, still
    # shown latest first
    assert len(counts) == 4
    assert sorted(s["count"] for s in listed_rare["species"]) == [
        c for c, _n in every[:4]
    ]
    lasts = [s["last_at"] for s in listed_rare["species"]]
    assert lasts == sorted(lasts, reverse=True)


def test_which_bird_follows_the_window(tmp_path, offline):
    with _at(9):
        r = server.fetch(
            {
                **MELBOURNE,
                "layout": "plate",
                "pictures": "none",
                "hero": "busy",
                "window": "72",
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        q = server.fetch(
            {
                **MELBOURNE,
                "layout": "plate",
                "pictures": "none",
                "hero": "quiet",
                "window": "1",
            },
            {},
            ctx={"data_dir": str(tmp_path)},
        )
        n = server.fetch(
            {**MELBOURNE, "layout": "plate", "pictures": "none", "hero": "new"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert r["hero_kick"] == "Most calls in the last 3 days"
    assert r["latest"]["count"] == max(s["count"] for s in r["species"])
    assert q["hero_kick"] == "Fewest calls in the last hour"
    assert n["latest"]["new"] and n["hero_kick"] == "New to-day"


def test_newest_arrival_over_a_longer_window_comes_from_the_log():
    species = _birds(("Amsel", 40, "07:00"), ("Star", 2, "07:50"), ("Kauz", 1, "05:00"))
    ever = {
        "amsel": ["2026-09-01", "2026-10-11"],
        "star": ["2026-10-10", "2026-10-11"],
        "kauz": ["2026-10-11"],
    }
    now = datetime(2026, 10, 11, 8, 0, tzinfo=ZoneInfo("Australia/Melbourne"))
    got = server._arrival(species, ever, "2026-09-01", "week", now)
    assert got["name"] == "Kauz", "the bird the log first met latest"
    assert server._arrival(species, ever, "2026-09-01", "1", now)["name"] == "Kauz"
    # nothing arrives on the day the log began: every bird is first met then
    assert server._arrival(species, ever, "2026-10-11", "week", now) is None
    # BirdNET-Go's own flag wins over the log
    flagged = [{**species[0], "new": True}, *species[1:]]
    assert server._arrival(flagged, ever, "2026-09-01", "week", now)["name"] == "Amsel"


def _spot_check(birds, spots, W, H, label=0.017):
    """The spotlit bird's middle on the page's, larger than any other, and no
    bird on another (labels left to the label test above)."""
    import numpy as np

    page = np.zeros((H, W), dtype=np.int16)
    dims = []
    for b, (x, y, scale) in zip(birds, spots, strict=True):
        assert scale > 0, f"{b['name']} was left off"
        m = b["mask"]
        fit = m.resize((max(1, int(m.width * scale)), max(1, int(m.height * scale))))
        body = np.asarray(fit.point(lambda v: 255 if v > 127 else 0), dtype=bool)
        h, w = body.shape
        assert 0 <= x and 0 <= y and x + w <= W and y + h <= H
        page[y : y + h, x : x + w] += body
        dims.append(max(w, h))
        if b.get("spot"):
            ys, xs = np.nonzero(body)
            cx, cy = x + xs.mean(), y + ys.mean()
    assert page.max() <= 1, "no bird overlaps another"
    assert dims[0] > max(dims[1:]), "the spotlit bird is the largest"
    assert abs(cx - W / 2) < W * 0.04 and abs(cy - H / 2) < H * 0.06, "in the middle"


@pytest.mark.parametrize("arrange", ["spiral", "voids"])
@pytest.mark.parametrize("size", [(1600, 960), (900, 1500)])
def test_spotlight_is_pinned_in_the_middle_by_fugleramme(arrange, size):
    W, H = size
    birds = _demo_sheet_birds("true")[:8]
    small = min(range(len(birds)), key=lambda i: birds[i]["factor"])
    birds.insert(0, {**birds.pop(small), "spot": True})
    got = server._pack_fugleramme(birds, W, H, 0.017, "sheet", arrange)
    assert got is not None
    spots, anchors = got
    _spot_check(birds, spots, W, H)
    # its long side is Fugleramme's share of the page's short side
    m = birds[0]["mask"]
    long_side = max(m.size) * spots[0][2]
    inner = min(W * 0.92, H * 0.88)
    assert abs(long_side - server.SPOTLIGHT_SHARE * inner) < inner * 0.03
    # its label (half as large again) under it
    assert anchors[0][1] * H > spots[0][1]


@pytest.mark.parametrize("arrange", ["oval", "rows", "ring", "heap"])
def test_spotlight_is_pinned_in_the_middle_on_the_own_arrangements(arrange):
    W, H = 1600, 960
    birds = _demo_sheet_birds("true")[:8]
    birds.insert(0, {**birds.pop(5), "spot": True})
    spots = server._pack_sheet(birds, W, H, 0.017, "sheet", arrange)
    _spot_check(birds, spots, W, H)


def test_spotlight_reaches_the_sheet_and_the_client(tmp_path, offline):
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    opts = {
        **MELBOURNE,
        "layout": "cluster",
        "pictures": "gen",
        "cluster_size": 3,
        "cluster_arrange": "spiral",
        "spotlight": True,
        "ranking": "heard",
    }
    with _at(8):
        latest = max(
            server._demo(MELBOURNE, server.datetime.now())["species"],
            key=lambda s: s["last"],
        )["name"]
        for _ in range(5):
            server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
            _settle()
        r = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
    c = r["cluster"]
    spot = [b for b in c["birds"] if b.get("spot")]
    assert [b["name"] for b in spot] == [latest], "the latest bird, ranked or not"
    made = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (tmp_path / "gen").glob("sheet_*.json")
    ]
    assert any(m.get("spot") for m in made)
    if shutil.which("node"):
        script = (
            f"import('file://{ROOT}/client.js').then(m => {{ const s = {{innerHTML: ''}};"
            f" m.default(s, {{data: {json.dumps(r)}, cell: {{size: 'md'}}}});"
            " if (!s.innerHTML.includes('sh-l sh-spot')) throw new Error('no spot');"
            " console.log('ok'); })"
        )
        out = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert out.returncode == 0, out.stderr
    # the wreath keeps its middle for the title: no spotlight there
    with _at(8):
        w = server.fetch(
            {**opts, "cluster_style": "wreath"}, {"fal_api_key": "k"}, ctx=ctx
        )
    assert not any(b.get("spot") for b in (w["cluster"] or {}).get("birds", []))


def _visible_always(manifest):
    return [
        o["name"]
        for o in manifest["cell_options"]
        if not o.get("show_if") and not o.get("advanced")
    ]


def test_options_panel_follows_the_rulebook():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    opts = manifest["cell_options"]
    by = {o["name"]: o for o in opts}
    assert len(by) == len(opts), "option names are unique"
    assert len(_visible_always(manifest)) <= 5
    assert _visible_always(manifest)[:2] == ["url", "layout"]
    # every show_if names an option above it, and values it has
    for o in opts:
        for key, want in (o.get("show_if") or {}).items():
            assert key in by, f"{o['name']} depends on {key}, which is not an option"
            assert key != o["name"]
            ref = by[key]
            vals = {c["value"] for c in ref.get("choices", [])}
            raw = want["not"] if isinstance(want, dict) else want
            for v in raw if isinstance(raw, list) else [raw]:
                if ref["type"] == "boolean":
                    assert isinstance(v, bool)
                elif vals:
                    assert v in vals, f"{o['name']}: {key}={v!r} is not a choice"
    # the advanced ones come last
    flags = [bool(o.get("advanced")) for o in opts]
    assert flags == sorted(flags), "More options at the end"
    # labels: no look prefixes, no em dashes, the shared words
    for o in opts:
        assert not o["label"].startswith(("Cluster:", "Full bleed:")), o["label"]
        text = json.dumps(o, ensure_ascii=False)
        assert chr(0x2014) not in text, o["name"]
    assert (
        by["font"]["label"] == "Typeface"
        and by["footer"]["label"] == "Foot of the page"
    )
    assert by["location"]["label"] == "Place"
    for name in ("token", "font", "min_confidence", "refresh_min", "sort", "footer"):
        assert by[name].get("advanced"), name


def test_saved_cells_keep_working_against_v0_15_1():
    old = json.loads(
        (ROOT / "tests" / "manifest_v0_15_1.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    by = {o["name"]: o for o in manifest["cell_options"]}
    for name, was in old.items():
        assert name in by, f"{name} was removed"
        now = by[name]
        assert now["type"] == was["type"] and now.get("default") == was["default"], name
        values = [c["value"] for c in now.get("choices", [])]
        assert set(was["values"]) <= set(values), f"{name} lost a choice"
    # the new options default to what the page did before
    assert by["ranking"]["default"] == "heard"
    assert by["pick"]["default"] == "order"
    assert by["spotlight"]["default"] is False
    assert [c["value"] for c in by["window"]["choices"]] == [
        "0.25",
        "0.5",
        "1",
        "3",
        "6",
        "12",
        "today",
        "72",
        "week",
        "720",
        "0",
    ]
    assert set(server.WINDOWS) == {c["value"] for c in by["window"]["choices"]}
    assert set(server.HEROES) == {c["value"] for c in by["hero"]["choices"]}
    assert set(server.RANKINGS) == {c["value"] for c in by["ranking"]["choices"]}
    assert set(server.PICKS) == {c["value"] for c in by["pick"]["choices"]}
