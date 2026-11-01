"""Bird stickers as a picture choice and as the painter's reference."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def _server():
    name = "bb_server_stickers_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _sticker_bytes() -> bytes:
    im = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    for x in range(100, 300):
        for y in range(80, 220):
            im.putpixel((x, y), (200, 60, 40, 255))
    out = io.BytesIO()
    im.save(out, "WEBP", lossless=True)
    return out.getvalue()


def test_index_and_lookup():
    st = _server()._stickers()
    assert st.species_count() == 11064
    hit = st.lookup("Gymnorhina tibicen")
    assert hit and hit["family"] == "Passeriformes/Artamidae"
    assert st.lookup("Not abird") is None


def test_sticker_picture_has_the_plate_shape(tmp_path, monkeypatch):
    server = _server()
    monkeypatch.setattr(server, "_cached_get", lambda data_dir, url, token="": _sticker_bytes())
    pic = server._sticker("Gymnorhina tibicen", 300, tmp_path)
    assert pic and pic["kind"] == "plate" and "huashu-bird-sticker" in pic["credit"]
    assert pic["src"].startswith("data:image/jpeg;base64,") and pic["paper"].startswith("#")
    assert server._sticker("Not abird", 300, tmp_path) is None


def test_attach_pictures_takes_stickers_before_photos(tmp_path, monkeypatch):
    server = _server()
    monkeypatch.setattr(server, "_cached_get", lambda data_dir, url, token="": _sticker_bytes())
    photos: list[str] = []
    monkeypatch.setattr(server, "_photo", lambda row, *a, **k: photos.append(row["sci"]) or None)
    rows = [
        {"name": "Australian Magpie", "sci": "Gymnorhina tibicen"},
        {"name": "Nothing", "sci": "Not abird"},
    ]
    server._STICKERS.set("sticker")
    try:
        server._attach_pictures(rows, "sticker", 300, "", "", "", tmp_path)
    finally:
        server._STICKERS.set("")
    assert rows[0]["pic"] and "huashu" in rows[0]["pic"]["credit"]
    assert rows[1]["pic"] is None and photos == ["Not abird"], (
        "only the bird with no sticker asks for a photo"
    )


def test_manifest_offers_the_sticker_modes():
    m = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    by = {o["name"]: o for o in m["cell_options"]}
    values = [c["value"] for c in by["pictures"]["choices"]]
    assert values[:3] == ["plate_photo", "plate_sticker", "sticker"]
    assert by["art_reference"]["show_if"] == {"pictures": ["plate_gen", "gen"]}
    assert by["art_reference"]["advanced"] is True and by["art_reference"]["default"] == "photo"
