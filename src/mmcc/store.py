from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _atomic_write(file_path: Path, content: str) -> None:
    # 同卷 tempfile + os.replace 防半写文件: 进程崩溃时原文件保持完整
    parent = file_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".mmcc_tmp_", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, file_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

from .paths import (
    PROJECTS_DIR,
    decode_project_dir,
    is_worktree_alias,
    longest_common_prefix,
    project_short_id,
)

VALID_TYPES = {"feedback", "user", "project", "reference"}


class MemoryStoreError(Exception):
    pass


class NotFoundError(MemoryStoreError):
    pass


class AmbiguousRefError(MemoryStoreError):
    def __init__(self, ref: str, candidates: list["MemoryEntry"]):
        super().__init__(f"Ambiguous ref {ref!r}: {len(candidates)} candidates")
        self.ref = ref
        self.candidates = candidates


class EntryExistsError(MemoryStoreError):
    pass


@dataclass(frozen=True)
class MemoryEntry:
    project_id: str
    project_path: str
    filename: str
    file_path: Path
    name: str
    description: str
    type: str
    origin_session_id: Optional[str]
    body: str
    mtime: float


@dataclass(frozen=True)
class ProjectInfo:
    project_id: str
    project_path: str
    memory_dir: Optional[Path]
    is_alias: bool
    entry_count: int
    has_index: bool
    mtime: float


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_KEY_RE = re.compile(r"^([\w-]+):\s*(.*)$")


def _parse_frontmatter(text: str) -> Optional[tuple[dict[str, str], str]]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        km = _KEY_RE.match(line)
        if km:
            key = km.group(1).strip()
            val = km.group(2).strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            fm[key] = val
    return fm, m.group(2)


def _name_from_filename(filename: str) -> str:
    stem = filename[:-3] if filename.endswith(".md") else filename
    for prefix in ("feedback_", "user_", "project_", "reference_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return stem.replace("_", " ")


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^\w]", "", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


class MemoryStore:
    def __init__(self, projects_dir: Path | str = PROJECTS_DIR):
        self.projects_dir = Path(projects_dir)
        self._common_prefix_cache: Optional[str] = None
        self._short_ids_cache: Optional[dict[str, str]] = None

    def parse_entry(self, file_path: Path) -> Optional[MemoryEntry]:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, PermissionError):
            return None
        parsed = _parse_frontmatter(text)
        if parsed is None:
            return None
        fm, body = parsed
        project_dir = file_path.parent.parent
        project_id = project_dir.name
        decoded = decode_project_dir(project_id) or project_id
        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        type_val = fm.get("type", "").lower()
        if type_val not in VALID_TYPES:
            type_val = "unknown"
        return MemoryEntry(
            project_id=project_id,
            project_path=decoded,
            filename=file_path.name,
            file_path=file_path,
            name=fm.get("name") or _name_from_filename(file_path.name),
            description=fm.get("description", ""),
            type=type_val,
            origin_session_id=fm.get("originSessionId") or None,
            body=body,
            mtime=mtime,
        )

    def list_projects(self, *, include_aliases: bool = False, include_empty: bool = False) -> list[ProjectInfo]:
        infos: list[ProjectInfo] = []
        if not self.projects_dir.exists():
            return infos
        for proj_dir in sorted(self.projects_dir.iterdir(), key=lambda p: p.name):
            if not proj_dir.is_dir():
                continue
            mem_dir = proj_dir / "memory"
            if not mem_dir.exists():
                continue
            is_alias = is_worktree_alias(proj_dir)
            if is_alias and not include_aliases:
                continue
            info = self._build_project_info(proj_dir, mem_dir, is_alias)
            if info is None:
                continue
            if not include_empty and info.entry_count == 0:
                continue
            infos.append(info)
        infos.sort(key=lambda p: -p.mtime)
        return infos

    def _build_project_info(self, proj_dir: Path, mem_dir: Path, is_alias: bool) -> Optional[ProjectInfo]:
        try:
            real_mem = mem_dir.resolve()
        except OSError:
            return None
        entry_count = 0
        has_index = False
        latest_mtime = 0.0
        try:
            for f in real_mem.iterdir():
                if not f.is_file() or not f.name.endswith(".md"):
                    continue
                if f.name == "MEMORY.md":
                    has_index = True
                    continue
                entry_count += 1
                try:
                    latest_mtime = max(latest_mtime, f.stat().st_mtime)
                except OSError:
                    pass
        except (OSError, PermissionError):
            return None
        project_id = proj_dir.name
        return ProjectInfo(
            project_id=project_id,
            project_path=decode_project_dir(project_id) or project_id,
            memory_dir=real_mem,
            is_alias=is_alias,
            entry_count=entry_count,
            has_index=has_index,
            mtime=latest_mtime,
        )

    def list_entries(self,
                     *,
                     project_id: Optional[str] = None,
                     type_filter: Optional[str] = None) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for proj in self.list_projects(include_aliases=False, include_empty=True):
            if project_id is not None and not self._project_matches(proj, project_id):
                continue
            if proj.memory_dir is None:
                continue
            try:
                files = sorted(proj.memory_dir.iterdir(), key=lambda f: f.name)
            except (OSError, PermissionError):
                continue
            for f in files:
                if not f.is_file() or not f.name.endswith(".md") or f.name == "MEMORY.md":
                    continue
                entry = self.parse_entry(f)
                if entry is None:
                    continue
                if type_filter is not None and entry.type != type_filter:
                    continue
                entries.append(entry)
        entries.sort(key=lambda e: -e.mtime)
        return entries

    def _project_matches(self, proj: ProjectInfo, query: str) -> bool:
        if proj.project_id == query:
            return True
        if self.short_id(proj.project_id) == query:
            return True
        q = query.lower()
        return q in proj.project_id.lower() or q in proj.project_path.lower()

    def common_prefix(self) -> str:
        if self._common_prefix_cache is not None:
            return self._common_prefix_cache
        if not self.projects_dir.exists():
            self._common_prefix_cache = ""
            return ""
        ids = [d.name for d in self.projects_dir.iterdir() if d.is_dir()]
        if len(ids) < 2:
            self._common_prefix_cache = ""
            return ""
        from collections import Counter
        groups = Counter(i[:3] for i in ids if len(i) >= 3)
        if not groups:
            self._common_prefix_cache = ""
            return ""
        majority, _ = groups.most_common(1)[0]
        target = [i for i in ids if i.startswith(majority)]
        target = [i for i in target if not any(j != i and j.startswith(i + "-") for j in target)]
        self._common_prefix_cache = longest_common_prefix(target) if len(target) >= 2 else ""
        return self._common_prefix_cache

    def short_id(self, project_id: str) -> str:
        sids = self._short_ids()
        return sids.get(project_id, project_short_id(project_id, self.common_prefix()))

    def _short_ids(self) -> dict[str, str]:
        if self._short_ids_cache is not None:
            return self._short_ids_cache
        lcp = self.common_prefix()
        ids: list[str] = []
        if self.projects_dir.exists():
            try:
                for d in self.projects_dir.iterdir():
                    if d.is_dir():
                        ids.append(d.name)
            except (OSError, PermissionError):
                pass
        layer1: dict[str, str] = {}
        for pid in ids:
            if lcp and pid.startswith(lcp):
                rest = pid[len(lcp):]
                layer1[pid] = rest if rest else pid
            else:
                layer1[pid] = pid
        cats = self._compute_categories(list(layer1.values())) if lcp else []
        layer2: dict[str, str] = {}
        for pid, l1 in layer1.items():
            stripped: Optional[str] = None
            for cat in cats:
                if l1.startswith(cat):
                    tail = l1[len(cat):]
                    if tail:
                        stripped = tail
                        break
            layer2[pid] = stripped if stripped is not None else l1
        if cats and len(set(layer2.values())) != len(layer2):
            self._short_ids_cache = layer1
        else:
            self._short_ids_cache = layer2
        return self._short_ids_cache

    def _compute_categories(self, layer1_values: list[str]) -> list[str]:
        from collections import defaultdict
        groups: dict[str, list[str]] = defaultdict(list)
        for v in layer1_values:
            head = v.split("-", 1)[0]
            groups[head].append(v)
        cats: list[str] = []
        for items in groups.values():
            if len(items) < 2:
                continue
            items_filtered = [i for i in items if not any(j != i and j.startswith(i + "-") for j in items)]
            if len(items_filtered) < 2:
                continue
            gprefix = longest_common_prefix(items_filtered)
            if not gprefix:
                continue
            if not gprefix.endswith("-"):
                idx = gprefix.rfind("-")
                if idx < 0:
                    continue
                gprefix = gprefix[: idx + 1]
            cats.append(gprefix)
        cats.sort(key=len, reverse=True)
        return cats

    def search(self, keyword: str, *,
               in_body: bool = True,
               in_title: bool = True,
               in_description: bool = True,
               type_filter: Optional[str] = None,
               project_id: Optional[str] = None,
               case_sensitive: bool = False,
               all_words: bool = False,
               fuzzy: bool = False) -> list[tuple[MemoryEntry, list[str]]]:
        if all_words and fuzzy:
            raise ValueError("all_words and fuzzy are mutually exclusive")
        if not (in_body or in_title or in_description):
            in_body = in_title = in_description = True
        flags = 0 if case_sensitive else re.IGNORECASE
        if fuzzy:
            import difflib
            kw = keyword if case_sensitive else keyword.lower()
            if not kw.strip():
                return []
            results: list[tuple[MemoryEntry, list[str]]] = []
            for entry in self.list_entries(project_id=project_id, type_filter=type_filter):
                haystacks: list[str] = []
                if in_title:
                    haystacks.append(entry.name)
                if in_description and entry.description:
                    haystacks.append(entry.description)
                if in_body:
                    haystacks.append(entry.body)
                text = "\n".join(haystacks)
                tokens = re.findall(r"[\w-]+", text)
                if not tokens:
                    continue
                tokens_norm = tokens if case_sensitive else [t.lower() for t in tokens]
                close = difflib.get_close_matches(kw, tokens_norm, n=1, cutoff=0.7)
                if not close:
                    continue
                matched_norm = close[0]
                try:
                    idx = tokens_norm.index(matched_norm)
                    matched_token = tokens[idx]
                except ValueError:
                    matched_token = matched_norm
                snippet = matched_token
                for line in text.splitlines():
                    if matched_token in line:
                        snippet = line.strip()
                        break
                results.append((entry, [f"~ {snippet}"]))
            results.sort(key=lambda r: (0 if r[0].type == "feedback" else 1, -r[0].mtime))
            return results
        if all_words:
            words = keyword.split()
            if not words:
                return []
            try:
                word_res = [re.compile(re.escape(w), flags) for w in words]
            except re.error:
                return []
            results: list[tuple[MemoryEntry, list[str]]] = []
            for entry in self.list_entries(project_id=project_id, type_filter=type_filter):
                haystacks: list[str] = []
                if in_title:
                    haystacks.append(entry.name)
                if in_description and entry.description:
                    haystacks.append(entry.description)
                if in_body:
                    haystacks.append(entry.body)
                combined = "\n".join(haystacks)
                if not all(wr.search(combined) for wr in word_res):
                    continue
                matched: list[str] = []
                seen: set[int] = set()
                for line in combined.splitlines():
                    if len(matched) >= 3:
                        break
                    for i, wr in enumerate(word_res):
                        if i in seen:
                            continue
                        if wr.search(line):
                            matched.append(line.strip())
                            seen.add(i)
                            break
                results.append((entry, matched[:3]))
            results.sort(key=lambda r: (0 if r[0].type == "feedback" else 1, -r[0].mtime))
            return results
        try:
            kw_re = re.compile(re.escape(keyword), flags)
        except re.error:
            return []
        results = []
        for entry in self.list_entries(project_id=project_id, type_filter=type_filter):
            matched = []
            hit = False
            if in_title and kw_re.search(entry.name):
                matched.append(f"name: {entry.name}")
                hit = True
            if in_description and entry.description and kw_re.search(entry.description):
                matched.append(f"description: {entry.description}")
                hit = True
            if in_body:
                body_hits = 0
                for line in entry.body.splitlines():
                    if kw_re.search(line):
                        matched.append(line.strip())
                        body_hits += 1
                        hit = True
                        if body_hits >= 3:
                            break
            if hit:
                results.append((entry, matched[:3]))
        results.sort(key=lambda r: (0 if r[0].type == "feedback" else 1, -r[0].mtime))
        return results

    def _resolve_project_dir(self, project_id: str) -> Optional[Path]:
        if not self.projects_dir.exists():
            return None
        direct = self.projects_dir / project_id
        if direct.is_dir():
            return direct
        try:
            entries = list(self.projects_dir.iterdir())
        except (OSError, PermissionError):
            return None
        for d in entries:
            if not d.is_dir():
                continue
            if self.short_id(d.name) == project_id:
                return d
        q = project_id.lower()
        candidates: list[Path] = []
        for d in entries:
            if not d.is_dir():
                continue
            decoded = decode_project_dir(d.name) or d.name
            if q in d.name.lower() or q in decoded.lower():
                candidates.append(d)
        if len(candidates) == 1:
            return candidates[0]
        return None

    def add_entry(self,
                  project_id: str,
                  name: str,
                  type: str,
                  description: str,
                  body: str,
                  *,
                  origin_session_id: Optional[str] = None) -> Path:
        if type not in VALID_TYPES:
            raise ValueError(f"Invalid type: {type!r} (must be one of {sorted(VALID_TYPES)})")
        slug = _slugify(name)
        if not slug:
            raise ValueError(f"Name {name!r} produces empty slug")
        project_dir = self._resolve_project_dir(project_id)
        if project_dir is None:
            raise NotFoundError(f"Project not found: {project_id!r}")
        mem_dir = project_dir / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{type}_{slug}.md"
        file_path = mem_dir / filename
        if file_path.exists():
            raise EntryExistsError(f"Entry already exists: {file_path}")
        fm_lines = [
            "---",
            f"name: {name}",
            f"description: {description}",
            f"type: {type}",
        ]
        if origin_session_id:
            fm_lines.append(f"originSessionId: {origin_session_id}")
        fm_lines.append("---")
        body_text = body if body.endswith("\n") else body + "\n"
        content = "\n".join(fm_lines) + "\n" + body_text
        _atomic_write(file_path, content)
        self._short_ids_cache = None
        self._common_prefix_cache = None
        return file_path

    def update_entry(self,
                     file_path: Path,
                     *,
                     name: Optional[str] = None,
                     description: Optional[str] = None,
                     type: Optional[str] = None,
                     body: Optional[str] = None) -> MemoryEntry:
        if body is not None and not isinstance(body, str):
            raise ValueError(f"body must be string or None, got {body.__class__.__name__}")
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"Cannot read {file_path}: {e}") from e
        parsed = _parse_frontmatter(text)
        if parsed is None:
            raise ValueError(f"Cannot parse frontmatter: {file_path}")
        fm, parsed_body = parsed
        if type is not None and type not in VALID_TYPES:
            raise ValueError(f"Invalid type: {type!r} (must be one of {sorted(VALID_TYPES)})")
        if name is not None:
            fm["name"] = name
        if description is not None:
            fm["description"] = description
        if type is not None:
            fm["type"] = type
        new_body = parsed_body if body is None else (body if body.endswith("\n") else body + "\n")
        lines = ["---"]
        for k, v in fm.items():
            lines.append(f"{k}: {v}")
        lines.append("---")
        new_text = "\n".join(lines) + "\n" + new_body
        _atomic_write(file_path, new_text)
        entry = self.parse_entry(file_path)
        if entry is None:
            raise ValueError(f"Failed to re-parse after update: {file_path}")
        return entry

    def delete_entry(self, file_path: Path) -> None:
        # mmcc memory entry 必须形如 <project>/memory/*.md，避免误删 sessions/*.jsonl 等
        if file_path.parent.name != "memory" or file_path.suffix != ".md":
            raise ValueError(f"Not a memory entry: {file_path}")
        file_path.unlink()
        self._short_ids_cache = None
        self._common_prefix_cache = None

    def find(self, ref: str) -> MemoryEntry:
        if ":" in ref:
            project_part, filename = ref.split(":", 1)
            target = filename if filename.endswith(".md") else filename + ".md"
            entries = self.list_entries(project_id=project_part)
            candidates = [e for e in entries if e.filename == target]
        else:
            target = ref if ref.endswith(".md") else ref + ".md"
            candidates = [e for e in self.list_entries() if e.filename == target]
        if not candidates:
            raise NotFoundError(f"Memory not found: {ref}")
        if len(candidates) > 1:
            raise AmbiguousRefError(ref, candidates)
        return candidates[0]
