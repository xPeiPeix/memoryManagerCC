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

        def do_PUT(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/api/entry":
                self._serve_json({"error": "not found"}, 404)
                return
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

        def do_DELETE(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/api/entry":
                self._serve_json({"error": "not found"}, 404)
                return
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
  :root {
    --bg: #0a0a0a;
    --bg-dim: #141414;
    --bg-hi: #1d1d1d;
    --fg: #ffb000;
    --fg-dim: #b07700;
    --fg-mute: #666;
    --accent: #00ff66;
    --danger: #ff4040;
  }
  html, body { margin: 0; padding: 0; height: 100%;
    font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
    font-size: 13px; line-height: 1.5; }
  body { display: flex; flex-direction: column; height: 100vh;
    background: var(--bg); color: var(--fg); }
  header { background: var(--bg); color: var(--fg); padding: 8px 14px;
    display: flex; gap: 12px; align-items: center; flex-shrink: 0;
    border-bottom: 1px solid var(--fg-dim); }
  header .prompt { color: var(--accent); white-space: nowrap; }
  header .cursor { animation: blink 1s steps(2) infinite;
    display: inline-block; width: 8px; background: var(--fg); }
  @keyframes blink { 50% { opacity: 0; } }
  header input, header select { padding: 4px 8px;
    background: var(--bg-dim); color: var(--fg); border: 1px solid var(--fg-dim);
    font-family: inherit; font-size: inherit; }
  header input:focus, header select:focus { outline: none; border-color: var(--fg); }
  header input { flex: 1; min-width: 200px; }
  header .stat { font-size: 12px; color: var(--fg-mute); white-space: nowrap; }
  #app { flex: 1; display: flex; min-height: 0; overflow: hidden; }
  aside { width: 360px; flex-shrink: 0; overflow-y: auto;
    background: var(--bg); border-right: 1px solid var(--fg-dim); }
  aside .project { padding: 6px 12px; background: var(--bg-dim);
    border-bottom: 1px solid var(--fg-dim); cursor: pointer; user-select: none;
    color: var(--fg); }
  aside .project::before { content: '> '; color: var(--accent); }
  aside .project.collapsed::before { content: 'v '; }
  aside .project .count { color: var(--fg-mute); margin-left: 6px; }
  aside .project.collapsed + .entries { display: none; }
  aside .entries { padding: 0; }
  aside .entry { padding: 4px 12px 4px 24px; cursor: pointer;
    border-left: 2px solid transparent; color: var(--fg-dim); }
  aside .entry:hover { background: var(--bg-dim); color: var(--fg); }
  aside .entry.selected { background: var(--fg); color: var(--bg);
    border-left-color: var(--accent); }
  .badge { display: inline-block; margin-right: 6px;
    color: var(--fg); border: 1px solid var(--fg-dim); padding: 0 4px; }
  .entry.selected .badge { color: var(--bg); border-color: var(--bg); }
  main { flex: 1; padding: 16px 24px; overflow-y: auto; background: var(--bg); }
  main h1 { margin: 0 0 4px; font-size: 18px; color: var(--fg);
    border-bottom: 1px dashed var(--fg-dim); padding-bottom: 4px; }
  main .meta { color: var(--fg-mute); margin-bottom: 14px; font-size: 12px; }
  main .meta .row { margin: 4px 0; }
  main .meta .description { display: block; margin: 6px 0; padding: 4px 8px;
    border-left: 2px solid var(--accent); color: var(--fg); }
  main .toolbar { margin: 8px 0; display: flex; gap: 6px; flex-wrap: wrap; }
  main button, main .toolbar a { background: var(--bg); color: var(--fg);
    border: 1px solid var(--fg-dim); padding: 3px 10px; cursor: pointer;
    font-family: inherit; font-size: inherit; text-decoration: none; }
  main button:hover, main .toolbar a:hover { background: var(--fg); color: var(--bg); }
  main button.danger { color: var(--danger); border-color: var(--danger); }
  main button.danger:hover { background: var(--danger); color: var(--bg); }
  main .body { font-size: 13px; color: var(--fg); line-height: 1.7; }
  main .body h1, main .body h2, main .body h3 { color: var(--fg);
    border-bottom: 1px dashed var(--fg-dim); padding-bottom: 2px; }
  main .body h1 { font-size: 16px; margin-top: 18px; }
  main .body h2 { font-size: 14px; margin-top: 14px; }
  main .body h3 { font-size: 13px; margin-top: 12px; }
  main .body pre { background: var(--bg-dim); color: var(--fg);
    border: 1px dashed var(--fg-dim); padding: 8px; overflow-x: auto; }
  main .body :not(pre) > code { background: var(--bg-dim); color: var(--accent);
    padding: 0 4px; border: 1px dashed var(--fg-dim); }
  main .body table { border-collapse: collapse; }
  main .body th, main .body td { padding: 4px 8px;
    border: 1px solid var(--fg-dim); }
  main .body blockquote { border-left: 2px solid var(--fg-dim);
    padding-left: 10px; color: var(--fg-mute); margin-left: 0; }
  main .body a { color: var(--accent); }
  main .editor { display: flex; flex-direction: column; gap: 8px; }
  main .editor label { color: var(--fg-mute); font-size: 12px; }
  main .editor input, main .editor textarea { background: var(--bg-dim);
    color: var(--fg); border: 1px solid var(--fg-dim); padding: 4px 8px;
    font-family: inherit; font-size: inherit; }
  main .editor input:focus, main .editor textarea:focus {
    outline: none; border-color: var(--fg); }
  main .editor textarea { min-height: 360px; resize: vertical; line-height: 1.5; }
  main .confirm { padding: 12px; border: 1px solid var(--danger);
    background: var(--bg-dim); color: var(--danger); }
  main .confirm .cursor { animation: blink 1s steps(2) infinite;
    display: inline-block; width: 8px; background: var(--danger); }
  .empty { color: var(--fg-mute); text-align: center; margin-top: 80px; }
</style>
</head>
<body>
<header>
  <span class="prompt">mmcc@notepad:~$</span><span class="cursor">&nbsp;</span>
  <input id="search" placeholder="grep memory..." />
  <select id="type-filter">
    <option value="">[all types]</option>
    <option value="feedback">[F] feedback</option>
    <option value="user">[U] user</option>
    <option value="project">[P] project</option>
    <option value="reference">[R] reference</option>
    <option value="unknown">[?] unknown</option>
  </select>
  <span class="stat" id="stat"></span>
</header>
<div id="app">
  <aside id="tree"></aside>
  <main id="viewer"><div class="empty">// select a memory entry from the left tree</div></main>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
<script>
// PR #3 XSS guard: strip raw HTML token so memory bodies can't execute pasted <script>
marked.use({ renderer: { html(token) { return ''; } } });

const TYPE_GLYPH = { feedback: 'F', user: 'U', project: 'P', reference: 'R', unknown: '?' };

let projects = [];
let selected = null;
let mode = 'view';  // 'view' | 'edit' | 'confirm-delete'

async function loadProjects() {
  try {
    const r = await fetch('/api/projects');
    const data = await r.json();
    projects = data.projects || [];
    renderTree();
  } catch (e) {
    document.getElementById('tree').innerHTML = '<div class="empty">// load failed: ' + e.message + '</div>';
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
    projDiv.innerHTML = escapeHTML(p.short_id) + '<span class="count">(' + matched.length + ')</span>';
    projDiv.onclick = () => projDiv.classList.toggle('collapsed');
    tree.appendChild(projDiv);
    const entriesDiv = document.createElement('div');
    entriesDiv.className = 'entries';
    for (const e of matched) {
      const entryDiv = document.createElement('div');
      entryDiv.className = 'entry';
      entryDiv.dataset.path = e.file_path;
      const glyph = TYPE_GLYPH[e.type] || '?';
      entryDiv.innerHTML = '<span class="badge">' + glyph + '</span>' + escapeHTML(e.name);
      entryDiv.title = e.description || e.filename;
      entryDiv.onclick = () => selectEntry(e.file_path);
      entriesDiv.appendChild(entryDiv);
    }
    tree.appendChild(entriesDiv);
  }
  document.getElementById('stat').textContent = totalShown + ' entries / ' + projects.length + ' projects';
  if (totalShown === 0) {
    tree.innerHTML = '<div class="empty">// no matches</div>';
  }
  // re-highlight selected after re-render
  if (selected) {
    document.querySelectorAll('.entry').forEach(el => {
      el.classList.toggle('selected', el.dataset.path === selected.file_path);
    });
  }
}

async function selectEntry(filePath) {
  document.querySelectorAll('.entry').forEach(el => {
    el.classList.toggle('selected', el.dataset.path === filePath);
  });
  try {
    const r = await fetch('/api/entry?path=' + encodeURIComponent(filePath));
    if (!r.ok) {
      document.getElementById('viewer').innerHTML = '<div class="empty">// load failed: ' + r.status + '</div>';
      return;
    }
    const e = await r.json();
    selected = e;
    mode = 'view';
    renderEntry();
  } catch (err) {
    document.getElementById('viewer').innerHTML = '<div class="empty">// network error: ' + err.message + '</div>';
  }
}

function renderEntry() {
  const v = document.getElementById('viewer');
  v.innerHTML = '';
  if (!selected) { v.innerHTML = '<div class="empty">// no entry selected</div>'; return; }

  const e = selected;
  const h1 = document.createElement('h1');
  h1.textContent = '[' + (TYPE_GLYPH[e.type] || '?') + '] ' + e.name;
  v.appendChild(h1);

  const meta = document.createElement('div'); meta.className = 'meta';
  meta.innerHTML =
    '<div class="row">project: ' + escapeHTML(e.project_path) + '</div>' +
    '<div class="row">file: ' + escapeHTML(e.filename) + '</div>' +
    '<div class="row">type: ' + escapeHTML(e.type) + '</div>';
  if (e.description) {
    const d = document.createElement('span'); d.className = 'description';
    d.textContent = '// ' + e.description; meta.appendChild(d);
  }
  v.appendChild(meta);

  if (mode === 'view') { renderViewMode(v, e); }
  else if (mode === 'edit') { renderEditMode(v, e); }
  else if (mode === 'confirm-delete') { renderConfirmDelete(v, e); }
}

function renderViewMode(v, e) {
  const toolbar = document.createElement('div'); toolbar.className = 'toolbar';
  const btnEdit = document.createElement('button'); btnEdit.textContent = '[edit]';
  btnEdit.onclick = () => { mode = 'edit'; renderEntry(); };
  const btnDel = document.createElement('button'); btnDel.textContent = '[delete]';
  btnDel.className = 'danger';
  btnDel.onclick = () => { mode = 'confirm-delete'; renderEntry(); };
  const btnCopy = document.createElement('button'); btnCopy.textContent = '[copy path]';
  btnCopy.onclick = copyPath;
  const btnVS = document.createElement('a'); btnVS.textContent = '[open in vscode]';
  btnVS.href = 'vscode://file/' + e.file_path.replace(/\\/g, '/');
  toolbar.appendChild(btnEdit); toolbar.appendChild(btnDel);
  toolbar.appendChild(btnCopy); toolbar.appendChild(btnVS);
  v.appendChild(toolbar);

  const body = document.createElement('div'); body.className = 'body';
  body.innerHTML = marked.parse(e.body || '');
  v.appendChild(body);
}

function renderEditMode(v, e) {
  const editor = document.createElement('div'); editor.className = 'editor';

  const lblName = document.createElement('label'); lblName.textContent = 'name:';
  const inpName = document.createElement('input'); inpName.id = 'inp-name'; inpName.value = e.name;
  const lblDesc = document.createElement('label'); lblDesc.textContent = 'description:';
  const inpDesc = document.createElement('input'); inpDesc.id = 'inp-desc'; inpDesc.value = e.description || '';
  const lblBody = document.createElement('label'); lblBody.textContent = 'body (markdown):';
  const inpBody = document.createElement('textarea'); inpBody.id = 'inp-body'; inpBody.value = e.body || '';

  editor.appendChild(lblName); editor.appendChild(inpName);
  editor.appendChild(lblDesc); editor.appendChild(inpDesc);
  editor.appendChild(lblBody); editor.appendChild(inpBody);

  const toolbar = document.createElement('div'); toolbar.className = 'toolbar';
  const btnSave = document.createElement('button'); btnSave.textContent = '[save] (Ctrl+S)';
  btnSave.onclick = saveEdit;
  const btnCancel = document.createElement('button'); btnCancel.textContent = '[cancel] (Esc)';
  btnCancel.onclick = () => { mode = 'view'; renderEntry(); };
  toolbar.appendChild(btnSave); toolbar.appendChild(btnCancel);
  editor.appendChild(toolbar);

  v.appendChild(editor);
  setTimeout(() => inpName.focus(), 0);
}

function renderConfirmDelete(v, e) {
  const c = document.createElement('div'); c.className = 'confirm';
  c.innerHTML = 'rm ' + escapeHTML(e.file_path) + '? [y/N]<span class="cursor">&nbsp;</span>';
  v.appendChild(c);
  const toolbar = document.createElement('div'); toolbar.className = 'toolbar';
  const btnYes = document.createElement('button'); btnYes.textContent = '[y] yes, delete';
  btnYes.className = 'danger'; btnYes.onclick = confirmDelete;
  const btnNo = document.createElement('button'); btnNo.textContent = '[n] cancel';
  btnNo.onclick = () => { mode = 'view'; renderEntry(); };
  toolbar.appendChild(btnYes); toolbar.appendChild(btnNo);
  v.appendChild(toolbar);
}

async function saveEdit() {
  if (!selected) return;
  const payload = {
    path: selected.file_path,
    name: document.getElementById('inp-name').value,
    description: document.getElementById('inp-desc').value,
    body: document.getElementById('inp-body').value,
  };
  try {
    const r = await fetch('/api/entry', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert('save failed: ' + r.status + ' ' + (err.error || ''));
      return;
    }
    const updated = await r.json();
    selected = updated;
    mode = 'view';
    renderEntry();
    loadProjects();
  } catch (err) {
    alert('network error: ' + err.message);
  }
}

async function confirmDelete() {
  if (!selected) return;
  const payload = { path: selected.file_path };
  try {
    const r = await fetch('/api/entry', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert('delete failed: ' + r.status + ' ' + (err.error || ''));
      return;
    }
    selected = null;
    mode = 'view';
    document.getElementById('viewer').innerHTML = '<div class="empty">// entry deleted</div>';
    loadProjects();
  } catch (err) {
    alert('network error: ' + err.message);
  }
}

function escapeHTML(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function copyPath() {
  if (!selected) return;
  navigator.clipboard.writeText(selected.file_path).then(
    () => { /* silent */ },
    () => alert('copy failed: select manually')
  );
}

document.getElementById('search').addEventListener('input', renderTree);
document.getElementById('type-filter').addEventListener('change', renderTree);

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') {
    if (mode === 'edit' || mode === 'confirm-delete') {
      mode = 'view';
      renderEntry();
      ev.preventDefault();
    }
  } else if (ev.ctrlKey && ev.key === 's') {
    if (mode === 'edit') {
      saveEdit();
      ev.preventDefault();
    }
  } else if (mode === 'confirm-delete') {
    if (ev.key === 'y' || ev.key === 'Y') { confirmDelete(); ev.preventDefault(); }
    else if (ev.key === 'n' || ev.key === 'N') {
      mode = 'view'; renderEntry(); ev.preventDefault();
    }
  }
});

loadProjects();
</script>
</body>
</html>
"""
