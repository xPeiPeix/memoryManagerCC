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

from .store import MemoryStore


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
        grouped[p.project_id] = {
            "project_id": p.project_id,
            "short_id": store.short_id(p.project_id),
            "project_path": p.project_path,
            "entry_count": p.entry_count,
            "mtime": p.mtime,
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
            else:
                self._serve_json({"error": "not found"}, 404)

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

<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; font-family: ui-sans-serif, system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
  body { display: flex; flex-direction: column; height: 100vh; color: #222; }
  header { background: #1f2937; color: #f9fafb; padding: 10px 16px; display: flex; gap: 10px; align-items: center; flex-shrink: 0; }
  header strong { font-size: 15px; }
  header input, header select { padding: 6px 10px; border: 1px solid #374151; background: #111827; color: #f9fafb; border-radius: 4px; font-size: 13px; }
  header input { flex: 1; min-width: 200px; }
  header .stat { font-size: 12px; color: #9ca3af; }
  #app { flex: 1; display: flex; min-height: 0; overflow: hidden; }
  aside { width: 340px; flex-shrink: 0; overflow-y: auto; border-right: 1px solid #e5e7eb; background: #f9fafb; }
  aside .project { font-weight: 600; padding: 9px 14px; background: #e5e7eb; border-bottom: 1px solid #d1d5db; font-size: 13px; cursor: pointer; user-select: none; }
  aside .project .count { color: #6b7280; font-weight: normal; font-size: 11px; margin-left: 6px; }
  aside .project.collapsed + .entries { display: none; }
  aside .entries { padding: 2px 0; }
  aside .entry { padding: 6px 14px 6px 28px; cursor: pointer; font-size: 13px; border-left: 3px solid transparent; }
  aside .entry:hover { background: #e0e7ff; }
  aside .entry.selected { background: #c7d2fe; border-left-color: #4f46e5; }
  .badge { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 3px; margin-right: 6px; color: #fff; vertical-align: 1px; }
  .badge-feedback { background: #dc2626; }
  .badge-user { background: #0284c7; }
  .badge-project { background: #16a34a; }
  .badge-reference { background: #d97706; }
  .badge-unknown { background: #6b7280; }
  main { flex: 1; padding: 28px 36px; overflow-y: auto; background: #fff; }
  main h1 { margin: 0 0 6px; font-size: 22px; }
  main .meta { color: #6b7280; font-size: 13px; margin-bottom: 18px; }
  main .meta code { background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
  main .meta button { margin-right: 6px; padding: 4px 10px; border: 1px solid #d1d5db; background: #fff; cursor: pointer; border-radius: 4px; font-size: 12px; }
  main .meta button:hover { background: #f3f4f6; }
  main .meta .description { display: block; margin: 8px 0; padding: 8px 12px; background: #fffbeb; border-left: 3px solid #f59e0b; color: #444; }
  main .body { line-height: 1.75; font-size: 14px; }
  main .body h1 { font-size: 20px; margin-top: 22px; }
  main .body h2 { font-size: 17px; margin-top: 18px; }
  main .body h3 { font-size: 15px; margin-top: 14px; }
  main .body pre { background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 6px; overflow-x: auto; }
  main .body :not(pre) > code { background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-size: 13px; color: #c026d3; }
  main .body table { border-collapse: collapse; }
  main .body th, main .body td { padding: 6px 10px; border: 1px solid #e5e7eb; }
  main .body th { background: #f9fafb; }
  main .body blockquote { border-left: 3px solid #d1d5db; padding-left: 12px; color: #6b7280; margin-left: 0; }
  .empty { color: #9ca3af; text-align: center; margin-top: 80px; font-size: 14px; }
</style>
</head>
<body>
<header>
  <strong>mmcc notepad</strong>
  <input id="search" placeholder="搜索 name / description / 文件名..." />
  <select id="type-filter">
    <option value="">所有类型</option>
    <option value="feedback">feedback</option>
    <option value="user">user</option>
    <option value="project">project</option>
    <option value="reference">reference</option>
    <option value="unknown">unknown</option>
  </select>
  <span class="stat" id="stat"></span>
</header>
<div id="app">
  <aside id="tree"></aside>
  <main id="viewer"><div class="empty">从左侧选择一条 memory</div></main>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
<script>
// 防 XSS：strip raw HTML token，避免 memory 里贴的 <script> 等代码示例被意外执行
marked.use({ renderer: { html(token) { return ''; } } });

let projects = [];
let selected = null;

async function loadProjects() {
  try {
    const r = await fetch('/api/projects');
    const data = await r.json();
    projects = data.projects || [];
    renderTree();
  } catch (e) {
    document.getElementById('tree').innerHTML = '<div class="empty">加载失败：' + e.message + '</div>';
  }
}

function renderTree() {
  const search = document.getElementById('search').value.toLowerCase().trim();
  const typeFilter = document.getElementById('type-filter').value;
  const tree = document.getElementById('tree');
  tree.innerHTML = '';
  let totalShown = 0;
  for (const p of projects) {
    const matched = p.entries.filter(e => {
      if (typeFilter && e.type !== typeFilter) return false;
      if (!search) return true;
      const hay = (e.name + ' ' + e.description + ' ' + e.filename).toLowerCase();
      return hay.includes(search);
    });
    if (matched.length === 0) continue;
    totalShown += matched.length;
    const projDiv = document.createElement('div');
    projDiv.className = 'project';
    projDiv.innerHTML = escapeHTML(p.short_id) + '<span class="count">' + matched.length + '</span>';
    projDiv.onclick = () => projDiv.classList.toggle('collapsed');
    tree.appendChild(projDiv);
    const entriesDiv = document.createElement('div');
    entriesDiv.className = 'entries';
    for (const e of matched) {
      const entryDiv = document.createElement('div');
      entryDiv.className = 'entry';
      entryDiv.dataset.path = e.file_path;
      entryDiv.innerHTML = '<span class="badge badge-' + escapeHTML(e.type) + '">' + escapeHTML(e.type) + '</span>' + escapeHTML(e.name);
      entryDiv.title = e.description || e.filename;
      entryDiv.onclick = () => selectEntry(e.file_path);
      entriesDiv.appendChild(entryDiv);
    }
    tree.appendChild(entriesDiv);
  }
  document.getElementById('stat').textContent = totalShown + ' 条 / ' + projects.length + ' 项目';
  if (totalShown === 0) {
    tree.innerHTML = '<div class="empty">无匹配结果</div>';
  }
}

async function selectEntry(filePath) {
  document.querySelectorAll('.entry').forEach(el => {
    el.classList.toggle('selected', el.dataset.path === filePath);
  });
  try {
    const r = await fetch('/api/entry?path=' + encodeURIComponent(filePath));
    if (!r.ok) {
      document.getElementById('viewer').innerHTML = '<div class="empty">加载失败 ' + r.status + '</div>';
      return;
    }
    const e = await r.json();
    selected = e;
    renderEntry(e);
  } catch (err) {
    document.getElementById('viewer').innerHTML = '<div class="empty">网络错误：' + err.message + '</div>';
  }
}

function renderEntry(e) {
  const v = document.getElementById('viewer');
  v.innerHTML = '';
  const h1 = document.createElement('h1'); h1.textContent = e.name; v.appendChild(h1);
  const meta = document.createElement('div'); meta.className = 'meta';
  meta.innerHTML =
    '<span class="badge badge-' + escapeHTML(e.type) + '">' + escapeHTML(e.type) + '</span> · ' +
    '<span>' + escapeHTML(e.project_path) + '</span> · ' +
    '<code>' + escapeHTML(e.filename) + '</code>';
  if (e.description) {
    const d = document.createElement('span'); d.className = 'description'; d.textContent = e.description; meta.appendChild(d);
  }
  const btnCopy = document.createElement('button'); btnCopy.textContent = '复制路径'; btnCopy.onclick = copyPath;
  const btnVS = document.createElement('button'); btnVS.textContent = '在 VSCode 打开'; btnVS.onclick = openInVSCode;
  meta.appendChild(document.createElement('br'));
  meta.appendChild(btnCopy);
  meta.appendChild(btnVS);
  v.appendChild(meta);
  const body = document.createElement('div'); body.className = 'body';
  body.innerHTML = marked.parse(e.body || '');
  v.appendChild(body);
}

function escapeHTML(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function copyPath() {
  if (!selected) return;
  navigator.clipboard.writeText(selected.file_path).then(
    () => alert('已复制：' + selected.file_path),
    () => alert('复制失败，请手动选择')
  );
}

function openInVSCode() {
  if (!selected) return;
  const fp = selected.file_path.replace(/\\/g, '/');
  window.location.href = 'vscode://file/' + fp;
}

document.getElementById('search').addEventListener('input', renderTree);
document.getElementById('type-filter').addEventListener('change', renderTree);

loadProjects();
</script>
</body>
</html>
"""
