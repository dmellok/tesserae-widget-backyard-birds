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


@pytest.mark.parametrize("ceiling", [(0, 100), (100, 0)])
def test_automatic_route_obeys_shared_ceiling(tmp_path, api, monkeypatch, ceiling):
    from app.genai import Ledger

    calls, _ = api
    monkeypatch.setattr(kie_images, "configuration", lambda: ("auto", ""))
    paid = []
    monkeypatch.setattr(server, "_json", lambda *a, **k: (paid.append(1) or {}, "failed"))
    monkeypatch.setattr(fal, "_http_json", lambda *a, **k: paid.append(1) or {})
    artwork.host_configure(ledger=Ledger(tmp_path / "ledger"), ceilings=lambda: ceiling)
    try:
        why = server._paint({"key": "robin", "name": "Robin", "sci": "Robin", "style": "gould"}, tmp_path, "fal-key")
        assert "ceiling" in why
        assert paid == []
        assert not [u for u, _ in calls if u.endswith("createTask")]
        assert server._budget_used(tmp_path / "gen") == 0
    finally:
        artwork.host_configure()


def test_automatic_timeout_and_two_callers_never_resubmit(tmp_path, api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.genai import Ledger

    monkeypatch.setattr(kie_images, "configuration", lambda: ("auto", ""))
    monkeypatch.setattr(fal.FalClient, "direct_model", lambda self, model: None)
    paid = []

    def request(*args, **kwargs):
        paid.append(1)
        raise TimeoutError("reply lost after payment")

    monkeypatch.setattr(server, "_json", request)
    ledger = Ledger(tmp_path / "ledger")
    artwork.host_configure(ledger=ledger, ceilings=lambda: (4, 4))
    job = {"key": "robin", "name": "Robin", "sci": "Robin", "style": "gould"}
    try:
        with ThreadPoolExecutor(2) as pool:
            errors = list(pool.map(lambda _: server._paint(job, tmp_path, "fal-key"), range(2)))
        assert all(errors)
        assert server._paint(job, tmp_path, "fal-key")
        assert paid == [1]
        assert ledger.today_cents() == 4
        assert server._budget_used(tmp_path / "gen") == 1
    finally:
        artwork.host_configure()


def test_cutout_is_charged_and_not_repeated(tmp_path, api, monkeypatch):
    from app.genai import Ledger

    _, state = api
    state["timeout"] = False
    monkeypatch.setattr(fal, "resolve_key", lambda *a: "fal-key")
    paid = []
    monkeypatch.setattr(server, "_cut_out", lambda *a: paid.append(1) or b"cutout")
    ledger = Ledger(tmp_path / "ledger")
    artwork.host_configure(ledger=ledger, ceilings=lambda: (4, 4))
    job = {"key": "robin", "name": "Robin", "sci": "Robin", "style": "gould"}
    try:
        assert server._paint(job, tmp_path, fal.DIRECT_ONLY) is None
        assert server._paint(job, tmp_path, fal.DIRECT_ONLY) is None
        assert paid == [1]
        assert ledger.today_cents() == 4  # one image (2) plus one cutout (2)
        assert server._budget_used(tmp_path / "gen") == 1
    finally:
        artwork.host_configure()


JOB = {"key": "robin", "name": "Robin", "sci": "Erithacus rubecula", "style": "gould"}


@pytest.mark.parametrize("answer", ["HTTP 401", "HTTP 403", "HTTP 422: bad prompt", "HTTP 429"])
def test_refused_fal_request_does_not_hold_the_bird_back(tmp_path, api, monkeypatch, answer):
    """A request fal.ai turned down bought nothing: no marker is left, the
    day's count and the ledger are handed back, and Repaint (or the next
    refresh) works at once."""
    import time

    from app.genai import Ledger

    monkeypatch.setattr(kie_images, "configuration", lambda: ("auto", ""))
    ledger = Ledger(tmp_path / "ledger")
    artwork.host_configure(ledger=ledger, ceilings=lambda: (100, 100))
    try:
        monkeypatch.setattr(server, "_json", lambda *a, **k: (None, answer))
        why = server._paint(dict(JOB), tmp_path, "bad-key")
        assert why and "fal.ai" in why
        assert not (tmp_path / "gen" / "kie_tasks" / "robin" / "submitted").exists()
        assert server._budget_used(tmp_path / "gen") == 0
        assert ledger.today_cents() == 0
        monkeypatch.setattr(
            server, "_json", lambda *a, **k: ({"images": [{"url": "https://x/y.jpg"}]}, None)
        )
        monkeypatch.setattr(server, "_http", lambda *a, **k: (b"picture", None))
        assert server._paint({**JOB, "repaint_at": time.time()}, tmp_path, "good-key") is None
        assert (tmp_path / "gen" / "robin.jpg").read_bytes() == b"picture"
        assert server._budget_used(tmp_path / "gen") == 1
    finally:
        artwork.host_configure()


@pytest.mark.parametrize(
    "answer", ["unreachable", "HTTP 500", "HTTP 502", "HTTP 408", "not json"]
)
def test_ambiguous_fal_failure_still_holds_the_bird_back(tmp_path, api, monkeypatch, answer):
    """A lost or 5xx answer may have been paid for: the marker stays and the
    request is not sent again."""
    from app.genai import Ledger

    monkeypatch.setattr(kie_images, "configuration", lambda: ("auto", ""))
    sent = []
    monkeypatch.setattr(server, "_json", lambda *a, **k: (sent.append(1), (None, answer))[1])
    ledger = Ledger(tmp_path / "ledger")
    artwork.host_configure(ledger=ledger, ceilings=lambda: (100, 100))
    try:
        assert server._paint(dict(JOB), tmp_path, "key")
        assert (tmp_path / "gen" / "kie_tasks" / "robin" / "submitted").exists()
        assert "may have completed" in server._paint(dict(JOB), tmp_path, "key")
        assert sent == [1]
        assert server._budget_used(tmp_path / "gen") == 1
    finally:
        artwork.host_configure()


def test_picture_that_cannot_be_fetched_counts_as_bought(tmp_path, api, monkeypatch):
    """fal.ai answered and only the download failed, even with a 404."""
    monkeypatch.setattr(kie_images, "configuration", lambda: ("auto", ""))
    monkeypatch.setattr(
        server, "_json", lambda *a, **k: ({"images": [{"url": "https://x/y.jpg"}]}, None)
    )
    monkeypatch.setattr(server, "_http", lambda *a, **k: (None, "HTTP 404"))
    assert "no picture" in server._paint(dict(JOB), tmp_path, "key")
    assert (tmp_path / "gen" / "kie_tasks" / "robin" / "submitted").exists()


@pytest.mark.parametrize(
    ("text", "bought_nothing"),
    [
        ("fal.ai refused the key or the account has no balance (HTTP 401)", True),
        ("fal.ai refused the request (HTTP 422: bad)", True),
        ("no fal.ai key", True),
        ("no reference picture to paint from", True),
        ("fal.ai answered HTTP 503", False),
        ("fal.ai answered HTTP 408", False),
        ("fal.ai took too long to answer", False),
        ("fal.ai could not be reached (timed out)", False),
        ("the picture could not be fetched (fal.ai answered HTTP 404)", False),
        ("fal.ai sent no picture", False),
    ],
)
def test_what_the_hosts_painter_says_is_read_for_whether_it_was_bought(text, bought_nothing):
    assert server._nothing_bought(fal.FalError(text)) is bought_nothing


def test_refused_shared_fal_request_does_not_hold_the_bird_back(tmp_path, api, monkeypatch):
    """The same through the host's painter (a service other than Automatic)."""
    import urllib.error

    monkeypatch.setattr(kie_images, "configuration", lambda: ("fal", ""))
    monkeypatch.setattr(fal.FalClient, "direct_model", lambda self, model: None)

    def refuse(url, body, key, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(fal, "_http_json", refuse)
    why = server._paint(dict(JOB), tmp_path, "bad-key")
    assert "HTTP 401" in why
    assert not (tmp_path / "gen" / "kie_tasks" / "robin" / "submitted").exists()


def test_refused_kie_submission_hands_its_charge_back(tmp_path, monkeypatch, api):
    from app.genai import Ledger

    def refuse(url, body=None, key="", timeout=60):
        if url.endswith("credit"):
            return {"data": 100}
        raise kie_images.KieRefused("kie.ai refused the key")

    monkeypatch.setattr(kie_images, "_json", refuse)
    ledger = Ledger(tmp_path / "ledger")
    artwork.host_configure(ledger=ledger, ceilings=lambda: (100, 100))
    try:
        assert "refused" in server._paint(dict(JOB), tmp_path, fal.DIRECT_ONLY)
        assert server._budget_used(tmp_path / "gen") == 0
        assert ledger.today_cents() == 0
    finally:
        artwork.host_configure()


def test_the_widget_loads_without_fcntl(monkeypatch):
    """Windows has no fcntl: nothing at import time may need it."""
    import sys

    monkeypatch.setitem(sys.modules, "fcntl", None)
    again = importlib.util.spec_from_file_location("birds_no_fcntl", ROOT / "server.py")
    assert again is not None and again.loader is not None
    module = importlib.util.module_from_spec(again)
    again.loader.exec_module(module)
    assert "import fcntl" not in "\n".join(
        line for line in (ROOT / "server.py").read_text().splitlines() if not line.startswith(" ")
    )


def test_repaint_clears_kie_tasks_with_nothing_to_collect(tmp_path, api, monkeypatch):
    import time

    _, state = api
    state["timeout"] = False
    tasks = tmp_path / "gen" / "kie_tasks" / "robin"
    tasks.mkdir(parents=True)
    (tasks / "0.json").write_text(json.dumps({"prompt": "an older prompt"}))
    cleared = []
    real = kie_images.clear_unfinished
    monkeypatch.setattr(
        kie_images, "clear_unfinished", lambda folder: cleared.append(folder) or real(folder)
    )
    assert server._paint({**JOB, "repaint_at": time.time()}, tmp_path, fal.DIRECT_ONLY) is None
    assert cleared == [tasks]


def test_failed_cutout_is_not_kept_as_a_result(tmp_path, api, monkeypatch):
    _, state = api
    state["timeout"] = False
    monkeypatch.setattr(fal, "resolve_key", lambda *a: "fal-key")
    monkeypatch.setattr(server, "_cut_out", lambda *a: None)
    assert server._paint(dict(JOB), tmp_path, fal.DIRECT_ONLY) is None
    cut = tmp_path / "gen" / "kie_tasks" / "robin" / "cutout"
    assert not (cut / "result.img").exists()
    assert not (tmp_path / "gen" / "robin.cut.png").exists()
