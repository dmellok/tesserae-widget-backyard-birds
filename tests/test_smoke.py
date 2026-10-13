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


@pytest.fixture
def offline():
    """No network: plates, photos and fal all answer from here."""

    class Calls(list):
        bodies: list[bytes]

    calls = Calls()
    calls.bodies = []

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        calls.append(url)
        if "fal.run" in url and kw.get("body"):
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
        if "fal.run" in url:
            return json.dumps(
                {"images": [{"url": "https://fal.example/out.jpg"}]}
            ).encode(), None
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
            {**MELBOURNE, "layout": "list", "count": 12},
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
            {**MELBOURNE, "layout": "list", "pictures": "photo"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    raw = base64.b64decode(r["species"][0]["pic"]["src"].split(",", 1)[1])
    assert max(Image.open(io.BytesIO(raw)).size) <= server.SIZE["list"]


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
    assert sum("fal.run" in u for u in offline) == 1, "painted once, then kept"


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
    assert sum("fal.run" in u for u in offline) == 1


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
    assert lst["font_css"] == ""


def test_panel_levels_convert_pictures_and_keep_separate_caches(tmp_path, offline):
    # A picture asked for by a greyscale or black-and-white panel comes back
    # converted, from its own cache file, and the colour one is untouched.
    pics = {}
    for levels in ("colour", "gray16", "gray4", "mono"):
        with _at(8):
            r = server.fetch(
                {**MELBOURNE, "layout": "list", "pictures": "photo", "panel": levels},
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


def test_every_art_style_makes_a_key_and_a_prompt():
    manifest = json.loads((ROOT / "plugin.json").read_text())
    offered = next(o for o in manifest["cell_options"] if o["name"] == "art_style")
    # offered in groups (test_every_style_is_in_exactly_one_group checks the order)
    assert sorted(c["value"] for c in offered["choices"]) == sorted(server.GEN_STYLES)
    for style, (label, how) in server.GEN_STYLES.items():
        key = server._pkey("turdus_merula", style)
        assert server._SLUG_RE.fullmatch(key), key
        assert label and how
        prompt = server.GEN_ONE.format(
            name="Eurasian Blackbird", sci="Turdus merula", style=how
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
    assert lst["font_css"] == "", "theme layouts carry no bundled faces"


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
    assert r["footer"] is False
    assert r["cluster"]["lines"] == ["name", "count"]
    assert r["cluster"]["label"] >= server.SHEET_LABEL
    # anything else is the defaults: everything shown, the look's own size
    with _at(8):
        r = server.fetch(
            {**MELBOURNE, "layout": "cluster", "cluster_text": "huge"},
            {},
            ctx={"data_dir": str(tmp_path)},
        )
    assert r["cluster_text"] == "key" and r["text_scale"] == 1 and r["footer"]


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
    assert [c["value"] for c in opt["choices"]] == grouped
    groups = [c["group"] for c in opt["choices"]]
    assert groups == sorted(groups, key=[g for g, _ in server.GEN_STYLE_GROUPS].index)


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
