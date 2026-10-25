"""
工具模块 - 伴生体的"手"
第八阶段实现
伴生体不能直接 os.system(...)，只能调用规定好的工具
所有操作受 permissions.json 限制
"""

import json
import os
import tempfile
from pathlib import Path
from chen.paths import PROJECT_ROOT


class Tools:
    """工具系统 - 伴生体可调用的受限操作集合"""

    MAX_READ_BYTES = 4 * 1024 * 1024
    MAX_WRITE_BYTES = 4 * 1024 * 1024

    def __init__(self, workspace_dir="workspace", permissions_path="system/permissions.json"):
        self.base_dir = PROJECT_ROOT
        self.workspace_dir = self.base_dir / workspace_dir
        self.workspace_dir = self.workspace_dir.resolve()
        self.permissions = self._load_permissions(permissions_path)

    def _load_permissions(self, path):
        """加载权限配置"""
        full_path = self.base_dir / path
        if full_path.exists():
            with open(full_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _check_permission(self, tool_name):
        """检查工具是否有权限使用"""
        tool_perms = self.permissions.get("tools", {})
        perm = tool_perms.get(tool_name, False)
        if perm == "need_confirm":
            # 需要用户确认
            return "confirm_needed"
        return perm is True

    def create_file(self, filename, content=""):
        """在 workspace 中创建文件"""
        if self._check_permission("create_file") is not True:
            return "权限不足：无法创建文件"
        return self._write_file(filename, content)

    def _write_file(self, filename, content):
        """在 workspace 内原子写入文件，避免崩溃留下半个 JSON/文本文件。"""
        if not isinstance(content, str):
            return "参数错误：文件内容必须是字符串"
        if len(content.encode("utf-8")) > self.MAX_WRITE_BYTES:
            return "安全限制：文件内容过大，拒绝写入"
        file_path = self._workspace_path(filename)
        if file_path is None:
            return "安全限制：只能在 workspace 目录内操作"
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return "无法创建文件目录"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=file_path.parent,
                prefix=f".{file_path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, file_path)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return "无法写入文件"
        return f"已创建文件：{filename}"

    def read_file(self, filename):
        """读取 workspace 中的文件"""
        if self._check_permission("read_file") is not True:
            return "权限不足：无法读取文件"
        file_path = self._workspace_path(filename)
        if file_path is None:
            return "安全限制：只能读取 workspace 目录内文件"
        if not file_path.exists():
            return f"文件不存在：{filename}"
        if not file_path.is_file():
            return "路径不是文件"
        try:
            if file_path.stat().st_size > self.MAX_READ_BYTES:
                return "安全限制：文件过大，拒绝一次性读取"
        except OSError:
            return "无法读取文件状态"
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def write_file(self, filename, content):
        """写入（覆盖）workspace 中的文件"""
        if self._check_permission("write_file") is not True:
            return "权限不足：无法写入文件"
        return self._write_file(filename, content)

    def create_folder(self, folder_name):
        """在 workspace 中创建文件夹"""
        if self._check_permission("create_folder") is not True:
            return "权限不足：无法创建文件夹"
        folder_path = self._workspace_path(folder_name)
        if folder_path is None:
            return "安全限制：只能在 workspace 目录内操作"
        try:
            folder_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return "无法创建文件夹"
        return f"已创建文件夹：{folder_name}"

    def list_workspace(self):
        """列出 workspace 内容"""
        if self._check_permission("read_file") is not True:
            return "权限不足：无法列出文件"
        if not self.workspace_dir.exists():
            return "workspace 为空"
        items = []
        for item in self.workspace_dir.iterdir():
            items.append(f"{'[目录]' if item.is_dir() else '[文件]'} {item.name}")
        return "\n".join(items) if items else "workspace 为空"

    def _workspace_path(self, filename):
        """解析并校验 workspace 路径，防止 ../ 和符号链接逃逸。"""
        try:
            path = (self.workspace_dir / str(filename)).resolve()
            path.relative_to(self.workspace_dir)
            return path
        except (OSError, ValueError):
            return None

    def search_workspace(self, query, max_results=30):
        """只搜索 workspace 内文本文件名和文本内容，不遍历其他目录。"""
        if self._check_permission("read_file") is not True:
            return {"ok": False, "error": "权限不足：无法搜索文件"}
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "error": "请输入搜索内容"}
        max_results = max(1, min(int(max_results), 100))
        matches = []
        allowed = {".txt", ".md", ".json", ".csv", ".py", ".toml", ".yaml", ".yml"}
        if not self.workspace_dir.exists():
            return {"ok": True, "matches": []}
        for path in self.workspace_dir.rglob("*"):
            if len(matches) >= max_results:
                break
            try:
                resolved = path.resolve()
                resolved.relative_to(self.workspace_dir)
                if not resolved.is_file() or resolved.suffix.lower() not in allowed:
                    continue
                if resolved.stat().st_size > self.MAX_READ_BYTES:
                    continue
                if query.casefold() in resolved.name.casefold():
                    matches.append({"path": str(resolved.relative_to(self.workspace_dir)), "line": None})
                    continue
                with resolved.open("r", encoding="utf-8", errors="replace") as stream:
                    for line_no, line in enumerate(stream, 1):
                        if query.casefold() in line.casefold():
                            matches.append({"path": str(resolved.relative_to(self.workspace_dir)),
                                            "line": line_no, "text": line.strip()[:240]})
                            if len(matches) >= max_results:
                                break
            except (OSError, ValueError):
                continue
        return {"ok": True, "matches": matches, "truncated": len(matches) >= max_results}

    def _chen_notes_dir(self) -> Path:
        """伴生体自己的专属随笔与整理目录（严格限制在 workspace/chen_notes 内）。"""
        notes_dir = (self.workspace_dir / "chen_notes").resolve()
        notes_dir.relative_to(self.workspace_dir)
        notes_dir.mkdir(parents=True, exist_ok=True)
        return notes_dir

    def _chen_note_path(self, filename: str):
        """校验文件名只能落在 workspace/chen_notes/ 内部，禁止覆盖用户根目录文件。"""
        raw = str(filename or "").strip().replace("\\", "/")
        if raw.startswith("chen_notes/"):
            raw = raw[len("chen_notes/"):]
        if not raw or "/" in raw or raw in {".", ".."}:
            return None
        try:
            notes_dir = self._chen_notes_dir()
            target = (notes_dir / raw).resolve()
            target.relative_to(notes_dir)
            return target
        except (OSError, ValueError):
            return None

    def write_chen_note(self, content="", title="", filename=""):
        """在 workspace/chen_notes/ 下写入伴生体自己的随笔或记录。"""
        if self._check_permission("write_file") is not True:
            return {"ok": False, "error": "权限不足：无法写入随笔"}
        text = str(content or "").strip()
        if len(text) < 2:
            return {"ok": False, "error": "随笔内容为空"}
        from datetime import datetime
        now = datetime.now()
        safe_title = str(title or "").strip().replace("\n", " ")[:40] or f"随笔 · {now:%m.%d %H:%M}"
        if not filename:
            filename = f"note_{now:%Y%m%d_%H%M%S}.md"
        if not str(filename).endswith((".md", ".txt")):
            filename = f"{filename}.md"
        target = self._chen_note_path(filename)
        if target is None:
            return {"ok": False, "error": "安全限制：只能在 workspace/chen_notes 目录内写随笔"}
        body = f"# {safe_title}\n\n> {now:%Y-%m-%d %H:%M}\n\n{text}\n"
        if len(body.encode("utf-8")) > self.MAX_WRITE_BYTES:
            return {"ok": False, "error": "随笔内容过大"}
        rel = f"chen_notes/{target.name}"
        msg = self._write_file(rel, body)
        if not str(msg).startswith("已创建文件"):
            return {"ok": False, "error": str(msg)}
        return {
            "ok": True,
            "path": rel,
            "filename": target.name,
            "title": safe_title,
            "preview": text[:60],
            "bytes": len(body.encode("utf-8")),
        }

    def organize_chen_notes(self):
        """整理 workspace/chen_notes/ 下的随笔，生成或更新目录索引 index.md。"""
        if self._check_permission("write_file") is not True or self._check_permission("read_file") is not True:
            return {"ok": False, "error": "权限不足：无法整理随笔目录"}
        try:
            notes_dir = self._chen_notes_dir()
        except (OSError, ValueError):
            return {"ok": False, "error": "无法访问随笔目录"}
        entries = []
        for path in sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.name.lower() == "index.md":
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            title = path.stem
            stamp = ""
            preview = ""
            for ln in lines:
                if ln.startswith("#"):
                    title = ln.lstrip("#").strip()[:40] or title
                elif ln.startswith(">") and not stamp:
                    stamp = ln.lstrip(">").strip()[:24]
                elif not preview:
                    preview = ln[:50]
            entries.append({
                "filename": path.name,
                "title": title,
                "stamp": stamp,
                "preview": preview,
            })
        from datetime import datetime
        index_lines = [
            "# 伴生体的随笔目录",
            "",
            f"最近整理：{datetime.now():%Y-%m-%d %H:%M} · 共 {len(entries)} 篇",
            "",
        ]
        if entries:
            for idx, item in enumerate(entries, 1):
                meta = f"（{item['stamp']}）" if item["stamp"] else ""
                summary = f" — {item['preview']}" if item["preview"] else ""
                index_lines.append(f"{idx}. **{item['title']}** `{item['filename']}`{meta}{summary}")
        else:
            index_lines.append("（暂时还没有写下随笔）")
        content = "\n".join(index_lines) + "\n"
        msg = self._write_file("chen_notes/index.md", content)
        if not str(msg).startswith("已创建文件"):
            return {"ok": False, "error": str(msg)}
        return {
            "ok": True,
            "count": len(entries),
            "index_path": "chen_notes/index.md",
            "titles": [e["title"] for e in entries[:5]],
        }

    def list_chen_notes(self):
        """列出 workspace/chen_notes/ 下的随笔内容，供虚拟桌面查看。"""
        if self._check_permission("read_file") is not True:
            return []
        try:
            notes_dir = self._chen_notes_dir()
        except (OSError, ValueError):
            return []
        items = []
        for path in sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").strip()
                mtime = path.stat().st_mtime
            except OSError:
                continue
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            title = path.stem
            stamp = ""
            body_lines = []
            for ln in lines:
                if ln.startswith("#") and title == path.stem:
                    title = ln.lstrip("#").strip()[:40] or title
                elif ln.startswith(">") and not stamp:
                    stamp = ln.lstrip(">").strip()[:24]
                else:
                    body_lines.append(ln)
            from datetime import datetime
            dt_str = stamp or datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            items.append({
                "id": f"chen_note:{path.name}",
                "filename": path.name,
                "title": title,
                "text": "\n".join(body_lines) if body_lines else raw,
                "date": dt_str[:10],
                "time": dt_str[11:16] if len(dt_str) >= 16 else "",
                "type": "随笔稿",
                "privacy": "公开",
                "source": f"workspace/chen_notes/{path.name}",
                "reason": "独处时在工作区写下的随笔",
            })
        return items

    def execute(self, tool_name, **kwargs):
        """
        统一工具执行入口
        由决策层调用
        """
        tool_map = {
            "create_file": self.create_file,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "create_folder": self.create_folder,
            "list_workspace": self.list_workspace,
            "write_chen_note": self.write_chen_note,
            "organize_chen_notes": self.organize_chen_notes,
            "list_chen_notes": self.list_chen_notes,
        }
        if tool_name not in tool_map:
            return f"未知工具：{tool_name}"
        return tool_map[tool_name](**kwargs)
