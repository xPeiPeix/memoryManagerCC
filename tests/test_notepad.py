from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from mmcc.notepad import (
    _ThreadedServer,
    _find_free_port,
    _safe_resolve,
    _projects_payload,
    _entry_payload,
    make_handler,
)
from mmcc.store import MemoryStore


@pytest.fixture
def server(mock_projects):
    store = MemoryStore(mock_projects)
    handler_cls = make_handler(store, store.projects_dir)
    httpd = _ThreadedServer(("localhost", 0), handler_cls)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield store, port
    httpd.shutdown()
    httpd.server_close()
    t.join(timeout=2)


def test_find_free_port_returns_int():
    port = _find_free_port()
    assert isinstance(port, int)
    assert 1024 <= port <= 65535


def test_safe_resolve_within_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    inner = root / "file.md"
    inner.write_text("x", encoding="utf-8")
    assert _safe_resolve(str(inner), root) == inner.resolve()


def test_safe_resolve_rejects_outside(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    assert _safe_resolve(str(outside), root) is None


def test_projects_payload_shape(mock_projects):
    store = MemoryStore(mock_projects)
    payload = _projects_payload(store)
    assert "projects" in payload
    proj_ids = [p["project_id"] for p in payload["projects"]]
    assert "D--test-normal" in proj_ids
    normal = next(p for p in payload["projects"] if p["project_id"] == "D--test-normal")
    fnames = [e["filename"] for e in normal["entries"]]
    assert "feedback_first.md" in fnames
    for entry in normal["entries"]:
        for k in ("filename", "name", "description", "type", "mtime", "file_path"):
            assert k in entry


def test_entry_payload_full_content(mock_projects):
    store = MemoryStore(mock_projects)
    target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
    payload = _entry_payload(store, target)
    assert payload is not None
    assert payload["name"] == "First feedback"
    assert payload["type"] == "feedback"
    assert "Why" in payload["body"]
    assert payload["origin_session_id"] == "abc-123"


def test_entry_payload_returns_none_for_unparseable(mock_projects):
    store = MemoryStore(mock_projects)
    broken = mock_projects / "D--test-broken" / "memory" / "feedback_broken.md"
    assert _entry_payload(store, broken) is None


def test_api_projects_endpoint(server):
    _, port = server
    with urlopen(f"http://localhost:{port}/api/projects") as r:
        assert r.status == 200
        ctype = r.headers.get("Content-Type", "")
        assert "application/json" in ctype
        data = json.loads(r.read())
    assert "projects" in data
    proj_ids = [p["project_id"] for p in data["projects"]]
    assert "D--test-normal" in proj_ids


def test_api_entry_endpoint_returns_full_content(server, mock_projects):
    _, port = server
    target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
    url = f"http://localhost:{port}/api/entry?path={target}"
    with urlopen(url) as r:
        data = json.loads(r.read())
    assert data["name"] == "First feedback"
    assert data["type"] == "feedback"
    assert "Why" in data["body"]


def test_api_entry_rejects_path_traversal(server, mock_projects, tmp_path):
    _, port = server
    outside = tmp_path / "secret.md"
    outside.write_text("---\nname: x\ntype: feedback\n---\nbody\n", encoding="utf-8")
    url = f"http://localhost:{port}/api/entry?path={outside}"
    with pytest.raises(HTTPError) as exc_info:
        urlopen(url)
    assert exc_info.value.code == 403


def test_api_entry_missing_path_returns_400(server):
    _, port = server
    with pytest.raises(HTTPError) as exc_info:
        urlopen(f"http://localhost:{port}/api/entry")
    assert exc_info.value.code == 400


def test_api_entry_unparseable_returns_404(server, mock_projects):
    _, port = server
    broken = mock_projects / "D--test-broken" / "memory" / "feedback_broken.md"
    url = f"http://localhost:{port}/api/entry?path={broken}"
    with pytest.raises(HTTPError) as exc_info:
        urlopen(url)
    assert exc_info.value.code == 404


def test_index_html_endpoint(server):
    _, port = server
    with urlopen(f"http://localhost:{port}/") as r:
        assert r.status == 200
        ctype = r.headers.get("Content-Type", "")
        assert "text/html" in ctype
        body = r.read().decode("utf-8")
    assert "<title>mmcc notepad</title>" in body
    assert "marked" in body
    assert "/api/projects" in body


def test_404_for_unknown_path(server):
    _, port = server
    with pytest.raises(HTTPError) as exc_info:
        urlopen(f"http://localhost:{port}/nope")
    assert exc_info.value.code == 404
