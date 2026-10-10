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


def _jpeg(w: int = 300, h: int = 400) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 40)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def offline():
    """No network: plates, photos and fal all answer from here."""
    calls: list[str] = []

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        calls.append(url)
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
    with _at(8):
        for _ in range(3):
            r = server.fetch(
                {**MELBOURNE, "layout": "bleed", "pictures": "gen"},
                {"fal_api_key": "k"},
                ctx={"data_dir": str(tmp_path)},
            )
    assert r["latest"]["pic"]["kind"] == "generated"
    assert "AI painting" in r["latest"]["pic"]["credit"]
    assert sum("fal.run" in u for u in offline) == 1, "painted once, then kept"


def test_generated_one_new_painting_per_fetch(tmp_path, offline):
    with _at(8):
        server.fetch(
            {**MELBOURNE, "layout": "gallery", "pictures": "gen", "count": 6},
            {"fal_api_key": "k"},
            ctx={"data_dir": str(tmp_path)},
        )
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
    [(lay, "band") for lay in ("list", "latest", "gallery", "chart", "plate")]
    + [("bleed", cap) for cap in ("band", "editorial", "notebook", "tag", "poster")],
)
def test_client_renders_every_layout(tmp_path, layout, caption):
    with _at(8):
        data = server.fetch(
            {**MELBOURNE, "layout": layout, "caption": caption, "pictures": "none"},
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
