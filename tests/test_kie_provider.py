"""Backyard Birds uses the host's selected service without changing its cache."""

import importlib.util
import json
from pathlib import Path

import pytest

from app import artwork, fal, kie_images

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("birds_kie_server", ROOT / "server.py")
assert spec is not None and spec.loader is not None
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


@pytest.fixture
def api(tmp_path, monkeypatch):
    calls = []
    state = {"timeout": True}
    monkeypatch.setattr(kie_images, "configuration", lambda: ("kie", "key"))
    monkeypatch.setattr(kie_images, "POLL_S", 0)
    monkeypatch.setattr(server, "GEN_TIMEOUT_S", 0.01)
    monkeypatch.setattr(
        server, "_inat_ref", lambda *a: ("https://ref.example/bird.jpg", "Photographer")
    )
    monkeypatch.setattr(server, "_cut_out", lambda *a: None)

    def request(url, body=None, key="", timeout=60):
        calls.append((url, body))
        if url.endswith("credit"):
            return {"data": 100}
        if url.endswith("createTask"):
            return {"data": {"taskId": "paid-bird"}}
        if state["timeout"]:
            raise kie_images.KieError("temporary poll failure")
        return {
            "data": {
                "state": "success",
                "resultJson": json.dumps(
                    {"resultUrls": ["https://cdn.example/bird.png"]}
                ),
            }
        }

    monkeypatch.setattr(kie_images, "_json", request)
    monkeypatch.setattr(kie_images, "_download", lambda url: b"bird-picture")
    return calls, state


def test_bird_timeout_resumes_paid_task_and_counts_once(tmp_path, monkeypatch, api):
    calls, state = api
    ledger = __import__("app.genai", fromlist=["Ledger"]).Ledger(tmp_path / "ledger")
    artwork.host_configure(ledger=ledger, ceilings=lambda: (2, 2))
    job = {
        "key": "robin",
        "name": "Robin",
        "sci": "Erithacus rubecula",
        "style": "gould",
    }
    try:
        assert server._painting_key({}) == fal.DIRECT_ONLY
        # Failed collection keeps the paid task and the local count.
        why = server._paint(job, tmp_path, fal.DIRECT_ONLY)
        assert "paid task" in why
        assert server._budget_used(tmp_path / "gen") == 1
        state["timeout"] = False
        assert server._paint(job, tmp_path, fal.DIRECT_ONLY) is None
        assert (tmp_path / "gen" / "robin.jpg").read_bytes() == b"bird-picture"
        assert server._budget_used(tmp_path / "gen") == 1
        assert ledger.today_cents() == 2
        assert len([u for u, _ in calls if u.endswith("createTask")]) == 1
    finally:
        artwork.host_configure()


def test_failed_local_save_keeps_paid_task_for_retry(tmp_path, api, monkeypatch):
    calls, state = api
    state["timeout"] = False
    job = {
        "key": "robin",
        "name": "Robin",
        "sci": "Erithacus rubecula",
        "style": "gould",
    }
    write = server._write

    def full_disk(path, data):
        if path.suffix != ".jpg":
            write(path, data)

    monkeypatch.setattr(server, "_write", full_disk)
    assert "could not be saved" in server._paint(job, tmp_path, fal.DIRECT_ONLY)
    assert (tmp_path / "gen" / "kie_tasks" / "robin" / "0.json").exists()
    monkeypatch.setattr(server, "_write", write)
    assert server._paint(job, tmp_path, fal.DIRECT_ONLY) is None
    assert len([u for u, _ in calls if u.endswith("createTask")]) == 1
