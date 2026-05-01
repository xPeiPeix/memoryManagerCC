from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from mmcc.notepad import (
    _ThreadedServer,
    _find_free_port,
    _port_in_use,
    _resolve_listen_port,
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


@pytest.fixture
def busy_port():
    """Yield (host, port) with an active LISTEN socket — simulates 'another
    project already serving on this port'. Used to verify mmcc detects the
    occupation instead of bind-overlapping silently (Windows quirk).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("localhost", 0))
    s.listen(128)
    try:
        yield "localhost", s.getsockname()[1]
    finally:
        s.close()


def test_port_in_use_detects_active_listener(busy_port):
    host, port = busy_port
    assert _port_in_use(host, port) is True


def test_port_in_use_returns_false_for_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("localhost", 0))
    free_port = s.getsockname()[1]
    s.close()
    assert _port_in_use("localhost", free_port) is False


def test_find_free_port_skips_listening_port(busy_port):
    host, port = busy_port
    result = _find_free_port(host, preferred=port)
    assert result != port
    assert isinstance(result, int)


def test_resolve_listen_port_auto_when_none():
    port = _resolve_listen_port("localhost", None)
    assert isinstance(port, int)
    assert 1024 <= port <= 65535


def test_resolve_listen_port_passes_through_if_free():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("localhost", 0))
    free_port = s.getsockname()[1]
    s.close()
    assert _resolve_listen_port("localhost", free_port) == free_port


def test_resolve_listen_port_raises_if_explicit_port_in_use(busy_port):
    host, port = busy_port
    with pytest.raises(OSError):
        _resolve_listen_port(host, port)


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


def test_spa_disables_marked_raw_html(server):
    """SPA must configure marked to strip raw HTML tokens.

    Memory bodies may legitimately contain raw HTML (e.g. a feedback note
    discussing XSS attack vectors that pastes a `<script>` example).
    Without this guard the example would actually execute when the user
    opens the entry — a real UX hazard, not just a theoretical attack.

    This test enforces the SPA includes a marked.use(...) renderer config
    that neutralizes the html token. We do not validate runtime JS
    behaviour here (no headless browser in CI); the string-level guard is
    a regression tripwire — if someone removes the config the test fails.
    """
    _, port = server
    with urlopen(f"http://localhost:{port}/") as r:
        html = r.read().decode("utf-8")
    assert "marked.use" in html, \
        "SPA must call marked.use({renderer: {...}}) to disable raw HTML"
    assert "renderer" in html and "html" in html, \
        "marked.use must override renderer.html to strip raw HTML"


# === V2.2 PUT/DELETE handler tests ===

def _put_json(port: int, payload: dict) -> dict:
    req = Request(
        f"http://localhost:{port}/api/entry",
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req) as r:
        return json.loads(r.read())


def _delete_json(port: int, payload: dict) -> dict:
    req = Request(
        f"http://localhost:{port}/api/entry",
        data=json.dumps(payload).encode("utf-8"),
        method="DELETE",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req) as r:
        return json.loads(r.read())


def _put_raw(port: int, raw_body: bytes) -> int:
    """Send a raw byte body (for malformed JSON test). Returns HTTP status."""
    req = Request(
        f"http://localhost:{port}/api/entry",
        data=raw_body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req) as r:
            return r.status
    except HTTPError as e:
        return e.code


def _delete_raw(port: int, raw_body: bytes) -> int:
    req = Request(
        f"http://localhost:{port}/api/entry",
        data=raw_body,
        method="DELETE",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req) as r:
            return r.status
    except HTTPError as e:
        return e.code


def test_api_entry_put_updates_body_and_metadata(server, mock_projects):
    _, port = server
    target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
    _put_json(port, {
        "path": str(target),
        "name": "Patched name",
        "description": "Patched desc",
        "body": "**Patched:** new body content.\n",
    })
    text = target.read_text(encoding="utf-8")
    assert "name: Patched name" in text
    assert "description: Patched desc" in text
    assert "**Patched:** new body content." in text
    assert "**Why:** because of X" not in text


def test_api_entry_put_partial_only_description(server, mock_projects):
    _, port = server
    target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
    _put_json(port, {"path": str(target), "description": "Only desc changed"})
    text = target.read_text(encoding="utf-8")
    assert "description: Only desc changed" in text
    assert "name: First feedback" in text
    assert "**Why:** because of X" in text


def test_api_entry_put_path_traversal_403(server, mock_projects, tmp_path):
    _, port = server
    outside = tmp_path / "secret.md"
    outside.write_text("---\nname: x\ntype: feedback\n---\nbody\n", encoding="utf-8")
    with pytest.raises(HTTPError) as exc_info:
        _put_json(port, {"path": str(outside), "name": "evil"})
    assert exc_info.value.code == 403


def test_api_entry_put_invalid_type_400(server, mock_projects):
    _, port = server
    target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
    with pytest.raises(HTTPError) as exc_info:
        _put_json(port, {"path": str(target), "type": "bogus_type"})
    assert exc_info.value.code == 400


def test_api_entry_put_malformed_json_400(server):
    _, port = server
    assert _put_raw(port, b"{not valid json") == 400


def test_api_entry_put_missing_path_400(server):
    _, port = server
    with pytest.raises(HTTPError) as exc_info:
        _put_json(port, {"name": "no path"})
    assert exc_info.value.code == 400


def test_api_entry_put_nonexistent_file_404(server, mock_projects):
    _, port = server
    target = mock_projects / "D--test-normal" / "memory" / "does_not_exist.md"
    with pytest.raises(HTTPError) as exc_info:
        _put_json(port, {"path": str(target), "name": "ghost"})
    assert exc_info.value.code == 404


def test_api_entry_delete_removes_file(server, mock_projects):
    _, port = server
    target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
    assert target.exists()
    _delete_json(port, {"path": str(target)})
    assert not target.exists()


def test_api_entry_delete_path_traversal_403(server, mock_projects, tmp_path):
    _, port = server
    outside = tmp_path / "secret.md"
    outside.write_text("body\n", encoding="utf-8")
    with pytest.raises(HTTPError) as exc_info:
        _delete_json(port, {"path": str(outside)})
    assert exc_info.value.code == 403
    assert outside.exists()


def test_api_entry_delete_nonexistent_404(server, mock_projects):
    _, port = server
    target = mock_projects / "D--test-normal" / "memory" / "does_not_exist.md"
    with pytest.raises(HTTPError) as exc_info:
        _delete_json(port, {"path": str(target)})
    assert exc_info.value.code == 404


def test_api_entry_delete_malformed_json_400(server):
    _, port = server
    assert _delete_raw(port, b"{garbage") == 400


def test_api_entry_delete_missing_path_400(server):
    _, port = server
    with pytest.raises(HTTPError) as exc_info:
        _delete_json(port, {})
    assert exc_info.value.code == 400


# === SPA design-refresh + CRUD UI tripwires ===

def test_spa_design_refresh_tripwires(server):
    """SPA must adopt the design-refresh aesthetic: light Inter UI + blue
    accent + per-type color pills + Notion-style toolbar.

    String-level tripwire (no headless browser) — fails if the design is
    silently reverted. Also asserts CRUD presence and preserves the PR #3
    marked.use XSS guard.
    """
    _, port = server
    with urlopen(f"http://localhost:{port}/") as r:
        html = r.read().decode("utf-8")
    # design-refresh palette: blue accent + light surface
    assert "#2563eb" in html, "blue accent color missing"
    assert "--bg-panel" in html, "light surface tokens missing"
    # type-pill color vocabulary (one var per type)
    for tok in ("--type-feedback", "--type-user", "--type-project", "--type-reference"):
        assert tok in html, f"type color token {tok} missing"
    # font stack: Inter for UI + JetBrains Mono for code/breadcrumb
    assert "Inter" in html, "Inter UI font missing"
    assert "JetBrains Mono" in html, "JetBrains Mono code font missing"
    # CRUD UI markers (Chinese labels per design-refresh)
    assert "编辑" in html, "edit button missing"
    assert "删除" in html, "delete button missing"
    assert "确认删除" in html, "delete confirm prompt missing"
    # breadcrumb structure (project / memory / filename)
    assert "breadcrumb" in html, "breadcrumb container missing"
    assert "project_short" in html, "breadcrumb must reference project_short"
    # state machine guards
    assert "'view'" in html and "'edit'" in html, "view/edit modes missing"
    assert "'confirm-delete'" in html, "confirm-delete mode missing"
    # CRUD HTTP methods
    assert "'PUT'" in html, "PUT method call missing"
    assert "'DELETE'" in html, "DELETE method call missing"
    # PR #3 XSS guard preserved
    assert "marked.use" in html, "PR #3 marked.use XSS guard removed!"


def test_spa_keyboard_shortcuts(server):
    """SPA must bind Escape (cancel), Ctrl/Cmd+S (save), Ctrl/Cmd+K (search)."""
    _, port = server
    with urlopen(f"http://localhost:{port}/") as r:
        html = r.read().decode("utf-8")
    assert "Escape" in html, "Escape keybinding missing"
    # design-refresh uses (ev.ctrlKey || ev.metaKey) — both tokens present
    assert "ctrlKey" in html, "ctrlKey binding missing"
    assert "metaKey" in html, "metaKey binding missing (Cmd shortcuts)"
    assert "'k'" in html, "Cmd/Ctrl+K search shortcut missing"


# === codex P1+P2 follow-up tests ===

def test_api_entry_delete_rejects_non_memory_403(server, mock_projects):
    """codex P1: DELETE on a path under projects_root but not memory/ → 403"""
    _, port = server
    project = mock_projects / "D--test-normal"
    sessions = project / "sessions"
    sessions.mkdir(parents=True)
    target = sessions / "abc-123.jsonl"
    target.write_text("session log\n", encoding="utf-8")

    with pytest.raises(HTTPError) as exc_info:
        _delete_json(port, {"path": str(target)})
    assert exc_info.value.code == 403, "non-memory file delete must return 403"
    assert target.exists(), "non-memory file must NOT be deleted"


def test_api_entry_put_non_string_body_400(server, mock_projects):
    """codex P2: PUT with non-string body returns 400, not 500"""
    _, port = server
    target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
    with pytest.raises(HTTPError) as exc_info:
        _put_json(port, {"path": str(target), "body": 123})
    assert exc_info.value.code == 400, "non-string body must return 400"
