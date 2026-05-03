from __future__ import annotations

import http.server
import json
import socket
import socketserver
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

from .paths import decode_project_dir
from .store import MemoryStore, NotFoundError


def _port_in_use(host: str, port: int) -> bool:
    # Windows 默认允许多个 socket bind 同一个 LISTEN 端口（Linux 不允许），
    # 只 bind 探测会假阴性。必须 connect_ex 主动握手才能发现已有 listener。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def _find_free_port(host: str = "localhost", preferred: int = 8765) -> int:
    candidates = [preferred] + [p for p in range(8766, 8800) if p != preferred]
    for port in candidates:
        if _port_in_use(host, port):
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return port
        except OSError:
            continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _resolve_listen_port(host: str, port: Optional[int]) -> int:
    if port is None:
        return _find_free_port(host)
    if _port_in_use(host, port):
        raise OSError(
            f"Port {port} on {host} is already in use by another process"
        )
    return port


def _safe_resolve(file_path: str, root: Path) -> Optional[Path]:
    try:
        target = Path(file_path).resolve()
        root_resolved = root.resolve()
        target.relative_to(root_resolved)
        return target
    except (ValueError, OSError):
        return None


def _projects_payload(store: MemoryStore) -> dict:
    projects = store.list_projects(include_aliases=False, include_empty=False)
    entries = store.list_entries()
    grouped: dict = {}
    for p in projects:
        index_path = str(p.memory_dir / "MEMORY.md") if (p.has_index and p.memory_dir) else None
        grouped[p.project_id] = {
            "project_id": p.project_id,
            "short_id": store.short_id(p.project_id),
            "project_path": p.project_path,
            "entry_count": p.entry_count,
            "mtime": p.mtime,
            "has_index": p.has_index,
            "index_path": index_path,
            "entries": [],
        }
    for e in entries:
        bucket = grouped.get(e.project_id)
        if bucket is None:
            continue
        bucket["entries"].append({
            "filename": e.filename,
            "name": e.name,
            "description": e.description,
            "type": e.type,
            "mtime": e.mtime,
            "file_path": str(e.file_path),
        })
    for bucket in grouped.values():
        bucket["entries"].sort(key=lambda e: (e["type"], e["filename"]))
    return {"projects": sorted(grouped.values(), key=lambda p: -p["mtime"])}


def _entry_payload(store: MemoryStore, target: Path) -> Optional[dict]:
    entry = store.parse_entry(target)
    if entry is None:
        return None
    return {
        "filename": entry.filename,
        "name": entry.name,
        "description": entry.description,
        "type": entry.type,
        "origin_session_id": entry.origin_session_id,
        "body": entry.body,
        "file_path": str(entry.file_path),
        "project_id": entry.project_id,
        "project_path": entry.project_path,
        "project_short": store.short_id(entry.project_id),
        "mtime": entry.mtime,
    }


def _index_payload(store: MemoryStore, project_id: str) -> Optional[dict]:
    result = store.read_index(project_id)
    if result is None:
        return None
    file_path, body, mtime = result
    full_id = file_path.parent.parent.name
    return {
        "project_id": full_id,
        "project_short": store.short_id(full_id),
        "project_path": decode_project_dir(full_id) or full_id,
        "path": str(file_path),
        "body": body,
        "mtime": mtime,
    }


def make_handler(store: MemoryStore, projects_root: Path):
    class NotepadHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                self._serve_html(_INDEX_HTML)
            elif path == "/api/projects":
                self._serve_json(_projects_payload(store))
            elif path == "/api/entry":
                qs = urllib.parse.parse_qs(parsed.query)
                self._handle_entry(qs.get("path", [""])[0])
            elif path == "/api/index":
                qs = urllib.parse.parse_qs(parsed.query)
                self._handle_index(qs.get("project", [""])[0])
            else:
                self._serve_json({"error": "not found"}, 404)

        def do_PUT(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/entry":
                self._handle_entry_put()
                return
            if parsed.path == "/api/index":
                self._handle_index_put()
                return
            self._serve_json({"error": "not found"}, 404)

        def _handle_entry_put(self) -> None:
            payload = self._read_json_body()
            if payload is None:
                self._serve_json({"error": "malformed JSON body"}, 400)
                return
            target = self._resolve_or_error(payload.get("path"))
            if target is None:
                return
            if not target.exists():
                self._serve_json({"error": "not found"}, 404)
                return
            try:
                entry = store.update_entry(
                    target,
                    name=payload.get("name"),
                    description=payload.get("description"),
                    type=payload.get("type"),
                    body=payload.get("body"),
                )
            except (ValueError, OSError) as e:
                self._serve_json({"error": str(e)}, 400)
                return
            self._serve_json(_entry_payload(store, entry.file_path) or {"ok": True})

        def _handle_index_put(self) -> None:
            payload = self._read_json_body()
            if payload is None:
                self._serve_json({"error": "malformed JSON body"}, 400)
                return
            pid = payload.get("project_id")
            body = payload.get("body")
            if not isinstance(pid, str) or not pid:
                self._serve_json({"error": "missing project_id"}, 400)
                return
            try:
                store.write_index(pid, body)
            except ValueError as e:
                self._serve_json({"error": str(e)}, 400)
                return
            except NotFoundError as e:
                self._serve_json({"error": str(e)}, 404)
                return
            except OSError as e:
                self._serve_json({"error": str(e)}, 400)
                return
            self._serve_json(_index_payload(store, pid) or {"ok": True})

        def do_DELETE(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/entry":
                self._handle_entry_delete()
                return
            if parsed.path == "/api/index":
                self._handle_index_delete()
                return
            self._serve_json({"error": "not found"}, 404)

        def _handle_entry_delete(self) -> None:
            payload = self._read_json_body()
            if payload is None:
                self._serve_json({"error": "malformed JSON body"}, 400)
                return
            target = self._resolve_or_error(payload.get("path"))
            if target is None:
                return
            try:
                store.delete_entry(target)
            except FileNotFoundError:
                self._serve_json({"error": "not found"}, 404)
                return
            except ValueError as e:
                self._serve_json({"error": str(e)}, 403)
                return
            except OSError as e:
                self._serve_json({"error": str(e)}, 400)
                return
            self._serve_json({"ok": True, "deleted": str(target)})

        def _handle_index_delete(self) -> None:
            payload = self._read_json_body()
            if payload is None:
                self._serve_json({"error": "malformed JSON body"}, 400)
                return
            pid = payload.get("project_id")
            if not isinstance(pid, str) or not pid:
                self._serve_json({"error": "missing project_id"}, 400)
                return
            try:
                store.delete_index(pid)
            except NotFoundError as e:
                self._serve_json({"error": str(e)}, 404)
                return
            except OSError as e:
                self._serve_json({"error": str(e)}, 400)
                return
            self._serve_json({"ok": True, "project_id": pid})

        def _read_json_body(self) -> Optional[dict]:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0:
                return None
            try:
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                return None
            return payload if isinstance(payload, dict) else None

        def _resolve_or_error(self, file_path: Optional[str]) -> Optional[Path]:
            if not file_path or not isinstance(file_path, str):
                self._serve_json({"error": "missing path"}, 400)
                return None
            target = _safe_resolve(file_path, projects_root)
            if target is None:
                self._serve_json({"error": "forbidden: path outside projects root"}, 403)
                return None
            return target

        def _handle_entry(self, file_path: str) -> None:
            if not file_path:
                self._serve_json({"error": "missing path"}, 400)
                return
            target = _safe_resolve(file_path, projects_root)
            if target is None:
                self._serve_json({"error": "forbidden: path outside projects root"}, 403)
                return
            payload = _entry_payload(store, target)
            if payload is None:
                self._serve_json({"error": "not found or unparseable"}, 404)
                return
            self._serve_json(payload)

        def _handle_index(self, project_id: str) -> None:
            if not project_id:
                self._serve_json({"error": "missing project"}, 400)
                return
            payload = _index_payload(store, project_id)
            if payload is None:
                self._serve_json({"error": "index not found"}, 404)
                return
            self._serve_json(payload)

        def _serve_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_json(self, payload: dict, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args) -> None:
            return

    return NotepadHandler


class _ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def start_server(store: MemoryStore,
                 host: str = "localhost",
                 port: Optional[int] = None,
                 open_browser: bool = True) -> None:
    port = _resolve_listen_port(host, port)
    handler_cls = make_handler(store, store.projects_dir)
    httpd = _ThreadedServer((host, port), handler_cls)
    url = f"http://{host}:{port}"
    print(f"mmcc notepad serving at {url}", file=sys.stderr)
    print("Press Ctrl-C to stop.", file=sys.stderr)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", file=sys.stderr)
    finally:
        httpd.server_close()


_INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>mmcc notepad</title>
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; }
  :root {
    --bg: #ffffff;
    --bg-panel: #fafaf9;
    --bg-panel-alt: #f4f4f3;
    --bg-hover: #f0f0ee;
    --bg-code: #0f1419;
    --bg-code-fg: #e1e4e8;
    --border: #e8e8e6;
    --border-soft: #efefed;
    --ink: #1a1a1a;
    --ink-soft: #525252;
    --ink-mute: #8a8a87;
    --accent: #2563eb;
    --accent-hover: #1d4ed8;
    --accent-soft: #eff4ff;
    --accent-border: #bfd4fe;
    --danger: #dc2626;
    --danger-soft: #fef2f2;
    --danger-border: #fca5a5;
    --type-feedback: #d97706; --type-feedback-bg: #fef4e6;
    --type-user: #16a34a;     --type-user-bg: #ecfdf3;
    --type-project: #2563eb;  --type-project-bg: #eff4ff;
    --type-reference: #7c3aed;--type-reference-bg: #f3eefe;
    --type-unknown: #6b7280;  --type-unknown-bg: #f3f4f6;
    --type-index: #0891b2;    --type-index-bg: #ecfeff;
    --code-inline-fg: #b91c5c;
  }
  html, body { margin: 0; padding: 0; height: 100%;
    font-family: "Inter", -apple-system, "Segoe UI", system-ui, sans-serif;
    font-size: 13.5px; color: var(--ink); background: var(--bg);
    -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
  body { display: flex; flex-direction: column; height: 100vh; }

  /* Header */
  header { flex-shrink: 0; height: 48px; padding: 0 16px;
    background: var(--bg); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 12px; }
  .icon-btn { background: transparent; border: none; padding: 6px;
    border-radius: 5px; cursor: pointer; color: var(--ink-soft);
    display: inline-flex; align-items: center; justify-content: center;
    transition: background 0.12s; }
  .icon-btn:hover { background: var(--bg-hover); }
  .brand { display: flex; align-items: center; gap: 8px; }
  .brand .logo { width: 22px; height: 22px; border-radius: 6px;
    background: var(--accent); color: #fff; font-weight: 700; font-size: 11px;
    display: inline-flex; align-items: center; justify-content: center; }
  .brand .name { font-weight: 600; font-size: 14px; }
  .brand .sub { color: var(--ink-mute); font-size: 13px; }
  .search-wrap { flex: 1; max-width: 480px; margin-left: 24px; position: relative; }
  .search-wrap svg { position: absolute; left: 10px; top: 50%;
    transform: translateY(-50%); color: var(--ink-mute); }
  .search-wrap input { width: 100%; height: 32px; padding: 0 50px 0 30px;
    border-radius: 6px; border: 1px solid var(--border);
    background: var(--bg-panel); color: var(--ink);
    font-family: inherit; font-size: 13px; outline: none; transition: border-color 0.12s, box-shadow 0.12s; }
  .search-wrap input:focus { border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft); background: #fff; }
  .search-wrap .kbd { position: absolute; right: 8px; top: 50%;
    transform: translateY(-50%); font-size: 11px; color: var(--ink-mute);
    background: #fff; border: 1px solid var(--border);
    border-radius: 4px; padding: 1px 5px; font-family: "JetBrains Mono", monospace; pointer-events: none; }
  .spacer { flex: 1; }
  .type-tabs { display: flex; gap: 2px; }
  .type-tab { padding: 5px 10px; border-radius: 5px; font-size: 12.5px;
    border: none; cursor: pointer; background: transparent;
    color: var(--ink-soft); font-family: inherit; font-weight: 400;
    transition: background 0.12s, color 0.12s; }
  .type-tab:hover { background: var(--bg-hover); color: var(--ink); }
  .type-tab.active { background: var(--bg-panel-alt); color: var(--ink); font-weight: 500; }
  .divider { width: 1px; height: 20px; background: var(--border); margin: 0 4px; }
  .stat { color: var(--ink-mute); font-size: 12px; font-variant-numeric: tabular-nums; }

  /* Layout */
  #app { flex: 1; display: flex; min-height: 0; overflow: hidden; }
  aside { width: 280px; flex-shrink: 0; overflow-y: auto;
    background: var(--bg-panel); border-right: 1px solid var(--border);
    padding: 6px 0; transition: width 0.18s ease, opacity 0.18s ease; }
  aside.collapsed { width: 0; opacity: 0; padding: 0; border-right: none; overflow: hidden; }
  main { flex: 1; overflow-y: auto; background: var(--bg); }

  /* Sidebar tree */
  .project-row { display: flex; align-items: center; gap: 6px;
    padding: 6px 12px; cursor: pointer; user-select: none;
    color: var(--ink-soft); font-size: 12.5px; }
  .project-row:hover { background: var(--bg-hover); }
  .project-row .chev { width: 10px; height: 10px; flex-shrink: 0;
    color: var(--ink-mute); transition: transform 0.15s; }
  .project-row.open .chev { transform: rotate(90deg); }
  .project-row .pname { font-weight: 500; flex: 1; color: var(--ink);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .project-row .pcount { color: var(--ink-mute); font-size: 11.5px;
    font-variant-numeric: tabular-nums; }
  .entries-wrap { padding-bottom: 2px; }
  .project-row.collapsed + .entries-wrap { display: none; }
  .entry { padding: 5px 12px 5px 24px; cursor: pointer;
    background: transparent; font-size: 12.5px;
    display: flex; align-items: center; gap: 7px;
    border-left: 2px solid transparent; transition: background 0.1s; }
  .entry:hover { background: var(--bg-hover); }
  .entry.selected { background: var(--accent-soft);
    border-left-color: var(--accent); }
  .entry .glyph { font-size: 9.5px; font-weight: 600;
    padding: 1px 5px; border-radius: 3px; flex-shrink: 0;
    text-transform: uppercase; letter-spacing: 0.05em;
    width: 18px; text-align: center; }
  .entry .ename { color: var(--ink); flex: 1;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .entry.selected .ename { font-weight: 500; }

  /* Type colors */
  .glyph.t-feedback  { background: var(--type-feedback-bg);  color: var(--type-feedback); }
  .glyph.t-user      { background: var(--type-user-bg);      color: var(--type-user); }
  .glyph.t-project   { background: var(--type-project-bg);   color: var(--type-project); }
  .glyph.t-reference { background: var(--type-reference-bg); color: var(--type-reference); }
  .glyph.t-unknown   { background: var(--type-unknown-bg);   color: var(--type-unknown); }
  .glyph.t-index     { background: var(--type-index-bg);     color: var(--type-index); }
  .pill.t-feedback  { background: var(--type-feedback-bg);  color: var(--type-feedback); }
  .pill.t-user      { background: var(--type-user-bg);      color: var(--type-user); }
  .pill.t-project   { background: var(--type-project-bg);   color: var(--type-project); }
  .pill.t-reference { background: var(--type-reference-bg); color: var(--type-reference); }
  .pill.t-unknown   { background: var(--type-unknown-bg);   color: var(--type-unknown); }
  .pill.t-index     { background: var(--type-index-bg);     color: var(--type-index); }
  .entry.index-row  { font-style: italic; }
  .entry.index-row .ename { color: var(--type-index); }

  /* Main viewer */
  .viewer { max-width: 780px; margin: 0 auto; padding: 36px 56px 80px; }
  .breadcrumb { display: flex; align-items: center; gap: 8px;
    font-size: 12.5px; color: var(--ink-mute); margin-bottom: 18px;
    font-family: "JetBrains Mono", ui-monospace, monospace; }
  .breadcrumb .last { color: var(--ink-soft); }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; }
  .viewer h1.title { font-weight: 600; font-size: 30px; line-height: 1.25;
    margin: 8px 0 10px; letter-spacing: -0.02em; color: var(--ink); }
  .viewer .desc { font-size: 15px; color: var(--ink-soft);
    line-height: 1.55; margin: 0 0 20px; }
  .toolbar { display: flex; align-items: center; gap: 6px;
    padding-bottom: 16px; margin-bottom: 28px;
    border-bottom: 1px solid var(--border); }
  .btn { display: inline-flex; align-items: center; gap: 5px;
    font-family: inherit; font-size: 12.5px; font-weight: 500;
    padding: 6px 12px; border-radius: 6px; cursor: pointer;
    border: 1px solid var(--border); background: var(--bg);
    color: var(--ink); transition: all 0.12s; }
  .btn:hover { background: var(--bg-panel-alt); }
  .btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  .btn.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
  .btn.danger { color: var(--danger); }
  .btn.danger:hover { background: var(--danger-soft); border-color: var(--danger-border); }
  .toolbar .mtime { font-size: 12px; color: var(--ink-mute);
    font-variant-numeric: tabular-nums; }

  /* Markdown body */
  .body { font-size: 14px; color: var(--ink); line-height: 1.7; }
  .body h1 { font-weight: 600; font-size: 20px; margin: 24px 0 8px;
    letter-spacing: -0.015em; color: var(--ink); }
  .body h2 { font-weight: 600; font-size: 16.5px; margin: 22px 0 6px;
    letter-spacing: -0.01em; color: var(--ink); }
  .body h3 { font-weight: 600; font-size: 14.5px; margin: 16px 0 4px; color: var(--ink); }
  .body p { margin: 6px 0; }
  .body ol, .body ul { padding-left: 22px; margin: 6px 0; }
  .body li { margin: 3px 0; }
  .body blockquote { border-left: 3px solid var(--accent);
    padding: 6px 14px; margin: 10px 0; color: var(--ink-soft);
    background: var(--accent-soft); border-radius: 0 4px 4px 0; }
  .body pre { background: var(--bg-code); color: var(--bg-code-fg);
    padding: 14px 16px; border-radius: 8px; overflow-x: auto;
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 12.5px; line-height: 1.6; margin: 12px 0; }
  .body :not(pre) > code { background: var(--bg-panel-alt);
    color: var(--code-inline-fg); padding: 1.5px 6px;
    border-radius: 4px; font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 12.5px; border: 1px solid var(--border-soft); }
  .body table { border-collapse: collapse; margin: 10px 0; }
  .body th, .body td { padding: 6px 12px; border: 1px solid var(--border); }
  .body th { background: var(--bg-panel); font-weight: 600; }
  .body a { color: var(--accent); text-decoration: none; }
  .body a:hover { text-decoration: underline; }

  /* Editor */
  .editor { display: flex; flex-direction: column; gap: 10px; margin-top: 16px; }
  .editor label { color: var(--ink-mute); font-size: 12px; font-weight: 500; }
  .editor input, .editor textarea { background: var(--bg);
    color: var(--ink); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 12px; font-family: inherit;
    font-size: 13.5px; outline: none; transition: border-color 0.12s, box-shadow 0.12s; }
  .editor input:focus, .editor textarea:focus {
    border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .editor textarea { min-height: 360px; resize: vertical; line-height: 1.6;
    font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 13px; }

  /* Confirm */
  .confirm { padding: 14px 16px; border: 1px solid var(--danger-border);
    background: var(--danger-soft); color: var(--danger);
    border-radius: 8px; margin-top: 16px; font-size: 13px; line-height: 1.5; }
  .confirm code { background: rgba(220,38,38,0.08); padding: 1px 5px;
    border-radius: 3px; font-family: "JetBrains Mono", ui-monospace, monospace; }

  .empty { color: var(--ink-mute); text-align: center; margin-top: 80px; font-size: 13px; }
</style>
</head>
<body>
<header>
  <button class="icon-btn" id="toggle-sidebar" title="收起/展开侧栏">
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <rect x="2" y="3" width="12" height="10" rx="1.5" stroke="currentColor" stroke-width="1.3"/>
      <path d="M6 3v10" stroke="currentColor" stroke-width="1.3"/>
      <rect x="3" y="5" width="2" height="1" fill="currentColor"/>
      <rect x="3" y="7" width="2" height="1" fill="currentColor"/>
    </svg>
  </button>
  <div class="brand">
    <div class="logo">m</div>
    <span class="name">mmcc</span>
    <span class="sub">/ notepad</span>
  </div>
  <div class="search-wrap">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
      <circle cx="7" cy="7" r="4.5" stroke="currentColor" stroke-width="1.5"/>
      <path d="M10.5 10.5L13 13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
    <input id="search" placeholder="搜索 memory…" />
    <span class="kbd">⌘K</span>
  </div>
  <div class="spacer"></div>
  <div class="type-tabs" id="type-tabs">
    <button class="type-tab active" data-type="">all</button>
    <button class="type-tab" data-type="feedback">feedback</button>
    <button class="type-tab" data-type="user">user</button>
    <button class="type-tab" data-type="project">project</button>
    <button class="type-tab" data-type="reference">reference</button>
  </div>
  <div class="divider"></div>
  <span class="stat" id="stat"></span>
  <button class="icon-btn" id="refresh-btn" title="刷新（重拉项目树，编辑模式时不动 viewer）">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
      <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M13.5 2v3h-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </button>
</header>
<div id="app">
  <aside id="tree"></aside>
  <main id="viewer"><div class="empty">从左侧选择一条 memory 查看</div></main>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
<script>
// XSS guard — strip raw HTML from rendered markdown bodies (CLAUDE.md hard constraint)
marked.use({ renderer: { html() { return ''; } } });

const TYPE_GLYPH = { feedback: 'F', user: 'U', project: 'P', reference: 'R', unknown: '?', index: '📋' };
const TYPE_LABEL = { feedback: 'feedback', user: 'user', project: 'project', reference: 'reference', unknown: 'note', index: 'index' };

let projects = [];
// selected: null | {kind: 'entry', payload: {...}} | {kind: 'index', payload: {...}}
let selected = null;
let mode = 'view';

function selectedFilePath() {
  if (!selected) return null;
  return selected.kind === 'entry' ? selected.payload.file_path : selected.payload.path;
}

async function loadProjects() {
  try {
    const r = await fetch('/api/projects');
    const data = await r.json();
    projects = data.projects || [];
    renderTree();
  } catch (e) {
    document.getElementById('tree').innerHTML = '<div class="empty">加载失败：' + escapeHTML(e.message) + '</div>';
  }
}

function renderTree() {
  const search = document.getElementById('search').value.toLowerCase().trim();
  const typeFilter = document.querySelector('.type-tab.active').dataset.type;
  const tree = document.getElementById('tree');
  tree.innerHTML = '';
  let totalShown = 0;
  for (const p of projects) {
    const matched = p.entries.filter(e => {
      if (typeFilter && e.type !== typeFilter) return false;
      if (!search) return true;
      const hay = (e.name + ' ' + (e.description || '') + ' ' + e.filename).toLowerCase();
      return hay.includes(search);
    });
    // 索引不属于 4 个 type 之一, 不被类型 tab 过滤; 仅响应 search 关键字
    let showIndexRow = false;
    if (p.has_index) {
      if (!search) showIndexRow = true;
      else if ('索引 memory.md index'.toLowerCase().includes(search)) showIndexRow = true;
    }
    if (matched.length === 0 && !showIndexRow) continue;
    totalShown += matched.length + (showIndexRow ? 1 : 0);

    const projDiv = document.createElement('div');
    projDiv.className = 'project-row open';
    const pcount = matched.length + (showIndexRow ? 1 : 0);
    projDiv.innerHTML =
      '<svg class="chev" viewBox="0 0 10 10"><path d="M3 2l4 3-4 3z" fill="currentColor"/></svg>' +
      '<span class="pname">' + escapeHTML(p.short_id) + '</span>' +
      '<span class="pcount">' + pcount + '</span>';
    projDiv.onclick = () => {
      projDiv.classList.toggle('collapsed');
      projDiv.classList.toggle('open');
    };
    tree.appendChild(projDiv);

    const wrap = document.createElement('div');
    wrap.className = 'entries-wrap';

    if (showIndexRow) {
      const idxRow = document.createElement('div');
      idxRow.className = 'entry index-row';
      idxRow.dataset.kind = 'index';
      idxRow.dataset.projectId = p.project_id;
      idxRow.innerHTML =
        '<span class="glyph t-index">' + TYPE_GLYPH.index + '</span>' +
        '<span class="ename">索引 (MEMORY.md)</span>';
      idxRow.title = p.index_path || 'MEMORY.md';
      idxRow.onclick = () => selectIndex(p.project_id);
      wrap.appendChild(idxRow);
    }

    for (const e of matched) {
      const row = document.createElement('div');
      row.className = 'entry';
      row.dataset.kind = 'entry';
      row.dataset.path = e.file_path;
      const tkey = TYPE_GLYPH[e.type] ? e.type : 'unknown';
      const glyph = TYPE_GLYPH[tkey];
      row.innerHTML =
        '<span class="glyph t-' + tkey + '">' + glyph + '</span>' +
        '<span class="ename">' + escapeHTML(e.name) + '</span>';
      row.title = e.description || e.filename;
      row.onclick = () => selectEntry(e.file_path);
      wrap.appendChild(row);
    }
    tree.appendChild(wrap);
  }
  document.getElementById('stat').textContent = totalShown + ' 条';
  if (totalShown === 0) {
    tree.innerHTML = '<div class="empty">无匹配结果</div>';
  }
  if (selected) {
    document.querySelectorAll('.entry').forEach(el => {
      let match = false;
      if (selected.kind === 'entry' && el.dataset.kind === 'entry') {
        match = el.dataset.path === selected.payload.file_path;
      } else if (selected.kind === 'index' && el.dataset.kind === 'index') {
        match = el.dataset.projectId === selected.payload.project_id;
      }
      el.classList.toggle('selected', match);
    });
  }
}

async function selectEntry(filePath) {
  document.querySelectorAll('.entry').forEach(el => {
    el.classList.toggle('selected', el.dataset.kind === 'entry' && el.dataset.path === filePath);
  });
  try {
    const r = await fetch('/api/entry?path=' + encodeURIComponent(filePath));
    if (!r.ok) {
      document.getElementById('viewer').innerHTML = '<div class="empty">加载失败：' + r.status + '</div>';
      return;
    }
    const payload = await r.json();
    selected = { kind: 'entry', payload };
    mode = 'view';
    renderEntry();
  } catch (err) {
    document.getElementById('viewer').innerHTML = '<div class="empty">网络错误：' + escapeHTML(err.message) + '</div>';
  }
}

async function selectIndex(projectId) {
  document.querySelectorAll('.entry').forEach(el => {
    el.classList.toggle('selected', el.dataset.kind === 'index' && el.dataset.projectId === projectId);
  });
  try {
    const r = await fetch('/api/index?project=' + encodeURIComponent(projectId));
    if (!r.ok) {
      document.getElementById('viewer').innerHTML = '<div class="empty">加载失败：' + r.status + '</div>';
      return;
    }
    const payload = await r.json();
    selected = { kind: 'index', payload };
    mode = 'view';
    renderEntry();
  } catch (err) {
    document.getElementById('viewer').innerHTML = '<div class="empty">网络错误：' + escapeHTML(err.message) + '</div>';
  }
}

function renderEntry() {
  const v = document.getElementById('viewer');
  v.innerHTML = '';
  if (!selected) { v.innerHTML = '<div class="empty">未选中任何条目</div>'; return; }
  const e = selected.payload;
  const isIndex = selected.kind === 'index';
  const wrap = document.createElement('div');
  wrap.className = 'viewer';

  const tkey = isIndex ? 'index' : (TYPE_GLYPH[e.type] ? e.type : 'unknown');
  const filename = isIndex ? 'MEMORY.md' : e.filename;
  const titleText = isIndex ? '索引 (MEMORY.md)' : e.name;

  const crumb = document.createElement('div');
  crumb.className = 'breadcrumb';
  crumb.innerHTML =
    '<span>' + escapeHTML(e.project_short || e.project_path || '') + '</span>' +
    '<span>/</span><span>memory</span><span>/</span>' +
    '<span class="last">' + escapeHTML(filename) + '</span>';
  wrap.appendChild(crumb);

  const pillWrap = document.createElement('div');
  pillWrap.innerHTML = '<span class="pill t-' + tkey + '">' + escapeHTML(TYPE_LABEL[tkey]) + '</span>';
  wrap.appendChild(pillWrap);

  const title = document.createElement('h1');
  title.className = 'title';
  title.textContent = titleText;
  wrap.appendChild(title);

  if (!isIndex && e.description) {
    const desc = document.createElement('p');
    desc.className = 'desc';
    desc.textContent = e.description;
    wrap.appendChild(desc);
  }

  if (mode === 'view') renderViewMode(wrap, e, isIndex);
  else if (mode === 'edit') renderEditMode(wrap, e, isIndex);
  else if (mode === 'confirm-delete') renderConfirmDelete(wrap, e, isIndex);

  v.appendChild(wrap);
}

function renderViewMode(wrap, e, isIndex) {
  const filePath = isIndex ? e.path : e.file_path;

  const tb = document.createElement('div');
  tb.className = 'toolbar';

  const btnEdit = makeBtn('编辑', 'primary');
  btnEdit.onclick = () => { mode = 'edit'; renderEntry(); };
  const btnCopy = makeBtn('复制路径');
  btnCopy.onclick = copyPath;
  const btnVS = document.createElement('a');
  btnVS.className = 'btn';
  btnVS.textContent = '在 VSCode 打开';
  btnVS.href = 'vscode://file/' + (filePath || '').replace(/\\/g, '/');

  const spacer = document.createElement('span');
  spacer.style.flex = '1';

  const mt = document.createElement('span');
  mt.className = 'mtime';
  mt.textContent = e.mtime ? '最后修改 ' + formatMtime(e.mtime) : '';

  const btnDel = makeBtn('删除', 'danger');
  btnDel.onclick = () => { mode = 'confirm-delete'; renderEntry(); };

  tb.appendChild(btnEdit); tb.appendChild(btnCopy); tb.appendChild(btnVS);
  tb.appendChild(spacer); tb.appendChild(mt); tb.appendChild(btnDel);
  wrap.appendChild(tb);

  const body = document.createElement('div');
  body.className = 'body';
  body.innerHTML = marked.parse(e.body || '');
  wrap.appendChild(body);

  if (isIndex) {
    body.addEventListener('click', handleIndexLinkClick);
  }
}

function handleIndexLinkClick(ev) {
  const a = ev.target.closest('a'); if (!a) return;
  const href = a.getAttribute('href') || '';
  // 外部 http(s) 链接: 新 tab 打开
  if (/^https?:\/\//i.test(href)) {
    a.target = '_blank';
    a.rel = 'noopener';
    return;
  }
  // 危险协议 / 锚点: 直接拒绝默认行为
  if (href.startsWith('#') || /^(javascript|data|file|vbscript):/i.test(href)) {
    ev.preventDefault();
    return;
  }
  // 内部 .md 链接: 拦截并切换到对应条目或索引
  const m = /^([^?#]+\.md)(\?.*)?(#.*)?$/i.exec(href);
  if (!m) return;
  ev.preventDefault();
  if (!selected || selected.kind !== 'index') return;
  let filename;
  try { filename = decodeURIComponent(m[1]); } catch (_) { filename = m[1]; }
  if (filename.toLowerCase() === 'memory.md') {
    selectIndex(selected.payload.project_id);
    return;
  }
  const indexPath = selected.payload.path || '';
  const sep = indexPath.includes('\\') ? '\\' : '/';
  const dir = indexPath.substring(0, indexPath.lastIndexOf(sep));
  if (!dir) return;
  selectEntry(dir + sep + filename);
}

function renderEditMode(wrap, e, isIndex) {
  // wrap 在 renderEntry 末尾才 appendChild 到 viewer, 这里仍是 detached node;
  // 用闭包变量赋 value, 不要走 document.getElementById (会返回 null)
  const editor = document.createElement('div');
  editor.className = 'editor';

  let inpName, inpDesc, inpBody;

  if (!isIndex) {
    const lblName = document.createElement('label'); lblName.textContent = 'name';
    inpName = document.createElement('input');
    inpName.id = 'inp-name'; inpName.value = e.name || '';

    const lblDesc = document.createElement('label'); lblDesc.textContent = 'description';
    inpDesc = document.createElement('input');
    inpDesc.id = 'inp-desc'; inpDesc.value = e.description || '';

    editor.appendChild(lblName); editor.appendChild(inpName);
    editor.appendChild(lblDesc); editor.appendChild(inpDesc);
  }

  const lblBody = document.createElement('label');
  lblBody.textContent = isIndex ? 'MEMORY.md (raw markdown)' : 'body (markdown)';
  inpBody = document.createElement('textarea');
  inpBody.id = 'inp-body'; inpBody.value = e.body || '';

  editor.appendChild(lblBody); editor.appendChild(inpBody);
  wrap.appendChild(editor);

  const tb = document.createElement('div');
  tb.className = 'toolbar';
  tb.style.borderBottom = 'none';
  tb.style.marginTop = '14px';
  tb.style.paddingBottom = '0';
  const save = makeBtn('保存 (⌘S)', 'primary');
  save.onclick = saveEdit;
  const cancel = makeBtn('取消 (Esc)');
  cancel.onclick = () => { mode = 'view'; renderEntry(); };
  tb.appendChild(save); tb.appendChild(cancel);
  wrap.appendChild(tb);

  setTimeout(() => (isIndex ? inpBody : inpName).focus(), 0);
}

function renderConfirmDelete(wrap, e, isIndex) {
  const filePath = isIndex ? e.path : e.file_path;
  const c = document.createElement('div');
  c.className = 'confirm';
  if (isIndex) {
    c.innerHTML = '确认删除 <code>' + escapeHTML(filePath) + '</code> ？删除后该项目的索引文件 MEMORY.md 将丢失，需要重新手动维护。此操作不可撤销。';
  } else {
    c.innerHTML = '确认删除 <code>' + escapeHTML(filePath) + '</code> ？此操作不可撤销。';
  }
  wrap.appendChild(c);
  const tb = document.createElement('div');
  tb.className = 'toolbar';
  tb.style.borderBottom = 'none';
  tb.style.marginTop = '14px';
  tb.style.paddingBottom = '0';
  const yes = makeBtn('确认删除', 'danger');
  yes.onclick = confirmDelete;
  const no = makeBtn('取消');
  no.onclick = () => { mode = 'view'; renderEntry(); };
  tb.appendChild(yes); tb.appendChild(no);
  wrap.appendChild(tb);
}

function makeBtn(text, kind) {
  const b = document.createElement('button');
  b.className = 'btn' + (kind ? ' ' + kind : '');
  b.textContent = text;
  return b;
}

function formatMtime(mtime) {
  const d = new Date(mtime * 1000);
  if (isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
    + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

async function saveEdit() {
  if (!selected) return;
  const isIndex = selected.kind === 'index';
  const url = isIndex ? '/api/index' : '/api/entry';
  let payload;
  if (isIndex) {
    payload = {
      project_id: selected.payload.project_id,
      body: document.getElementById('inp-body').value,
    };
  } else {
    payload = {
      path: selected.payload.file_path,
      name: document.getElementById('inp-name').value,
      description: document.getElementById('inp-desc').value,
      body: document.getElementById('inp-body').value,
    };
  }
  try {
    const r = await fetch(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert('保存失败：' + r.status + ' ' + (err.error || ''));
      return;
    }
    const updated = await r.json();
    selected = { kind: isIndex ? 'index' : 'entry', payload: updated };
    mode = 'view';
    renderEntry();
    loadProjects();
  } catch (err) {
    alert('网络错误：' + err.message);
  }
}

async function confirmDelete() {
  if (!selected) return;
  const isIndex = selected.kind === 'index';
  const url = isIndex ? '/api/index' : '/api/entry';
  const body = isIndex
    ? { project_id: selected.payload.project_id }
    : { path: selected.payload.file_path };
  try {
    const r = await fetch(url, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert('删除失败：' + r.status + ' ' + (err.error || ''));
      return;
    }
    selected = null;
    mode = 'view';
    document.getElementById('viewer').innerHTML =
      '<div class="empty">' + (isIndex ? '索引已删除' : '条目已删除') + '</div>';
    loadProjects();
  } catch (err) {
    alert('网络错误：' + err.message);
  }
}

function copyPath() {
  const fp = selectedFilePath();
  if (!fp) return;
  navigator.clipboard.writeText(fp).catch(() => alert('复制失败'));
}

function escapeHTML(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

document.getElementById('search').addEventListener('input', renderTree);
document.getElementById('type-tabs').addEventListener('click', (ev) => {
  const t = ev.target.closest('.type-tab');
  if (!t) return;
  document.querySelectorAll('.type-tab').forEach(el => el.classList.toggle('active', el === t));
  renderTree();
});
document.getElementById('toggle-sidebar').addEventListener('click', () => {
  document.getElementById('tree').classList.toggle('collapsed');
});
document.getElementById('refresh-btn').addEventListener('click', refreshAll);

async function refreshAll() {
  // 编辑/删除确认中保护用户未提交的输入, 仅刷新左侧树; view 模式才重拉当前条目
  await loadProjects();
  if (selected && mode === 'view') {
    if (selected.kind === 'index') {
      await selectIndex(selected.payload.project_id);
    } else {
      await selectEntry(selected.payload.file_path);
    }
  }
}

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') {
    if (mode === 'edit' || mode === 'confirm-delete') {
      mode = 'view'; renderEntry(); ev.preventDefault();
    }
  } else if ((ev.ctrlKey || ev.metaKey) && ev.key === 's') {
    if (mode === 'edit') { saveEdit(); ev.preventDefault(); }
  } else if ((ev.ctrlKey || ev.metaKey) && ev.key === 'k') {
    document.getElementById('search').focus(); ev.preventDefault();
  }
});

loadProjects();
</script>
</body>
</html>
"""
