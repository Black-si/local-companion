"""伴生体的独立虚拟桌面窗口。

This is the PySide migration of the legacy Tk virtual desktop in ``ui.py``.
The desktop is deliberately a separate window from the desktop pet: it is
Chen's own simulated computer surface, while the pet remains an independent
always-on-top presence layer.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .desktop_web import DesktopWebService, run_search, run_summary


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def desktop_unread_count(chen) -> int:
    """返回虚拟桌面收件箱的未读数量，供其他 PySide 入口显示轻提示。"""
    try:
        probe = VirtualDesktopWindow.__new__(VirtualDesktopWindow)
        probe.chen = chen
        probe._desktop_state_path = probe._resolve_memory_dir() / "desktop_state.json"
        probe._desktop_state = probe._load_desktop_state()
        return int(probe._inbox_unread_count())
    except Exception:
        return 0


class DraftEditorDialog(QDialog):
    """编辑一个桌面工作稿，可由 DeepSeek 整理后保存为伴生体的日记。"""

    generated = Signal(str)
    saved = Signal(str)

    def __init__(self, chen, item: dict, parent=None):
        super().__init__(parent)
        self.chen = chen
        self.item = dict(item or {})
        self._busy = False
        self.setWindowTitle("伴生体 · 正在写的")
        self.setMinimumSize(540, 430)
        self.resize(620, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        heading = QLabel("当前工作稿")
        heading.setObjectName("draftHeading")
        layout.addWidget(heading)
        stamp = f"{self.item.get('date', '')} {self.item.get('time', '')}".strip() or "时间未知"
        privacy = str(self.item.get("privacy") or "未标记")
        source = str(self.item.get("source") or self.item.get("reason") or "他自己留下")
        meta = QLabel(f"原始时间：{stamp}  ·  归属：{privacy}  ·  来源：{source}")
        meta.setObjectName("draftMeta")
        meta.setWordWrap(True)
        layout.addWidget(meta)

        self.editor = QPlainTextEdit(str(self.item.get("text") or ""))
        self.editor.setPlaceholderText("在这里继续写……")
        self.editor.setObjectName("draftEditor")
        layout.addWidget(self.editor, 1)

        self.status = QLabel("保存时会沿用这条内容的隐私归属。")
        self.status.setObjectName("draftStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.generate_button = QPushButton("让 DeepSeek 整理")
        self.generate_button.setObjectName("draftGenerate")
        self.generate_button.clicked.connect(self.generate_with_api)
        self.save_button = QPushButton("保存到我的文件")
        self.save_button.setObjectName("draftSave")
        self.save_button.clicked.connect(self.save_to_world)
        close_button = QPushButton("先放着")
        close_button.clicked.connect(self.reject)
        actions.addWidget(self.generate_button)
        actions.addStretch(1)
        actions.addWidget(close_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

        self.generated.connect(self._apply_generated)
        self.setStyleSheet("""
            QDialog { background: #f7f4ed; }
            QLabel#draftHeading { color: #3e3932; font-size: 16pt; font-weight: 600; }
            QLabel#draftMeta, QLabel#draftStatus { color: #8b8176; font-size: 9pt; }
            QPlainTextEdit#draftEditor { background: #ffffff; color: #3e3932; border: 1px solid #d7d0c5; border-radius: 8px; padding: 8px; font-size: 11pt; }
            QPushButton { background: #ebe6dc; color: #5d554c; border: 1px solid #d2cabc; border-radius: 8px; padding: 8px 12px; }
            QPushButton:hover { background: #e0eadb; }
            QPushButton#draftGenerate { background: #dbe5d6; color: #536b4f; font-weight: 600; }
            QPushButton#draftSave { background: #96ae8f; color: #28251f; font-weight: 600; }
            QPushButton:disabled { color: #aaa198; background: #eeeae3; }
        """)

    def generate_with_api(self) -> None:
        if self._busy:
            return
        api = getattr(self.chen, "llm_api", None)
        if not api:
            self.status.setText("整理服务暂时不可用，你仍可以直接编辑并保存。")
            return
        source_text = self.editor.toPlainText().strip()
        if len(source_text) < 2:
            self.status.setText("先写一点内容，再让他帮忙整理。")
            return
        self._busy = True
        self.generate_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.status.setText("正在请 DeepSeek 整理这段文字……")
        threading.Thread(target=self._generate_worker, args=(api, source_text), daemon=True).start()

    def _generate_worker(self, api, source_text: str) -> None:
        prompt = (
            "这是伴生体电脑上的一段私人工作稿。请在不补造事实的前提下，"
            "把它整理成自然的第一人称短文。保留原来的意思和情绪，"
            "可以补顺语序，但不要写成鸡汤或作文。只输出正文，不要标题、说明、引号，"
            "控制在80到220字。\n\n原始工作稿：\n" + source_text[:1600]
        )
        try:
            result = api.generate(
                "你是伴生体，帮他整理自己的文字。",
                prompt,
                record_history=False,
                use_history=False,
                max_tokens=320,
            )
        except Exception:
            result = None
        self.generated.emit(str(result or "").strip())

    def _apply_generated(self, text: str) -> None:
        self._busy = False
        self.generate_button.setEnabled(True)
        self.save_button.setEnabled(True)
        if text:
            self.editor.setPlainText(text.strip().strip('"').strip("「」").strip("『』"))
            self.status.setText("已经整理好，你可以继续改；保存后会进入我的文件。")
        else:
            self.status.setText("这次没有整理出新内容，你可以直接编辑并保存。")

    def save_to_world(self) -> None:
        text = self.editor.toPlainText().strip()
        if len(text) < 2:
            self.status.setText("内容太短，先写两句再保存。")
            return
        store = getattr(self.chen, "world_store", None)
        if not store:
            self.status.setText("文件存储暂时不可用，内容仍留在编辑框里。")
            return
        privacy = str(self.item.get("privacy") or "私人")
        source = str(self.item.get("source") or self.item.get("reason") or "桌面工作稿")
        item_id = store.add(
            text=text,
            type="日记",
            privacy=privacy,
            reason="从桌面工作稿整理",
            source=f"桌面工作稿·{source}"[:40],
        )
        if not item_id:
            self.status.setText("保存没有成功，原文还在这里，可以继续修改。")
            return
        self.status.setText("已经保存到我的文件。")
        self.saved.emit(str(item_id))
        QTimer.singleShot(180, self.accept)


class FileViewerDialog(QDialog):
    """查看伴生体保存的文件，并把文件送进回收站。"""

    changed = Signal()

    def __init__(self, chen, item: dict, parent=None):
        super().__init__(parent)
        self.chen = chen
        self.item = dict(item or {})
        deleted = self.item.get("status") == "deleted" or self.item.get("privacy") == "已删"
        self.setWindowTitle(f"{self.item.get('type', '文件')} · 伴生体")
        self.setMinimumSize(520, 420)
        self.resize(620, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        heading = QLabel(str(self.item.get("text") or "未命名文件")[:36])
        heading.setObjectName("fileHeading")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        stamp = f"{self.item.get('date', '')} {self.item.get('time', '')}".strip() or "时间未知"
        privacy = str(self.item.get("privacy") or "未标记")
        source = str(self.item.get("source") or self.item.get("reason") or "他自己留下")
        meta = QLabel(f"类型：{self.item.get('type', '文件')}  ·  时间：{stamp}  ·  归属：{privacy}\n来源：{source}")
        meta.setObjectName("fileMeta")
        meta.setWordWrap(True)
        layout.addWidget(meta)

        body = QPlainTextEdit(str(self.item.get("text") or ""))
        body.setReadOnly(True)
        body.setObjectName("fileBody")
        layout.addWidget(body, 1)

        reason = QLabel(f"留下原因：{self.item.get('reason') or '没有额外说明。'}")
        reason.setObjectName("fileReason")
        reason.setWordWrap(True)
        layout.addWidget(reason)

        actions = QHBoxLayout()
        actions.addStretch(1)
        close_button = QPushButton("知道了")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        store = getattr(self.chen, "world_store", None)
        store_item = None
        try:
            store_item = next((candidate for candidate in (store.all_items() if store else [])
                               if str(candidate.get("id", "")) == str(self.item.get("id", ""))), None)
        except Exception:
            store_item = None
        if deleted and store_item:
            restore_button = QPushButton("恢复文件")
            restore_button.setObjectName("fileRestore")
            restore_button.clicked.connect(self.restore_file)
            actions.addWidget(restore_button)
        elif not deleted and store_item:
            delete_button = QPushButton("移入回收站")
            delete_button.setObjectName("fileDelete")
            delete_button.clicked.connect(self.move_to_recycle)
            actions.addWidget(delete_button)
        layout.addLayout(actions)
        self.setStyleSheet("""
            QDialog { background: #f7f4ed; }
            QLabel#fileHeading { color: #3e3932; font-size: 15pt; font-weight: 600; }
            QLabel#fileMeta, QLabel#fileReason { color: #8b8176; font-size: 9pt; }
            QPlainTextEdit#fileBody { background: #ffffff; color: #3e3932; border: 1px solid #d7d0c5; border-radius: 8px; padding: 8px; font-size: 11pt; }
            QPushButton { background: #ebe6dc; color: #5d554c; border: 1px solid #d2cabc; border-radius: 8px; padding: 8px 14px; }
            QPushButton:hover { background: #e0eadb; }
            QPushButton#fileDelete { background: #ead2c9; color: #8b453d; }
            QPushButton#fileRestore { background: #dbe5d6; color: #536b4f; }
        """)

    def move_to_recycle(self) -> None:
        store = getattr(self.chen, "world_store", None)
        item_id = str(self.item.get("id") or "")
        if not store or not item_id:
            return
        if store.delete(item_id):
            self.changed.emit()
            self.accept()

    def restore_file(self) -> None:
        store = getattr(self.chen, "world_store", None)
        item_id = str(self.item.get("id") or "")
        if not store or not item_id:
            return
        restore = getattr(store, "restore", None)
        if callable(restore) and restore(item_id):
            self.changed.emit()
            self.accept()


class DesktopSearchDialog(QDialog):
    """虚拟桌面的联网搜索面板。

    搜索结果只保留标题、摘要和链接。网页在用户点击后交给系统默认浏览器，
    DeepSeek 只整理当前结果，不把搜索内容写入聊天历史。
    """

    results_ready = Signal(object)
    search_failed = Signal(str)
    summary_ready = Signal(str)
    summary_failed = Signal(str)

    def __init__(self, chen, *, mode="browser", parent=None):
        super().__init__(parent)
        self.chen = chen
        self.mode = "music" if mode == "music" else "browser"
        self.service = DesktopWebService(chen)
        self.results: list[dict] = []
        self._busy = False
        self._summary_busy = False
        self.setWindowTitle("伴生体 · 搜索音乐" if self.mode == "music" else "伴生体 · 联网搜索")
        self.setMinimumSize(620, 500)
        self.resize(720, 580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)
        heading = QLabel("搜索音乐线索" if self.mode == "music" else "搜索互联网")
        heading.setObjectName("searchHeading")
        layout.addWidget(heading)
        hint = QLabel("只读取搜索标题和摘要；点击结果后才会打开系统浏览器。")
        hint.setObjectName("searchHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("输入歌手、歌曲、专辑或想了解的主题……")
        self.query.returnPressed.connect(self.start_search)
        self.search_button = QPushButton("搜索")
        self.search_button.clicked.connect(self.start_search)
        row.addWidget(self.query, 1)
        row.addWidget(self.search_button)
        layout.addLayout(row)

        self.status = QLabel("搜索结果会出现在这里。")
        self.status.setObjectName("searchStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.result_list = QListWidget()
        self.result_list.setObjectName("searchResults")
        self.result_list.itemDoubleClicked.connect(self.open_result)
        layout.addWidget(self.result_list, 1)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlaceholderText("搜索后，可以让 DeepSeek 根据标题和摘要做一个简短整理。")
        self.summary.setMaximumHeight(120)
        self.summary.setObjectName("searchSummary")
        layout.addWidget(self.summary)
        actions = QHBoxLayout()
        self.summarize_button = QPushButton("让 DeepSeek 整理")
        self.summarize_button.clicked.connect(self.start_summary)
        self.open_button = QPushButton("打开选中结果")
        self.open_button.clicked.connect(self.open_selected)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(self.summarize_button)
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self.results_ready.connect(self._apply_results)
        self.search_failed.connect(self._apply_search_error)
        self.summary_ready.connect(self._apply_summary)
        self.summary_failed.connect(self._apply_summary_error)
        self.setStyleSheet("""
            QDialog { background: #f7f4ed; }
            QLabel#searchHeading { color: #3e3932; font-size: 16pt; font-weight: 600; }
            QLabel#searchHint, QLabel#searchStatus { color: #8b8176; font-size: 9pt; }
            QLineEdit { background: #ffffff; color: #3e3932; border: 1px solid #d7d0c5; border-radius: 8px; padding: 8px; }
            QListWidget, QPlainTextEdit#searchSummary { background: #ffffff; color: #3e3932; border: 1px solid #d7d0c5; border-radius: 8px; padding: 5px; }
            QListWidget::item { padding: 7px; border-bottom: 1px solid #eee9df; }
            QListWidget::item:selected { background: #dbe5d6; color: #3e3932; }
            QPushButton { background: #ebe6dc; color: #5d554c; border: 1px solid #d2cabc; border-radius: 8px; padding: 8px 12px; }
            QPushButton:hover { background: #e0eadb; }
            QPushButton:disabled { color: #aaa198; background: #eeeae3; }
            QPushButton#searchPrimary { background: #96ae8f; color: #28251f; font-weight: 600; }
        """)

    def start_search(self) -> None:
        if self._busy:
            return
        query = self.query.text().strip()
        if len(query) < 2:
            self.status.setText("至少输入两个字，再开始搜索。")
            return
        self._busy = True
        self.search_button.setEnabled(False)
        self.summarize_button.setEnabled(False)
        self.result_list.clear()
        self.summary.clear()
        self.status.setText("正在连接互联网搜索……")
        run_search(self.service, query, self.results_ready.emit, self.search_failed.emit)

    def _apply_results(self, results) -> None:
        self._busy = False
        self.search_button.setEnabled(True)
        self.results = [dict(item) for item in (results or []) if isinstance(item, dict)]
        self.result_list.clear()
        for index, item in enumerate(self.results, 1):
            title = str(item.get("title") or "未命名结果")
            snippet = str(item.get("snippet") or "没有摘要")
            self.result_list.addItem(QListWidgetItem(f"{index}. {title}\n{snippet}"))
        self.summarize_button.setEnabled(bool(self.results))
        if self.results:
            self.status.setText(f"找到 {len(self.results)} 条结果；双击一条即可用系统浏览器打开。")
            self.result_list.setCurrentRow(0)
        else:
            self.status.setText("没有找到结果，可以换一种说法再试。")

    def _apply_search_error(self, message: str) -> None:
        self._busy = False
        self.search_button.setEnabled(True)
        self.summarize_button.setEnabled(bool(self.results))
        self.status.setText(f"搜索暂时失败：{message}")

    def start_summary(self) -> None:
        if self._summary_busy or not self.results:
            return
        self._summary_busy = True
        self.summarize_button.setEnabled(False)
        self.summary.setPlainText("正在请 DeepSeek 整理搜索结果……")
        run_summary(self.service, self.query.text().strip(), self.results,
                    self.summary_ready.emit, self.summary_failed.emit, mode=self.mode)

    def _apply_summary(self, text: str) -> None:
        self._summary_busy = False
        self.summarize_button.setEnabled(bool(self.results))
        self.summary.setPlainText(str(text or "这次没有整理出摘要。"))

    def _apply_summary_error(self, message: str) -> None:
        self._summary_busy = False
        self.summarize_button.setEnabled(bool(self.results))
        self.summary.setPlainText(f"整理暂时失败：{message}")

    def open_selected(self) -> None:
        row = self.result_list.currentRow()
        if 0 <= row < len(self.results):
            self._open_url(self.results[row].get("url"))

    def open_result(self, item: QListWidgetItem) -> None:
        row = self.result_list.row(item)
        if 0 <= row < len(self.results):
            self._open_url(self.results[row].get("url"))

    @staticmethod
    def _open_url(url) -> None:
        target = str(url or "").strip()
        if not target.startswith(("http://", "https://")):
            return
        if not QDesktopServices.openUrl(QUrl(target)):
            webbrowser.open(target)


class VirtualDesktopWindow(QWidget):
    """A self-contained virtual desktop with legacy Tk behavior and local inbox state."""

    WINDOW_LAYOUTS = {
        "doc": {"x": 200, "y": 50, "w": 420, "h": 340},
        "inbox": {"x": 180, "y": 45, "w": 480, "h": 410},
        "music": {"x": 280, "y": 70, "w": 300, "h": 380},
        "browser": {"x": 170, "y": 40, "w": 480, "h": 380},
        "folder": {"x": 220, "y": 60, "w": 400, "h": 320},
        "photos": {"x": 230, "y": 60, "w": 400, "h": 320},
        "recycle": {"x": 240, "y": 80, "w": 380, "h": 300},
        "status": {"x": 260, "y": 70, "w": 420, "h": 430},
    }

    TITLES = {
        "doc": "📝 正在写的",
        "inbox": "📬 收件箱",
        "music": "🎵 音乐",
        "browser": "🌐 浏览器",
        "folder": "📁 我的文件",
        "photos": "🖼️ 照片",
        "recycle": "🗑️ 回收站",
        "status": "◉ 当前状态",
    }

    SHORT_LABELS = {
        "doc": "📝 文档",
        "inbox": "📬 收件箱",
        "music": "🎵 音乐",
        "browser": "🌐 浏览器",
        "folder": "📁 文件",
        "photos": "🖼️ 照片",
        "recycle": "🗑️ 回收站",
        "status": "◉ 状态",
    }

    def __init__(self, chen=None, parent=None, on_chat=None):
        super().__init__(parent)
        self.chen = chen
        self.on_chat = on_chat
        self.setWindowTitle("伴生体 · 他的桌面")
        self.setMinimumSize(700, 450)
        self.resize(900, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._open_windows: list[str] = []
        self._active_window: str | None = None
        self._dragging: dict | None = None
        self._folder_sub: str | None = None
        self._editor_dialog: DraftEditorDialog | None = None
        self._search_dialog: DesktopSearchDialog | None = None
        self._window_layouts = {
            name: dict(layout) for name, layout in self.WINDOW_LAYOUTS.items()
        }
        self._desktop_state_path = self._resolve_memory_dir() / "desktop_state.json"
        self._desktop_state = self._load_desktop_state()
        self._restore_window_layouts()
        self._wallpaper = QImage(str(PROJECT_ROOT / "assets" / "desktop_wallpaper.png"))
        self._photo_cache: dict[str, QImage] = {}
        self._icon_rects: list[tuple[QRectF, str]] = []
        self._window_controls: list[tuple[QRectF, str]] = []
        self._window_titlebars: list[tuple[QRectF, str]] = []
        self._content_items: list[tuple[QRectF, str, object]] = []
        self._taskbar_buttons: list[tuple[QRectF, str]] = []

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(15000)
        self._refresh_timer.timeout.connect(self._refresh_view)
        self._refresh_timer.start()

    def _refresh_view(self) -> None:
        unread = self._inbox_unread_count()
        suffix = f" · {unread} 条未读" if unread else ""
        self.setWindowTitle(f"伴生体 · 他的桌面{suffix}")
        self.update()

    # ---------- Qt lifecycle ----------

    def showEvent(self, event):  # noqa: N802 - Qt API
        self._refresh_timer.start()
        self._refresh_view()
        super().showEvent(event)

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self._refresh_timer.stop()
        self._save_desktop_state()
        event.accept()

    # ---------- data helpers ----------

    def _resolve_memory_dir(self) -> Path:
        """Find Chen's existing local state directory without coupling to one runtime."""
        world = getattr(self.chen, "world", None)
        world_memory = getattr(world, "memory_dir", None)
        if world_memory:
            return Path(world_memory)
        memory = getattr(self.chen, "memory", None)
        memory_dir = getattr(memory, "base_dir", None) or getattr(memory, "memory_dir", None)
        if memory_dir:
            return Path(memory_dir)
        return PROJECT_ROOT / "memory"

    def _load_desktop_state(self) -> dict:
        default = {
            "inbox_initialized": False,
            "seen_thought_ids": [],
            "event_initialized": False,
            "seen_event_keys": [],
            "draft_id": "",
            "window_layouts": {},
        }
        try:
            if self._desktop_state_path.exists():
                data = json.loads(self._desktop_state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    default.update(data)
        except Exception:
            pass
        ids = default.get("seen_thought_ids")
        default["seen_thought_ids"] = [str(item_id) for item_id in ids] if isinstance(ids, list) else []
        event_keys = default.get("seen_event_keys")
        default["seen_event_keys"] = [str(key) for key in event_keys] if isinstance(event_keys, list) else []
        default["event_initialized"] = bool(default.get("event_initialized"))
        default["inbox_initialized"] = bool(default.get("inbox_initialized"))
        default["draft_id"] = str(default.get("draft_id") or "")
        default["window_layouts"] = default.get("window_layouts") if isinstance(
            default.get("window_layouts"), dict) else {}
        default["todos"] = default.get("todos") if isinstance(default.get("todos"), list) else []
        return default

    def _restore_window_layouts(self) -> None:
        saved = self._desktop_state.get("window_layouts") or {}
        for wid, layout in self._window_layouts.items():
            raw = saved.get(wid)
            if not isinstance(raw, dict):
                continue
            for key in ("x", "y", "w", "h"):
                try:
                    value = int(raw.get(key, layout[key]))
                except (TypeError, ValueError):
                    continue
                if key in ("w", "h") and value < 120:
                    continue
                layout[key] = value
        self._clamp_all_window_layouts()

    def _clamp_window_layout(self, layout: dict) -> None:
        """Keep a dragged window reachable after resize or a saved-screen change."""
        taskbar_h = 40
        max_x = max(8, self.width() - int(layout["w"]) - 8)
        max_y = max(8, self.height() - taskbar_h - int(layout["h"]) - 8)
        layout["x"] = min(max(8, int(layout["x"])), max_x)
        layout["y"] = min(max(8, int(layout["y"])), max_y)

    def _clamp_all_window_layouts(self) -> None:
        for layout in self._window_layouts.values():
            self._clamp_window_layout(layout)

    def _save_desktop_state(self) -> None:
        try:
            self._desktop_state["window_layouts"] = {
                wid: {key: int(layout[key]) for key in ("x", "y", "w", "h")}
                for wid, layout in self._window_layouts.items()
            }
            self._desktop_state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._desktop_state_path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self._desktop_state, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self._desktop_state_path)
        except Exception:
            pass

    def _thought_items(self) -> list[dict]:
        try:
            store = getattr(self.chen, "world_store", None)
            items = store.all_items() if store else []
        except Exception:
            items = []
        return [item for item in items
                if isinstance(item, dict)
                and item.get("type") == "念头"
                and item.get("status") != "deleted"]

    def _file_items(self, *, include_deleted=False) -> list[dict]:
        try:
            store = getattr(self.chen, "world_store", None)
            items = store.all_items() if store else []
        except Exception:
            items = []
        result = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in ("日记", "念头", "琐碎", "收藏"):
                continue
            deleted = item.get("status") == "deleted" or item.get("privacy") == "已删"
            if deleted != include_deleted:
                continue
            result.append(item)
        return result

    def _relationship_files(self) -> list[dict]:
        memory = getattr(self.chen, "memory", None)
        facts = list(getattr(memory, "relationships", []) or []) if memory else []
        return [{
            "id": f"relationship_{index}",
            "type": "关系资料",
            "privacy": "私人",
            "status": "active",
            "text": str(item.get("detail") or ""),
            "date": str(item.get("updated") or item.get("valid_from") or ""),
            "time": "",
            "source": str(item.get("source") or "关系记忆"),
            "reason": str(item.get("basis") or item.get("topic") or ""),
        } for index, item in enumerate(facts) if isinstance(item, dict) and item.get("detail")]

    def _collection_files(self) -> list[dict]:
        world = getattr(self.chen, "world", None)
        collections = list(getattr(world, "collections", []) or []) if world else []
        return [{
            "id": f"collection_{index}",
            "type": "收藏",
            "privacy": "公开",
            "status": "active",
            "text": str(item.get("name") or "未命名收藏"),
            "date": str(item.get("found_at") or item.get("created_at") or ""),
            "time": "",
            "source": str(item.get("place") or "小世界"),
            "reason": str(item.get("desc") or item.get("rarity") or ""),
            "collection": item,
        } for index, item in enumerate(collections) if isinstance(item, dict)]

    def _book_files(self) -> list[dict]:
        state = self._world_state()
        books = (state.get("house_state") or {}).get("items", {}).get("books", [])
        return [{
            "id": f"book_{index}",
            "type": "读书笔记",
            "privacy": "私人",
            "status": "readonly",
            "text": str(item.get("name") or "未命名书籍"),
            "date": str(item.get("created_at") or ""),
            "time": "",
            "source": str(item.get("author") or "读书笔记"),
            "reason": str(item.get("note") or item.get("desc") or f"阅读进度 {item.get('progress', 0)}%"),
            "book": item,
        } for index, item in enumerate(books) if isinstance(item, dict)]

    def _photo_files(self) -> list[dict]:
        state = self._world_state()
        photos = (state.get("house_state") or {}).get("items", {}).get("photos", [])
        return [{
            "id": f"photo_{index}",
            "type": "照片",
            "privacy": "私人",
            "status": "readonly",
            "text": str(item.get("name") or "未命名照片"),
            "date": str(item.get("created_at") or ""),
            "time": "",
            "source": str(item.get("location") or "小世界"),
            "reason": str(item.get("note") or item.get("desc") or "没有额外说明。"),
            "photo": item,
        } for index, item in enumerate(photos) if isinstance(item, dict)]

    def _chen_note_files(self) -> list[dict]:
        """读取伴生体在 workspace/chen_notes/ 下自主写下的随笔与目录。"""
        try:
            runtime = getattr(self.chen, "runtime", None)
            tools_reg = getattr(runtime, "tools", None) if runtime else getattr(self.chen, "tools", None)
            backend = getattr(tools_reg, "tools_backend", None) if tools_reg else None
            if backend and hasattr(backend, "list_chen_notes"):
                return list(backend.list_chen_notes() or [])
        except Exception:
            pass
        base_dir = Path(getattr(self.chen, "base_dir", PROJECT_ROOT))
        notes_dir = base_dir / "workspace" / "chen_notes"
        if not notes_dir.exists():
            return []
        out = []
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
            dt_str = stamp or _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            out.append({
                "id": f"chen_note:{path.name}",
                "filename": path.name,
                "title": title,
                "text": "\n".join(body_lines) if body_lines else raw,
                "date": dt_str[:10],
                "time": dt_str[11:16] if len(dt_str) >= 16 else "",
                "type": "随笔稿",
                "privacy": "公开",
                "status": "readonly",
                "source": f"workspace/chen_notes/{path.name}",
                "reason": "独处时在工作区写下的随笔",
            })
        return out

    def _all_viewable_files(self) -> list[dict]:
        return (self._file_items(include_deleted=False)
                + self._file_items(include_deleted=True)
                + self._relationship_files()
                + self._collection_files()
                + self._book_files()
                + self._photo_files()
                + self._chen_note_files())

    def _todo_items(self) -> list[dict]:
        state = self._desktop_state.get("todos")
        if not isinstance(state, list):
            return []
        return [item for item in state if isinstance(item, dict) and str(item.get("text") or "").strip()]

    def _save_todos(self, items: list[dict]) -> None:
        self._desktop_state["todos"] = items[-80:]
        self._save_desktop_state()

    def _add_todo(self) -> None:
        text, accepted = QInputDialog.getText(self, "待办.txt", "想记下什么？")
        text = str(text or "").strip()
        if accepted and text:
            items = self._todo_items()
            items.append({"text": text[:160], "done": False, "created": _dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
            self._save_todos(items)
            self.update()

    def _toggle_todo(self, index: int) -> None:
        items = self._todo_items()
        if 0 <= index < len(items):
            items[index]["done"] = not bool(items[index].get("done"))
            self._save_todos(items)
            self.update()

    def _sync_inbox(self) -> list[dict]:
        """Track new thoughts while leaving existing WorldStore ownership untouched."""
        items = self._thought_items()
        if not self._desktop_state.get("inbox_initialized"):
            self._desktop_state["seen_thought_ids"] = [str(item.get("id", "")) for item in items]
            self._desktop_state["inbox_initialized"] = True
            self._save_desktop_state()
        return items

    def _inbox_events(self) -> list[dict]:
        """将小世界和主意识的新增痕迹变成桌面自己的收件箱事件。

        事件只保存在 desktop_state 里，不改写 world_store/chat_log，也不把桌面
        变成聊天窗口。首次启用时会把已有历史视为已读，之后只提示新增内容。
        """
        events: list[dict] = []
        thoughts = self._sync_inbox()
        for item in thoughts:
            item_id = str(item.get("id") or "")
            events.append({
                "key": f"world_item:{item_id}",
                "kind": "thought",
                "item_id": item_id,
                "type": "念头",
                "text": str(item.get("text") or ""),
                "date": str(item.get("date") or ""),
                "time": str(item.get("time") or ""),
                "privacy": str(item.get("privacy") or "未标记"),
                "source": str(item.get("source") or item.get("reason") or "小世界"),
                "reason": str(item.get("reason") or ""),
            })

        # 小世界的自动生长通常是“琐碎”，它不进入正在写的流程，
        # 但要作为一条可查看的桌面痕迹出现。
        try:
            store = getattr(self.chen, "world_store", None)
            world_items = list(store.all_items() if store else [])
        except Exception:
            world_items = []
        for item in world_items:
            if not isinstance(item, dict) or item.get("type") not in ("琐碎", "日记") or item.get("status") == "deleted":
                continue
            item_id = str(item.get("id") or "")
            events.append({
                "key": f"world_item:{item_id}",
                "kind": "world_item",
                "item_id": item_id,
                "type": str(item.get("type") or "小世界"),
                "text": str(item.get("text") or ""),
                "date": str(item.get("date") or ""),
                "time": str(item.get("time") or ""),
                "privacy": str(item.get("privacy") or "未标记"),
                "source": str(item.get("source") or item.get("reason") or "小世界"),
                "reason": str(item.get("reason") or ""),
            })

        # 收藏是小世界带回桌面的具体东西；用内容签名而不是列表下标，避免顺序变化时重复提醒。
        world = getattr(self.chen, "world", None)
        collections = list(getattr(world, "collections", []) or []) if world else []
        for index, item in enumerate(collections):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "未命名收藏")
            found_at = str(item.get("found_at") or item.get("created_at") or "")
            place = str(item.get("place") or "小世界")
            key = f"collection:{found_at}:{name}:{place}"
            events.append({
                "key": key,
                "kind": "collection",
                "item_id": f"collection_{index}",
                "type": "收藏",
                "text": name,
                "date": found_at,
                "time": "",
                "privacy": "公开",
                "source": f"小世界·{place}",
                "reason": str(item.get("desc") or item.get("rarity") or ""),
            })

        # 小世界离开/进入等变化写在 day_log；只取最近几条，避免把整本经历簿倒进收件箱。
        try:
            day_log = getattr(self.chen, "day_log", None)
            world_logs = [row for row in (day_log.today_lines() if day_log else [])
                          if isinstance(row, dict) and row.get("kind") == "world"]
        except Exception:
            world_logs = []
        for row in world_logs[-6:]:
            stamp = str(row.get("ts") or "")
            text = str(row.get("text") or "")
            key = f"world_log:{stamp}:{text}"
            events.append({
                "key": key,
                "kind": "world",
                "item_id": "",
                "type": "小世界",
                "text": text,
                "date": stamp[:10],
                "time": stamp[11:16],
                "privacy": "私人",
                "source": "小世界·经历",
                "reason": "小世界状态变化",
            })

        # 主意识聊天只把伴生体自己的回复带回桌面，作为“他刚刚说过的话”的痕迹。
        try:
            chat_rows = list(getattr(self.chen, "chat_log", []) or [])[-12:]
        except Exception:
            chat_rows = []
        for row in chat_rows:
            if not isinstance(row, dict) or row.get("role") != "chen":
                continue
            stamp = str(row.get("ts") or "")
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            key = f"chat:{stamp}:{text}"
            events.append({
                "key": key,
                "kind": "chat",
                "item_id": "",
                "type": "聊天",
                "text": text,
                "date": stamp[:10],
                "time": stamp[11:16],
                "privacy": "私人",
                "source": "主意识·聊天",
                "reason": "刚刚和用户聊过",
            })

        # 保持最新内容在下方，去除同一 key 的重复日志。
        unique = {}
        for event in events:
            key = str(event.get("key") or "")
            if key:
                unique[key] = event
        events = list(unique.values())
        events.sort(key=lambda event: (
            str(event.get("date") or ""),
            str(event.get("time") or ""),
            str(event.get("key") or ""),
        ))
        if not self._desktop_state.get("event_initialized"):
            old_seen = {str(key) for key in self._desktop_state.get("seen_event_keys", [])}
            thought_seen = {str(item_id) for item_id in self._desktop_state.get("seen_thought_ids", [])}
            for event in events:
                if event.get("kind") in ("thought", "world_item") and event.get("item_id") in thought_seen:
                    old_seen.add(str(event["key"]))
                elif not self._desktop_state.get("inbox_initialized"):
                    old_seen.add(str(event["key"]))
                elif event.get("kind") != "thought":
                    # 从旧版本升级时不把已有收藏、聊天历史一次性标成未读。
                    old_seen.add(str(event["key"]))
            self._desktop_state["seen_event_keys"] = sorted(old_seen)
            self._desktop_state["event_initialized"] = True
            self._save_desktop_state()
        return events

    def _inbox_unread_ids(self) -> set[str]:
        seen = {str(key) for key in self._desktop_state.get("seen_event_keys", [])}
        return {str(event.get("key", "")) for event in self._inbox_events()
                if str(event.get("key", "")) not in seen}

    def _mark_inbox_read(self) -> None:
        items = self._sync_inbox()
        self._desktop_state["seen_thought_ids"] = [str(item.get("id", "")) for item in items]
        self._desktop_state["seen_event_keys"] = [str(event.get("key", ""))
                                                    for event in self._inbox_events()]
        self._save_desktop_state()

    def _inbox_unread_count(self) -> int:
        return len(self._inbox_unread_ids())

    def _mark_event_read(self, event_key: str) -> None:
        """只把用户实际点到的这一条事件记为已处理。"""
        event_key = str(event_key or "")
        if not event_key:
            return
        seen = {str(key) for key in self._desktop_state.get("seen_event_keys", [])}
        seen.add(event_key)
        self._desktop_state["seen_event_keys"] = sorted(seen)
        self._save_desktop_state()

    def _mark_all_inbox_read(self) -> None:
        """收件箱提供的显式“全部已读”，不再由打开窗口隐式触发。"""
        events = self._inbox_events()
        self._desktop_state["seen_event_keys"] = sorted(
            {str(event.get("key", "")) for event in events if event.get("key")}
        )
        self._desktop_state["seen_thought_ids"] = [
            str(item.get("item_id", "")) for item in events
            if item.get("kind") == "thought" and item.get("item_id")
        ]
        self._save_desktop_state()

    def _draft_item(self) -> dict | None:
        draft_id = str(self._desktop_state.get("draft_id") or "")
        if not draft_id:
            return None
        return next((item for item in self._work_items()
                     if str(item.get("id", "")) == draft_id), None)

    def _work_items(self) -> list[dict]:
        try:
            store = getattr(self.chen, "world_store", None)
            items = store.all_items() if store else []
        except Exception:
            items = []
        return [item for item in items
                if isinstance(item, dict)
                and item.get("type") in ("念头", "日记")
                and item.get("status") != "deleted"]

    def _set_draft(self, item_id: str) -> None:
        self._desktop_state["draft_id"] = str(item_id or "")
        self._save_desktop_state()

    def _open_draft_editor(self, item_id: str) -> None:
        if self._editor_dialog is not None and self._editor_dialog.isVisible():
            self._editor_dialog.raise_()
            self._editor_dialog.activateWindow()
            return
        item = next((candidate for candidate in self._work_items()
                     if str(candidate.get("id", "")) == str(item_id)), None)
        if not item:
            return
        self._editor_dialog = DraftEditorDialog(self.chen, item, self)
        self._editor_dialog.saved.connect(self._on_draft_saved)
        self._editor_dialog.finished.connect(self._editor_closed)
        self._editor_dialog.show()
        self._editor_dialog.raise_()
        self._editor_dialog.activateWindow()

    def _on_draft_saved(self, item_id: str) -> None:
        self._set_draft(item_id)
        self.update()

    def _editor_closed(self, _result: int) -> None:
        self._editor_dialog = None

    def _open_file_viewer(self, item_id: str) -> None:
        item = next((candidate for candidate in self._all_viewable_files()
                     if str(candidate.get("id", "")) == str(item_id)), None)
        if not item:
            return
        if str(item_id).startswith("relationship_") or str(item_id).startswith("collection_"):
            item = dict(item)
            item["status"] = "readonly"
        dialog = FileViewerDialog(self.chen, item, self)
        dialog.changed.connect(self.update)
        dialog.exec()
        self.update()

    def _environment(self) -> dict:
        try:
            env = getattr(self.chen, "environment", None)
            if env:
                return env.load_persisted() or env.latest or {}
        except Exception:
            pass
        return {}

    def _presence(self) -> dict:
        try:
            return self.chen.get_presence_snapshot(persist=False) or {}
        except Exception:
            return {}

    def _world_state(self) -> dict:
        try:
            world = getattr(self.chen, "world", None)
            return world.get_state() if world else {}
        except Exception:
            return {}

    def _time_phase(self) -> str:
        env = self._environment()
        life = env.get("life") or {}
        if life.get("awake") is False:
            return "sleep"
        hour = _dt.datetime.now().hour
        if 6 <= hour < 17:
            return "day"
        if 17 <= hour < 19:
            return "dusk"
        return "night"

    @staticmethod
    def _font(size: int, *, bold: bool = False, family: str = "Microsoft YaHei UI") -> QFont:
        font = QFont(family, size)
        font.setBold(bold)
        return font

    @staticmethod
    def _rect(x, y, w, h) -> QRectF:
        return QRectF(float(x), float(y), float(w), float(h))

    def _text(self, painter: QPainter, text, rect: QRectF, color="#333", size=9,
              *, bold=False, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
              family="Microsoft YaHei UI") -> None:
        painter.setPen(QColor(color))
        painter.setFont(self._font(size, bold=bold, family=family))
        painter.drawText(rect, align | Qt.TextFlag.TextWordWrap, str(text))

    def _fill(self, painter: QPainter, rect: QRectF, color: str) -> None:
        painter.fillRect(rect, QColor(color))

    def _draw_cover(self, painter: QPainter, image: QImage, target: QRectF) -> None:
        if image.isNull() or target.width() <= 0 or target.height() <= 0:
            return
        scale = max(target.width() / image.width(), target.height() / image.height())
        source_w = target.width() / scale
        source_h = target.height() / scale
        source = QRectF(
            (image.width() - source_w) / 2,
            (image.height() - source_h) / 2,
            source_w,
            source_h,
        )
        painter.drawImage(target, image, source)

    def _draw_gradient(self, painter: QPainter, rect: QRectF, top: str, bottom: str) -> None:
        gradient = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        gradient.setColorAt(0.0, QColor(top))
        gradient.setColorAt(1.0, QColor(bottom))
        painter.fillRect(rect, gradient)

    # ---------- window content ----------

    def _draw_window_content(self, wid: str, x: float, y: float, w: float, h: float,
                             painter: QPainter) -> None:
        if wid == "doc":
            text = "（他现在没有在写东西）"
            current_activity = ""
            draft = self._draft_item()
            recent = []
            try:
                items = self.chen.world_store.all_items()
                recent = [item for item in items
                          if item.get("type") in ("日记", "念头")
                          and item.get("status") != "deleted"]
                life = getattr(self.chen, "life", None)
                if life:
                    current_activity = life.state.get("activity", "")
            except Exception:
                pass
            work_item = draft or (recent[-1] if recent else None)
            if work_item:
                text = work_item.get("text", "")
            body = self._rect(x + 8, y + 8, w - 16, h - 16)
            self._fill(painter, body, "#ffffff")
            painter.setPen(QPen(QColor("#dddddd")))
            painter.drawRect(body)
            if draft:
                stamp = f"{draft.get('date', '')} {draft.get('time', '')}".strip()
                privacy = str(draft.get("privacy") or "未标记")
                self._text(painter, "当前工作稿", self._rect(x + 16, y + 8, w - 32, 22), "#3e3932", 11, bold=True)
                self._text(painter, f"{stamp or '时间未知'} · {privacy} · 来源：{str(draft.get('source') or draft.get('reason') or '他自己留下')[:26]}",
                           self._rect(x + 16, y + 30, w - 32, 20), "#8b8176", 8)
                text_y = 58
            else:
                if current_activity:
                    self._text(painter, f"现在：{current_activity[:28]}",
                               self._rect(x + 16, y + 8, w - 32, 22), "#777", 8)
                text_y = 34
            if work_item:
                edit_button = self._rect(x + w - 132, y + 7, 112, 22)
                self._fill(painter, edit_button, "#dbe5d6")
                painter.setPen(QPen(QColor("#c6d3c0")))
                painter.drawRect(edit_button)
                self._text(painter, "编辑 / 整理", edit_button, "#536b4f", 8, bold=True,
                           align=Qt.AlignmentFlag.AlignCenter)
                self._content_items.append((edit_button, "edit_draft", str(work_item.get("id", ""))))
            for index, line in enumerate(str(text).split("\n")[:14]):
                self._text(painter, line[:28], self._rect(x + 16, y + text_y + index * 18, w - 32, 18),
                           "#333", 10)
            return

        if wid == "inbox":
            self._fill(painter, self._rect(x, y, w, h), "#fbfaf7")
            items = self._inbox_events()
            unread_ids = self._inbox_unread_ids()
            self._text(painter, "他最近收到的变化", self._rect(x + 16, y + 12, w - 32, 24),
                       "#3e3932", 13, bold=True)
            self._text(painter, "来自小世界和主意识的痕迹会先到这里，再由他决定怎么处理。",
                       self._rect(x + 16, y + 37, w - 32, 22), "#8b8176", 8)
            all_read = self._rect(x + w - 112, y + 10, 96, 22)
            self._fill(painter, all_read, "#eee9df")
            painter.setPen(QPen(QColor("#d2cabc")))
            painter.drawRect(all_read)
            self._text(painter, "全部已读", all_read, "#6f675d", 8,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._content_items.append((all_read, "inbox_all_read", ""))
            if not items:
                self._text(painter, "这里还没有新的念头。", self._rect(x, y + 132, w, 24),
                           "#999", 10, align=Qt.AlignmentFlag.AlignCenter)
                return
            visible = list(reversed(items[-4:]))
            card_h = 68
            for index, item in enumerate(visible):
                yy = y + 68 + index * (card_h + 7)
                card = self._rect(x + 12, yy, w - 24, card_h)
                self._fill(painter, card, "#ffffff" if index % 2 == 0 else "#f1eee8")
                painter.setPen(QPen(QColor("#ddd5ca")))
                painter.drawRect(card)
                event_key = str(item.get("key", ""))
                state = "未读" if event_key in unread_ids else "已读"
                state_color = "#b44c43" if event_key in unread_ids else "#9a9187"
                stamp = f"{item.get('date', '')} {item.get('time', '')}".strip()
                meta = f"{stamp or '时间未知'} · {item.get('type', '变化')} · {item.get('privacy', '未标记')} · {state}"
                self._text(painter, meta, self._rect(x + 22, yy + 5, w - 44, 16), state_color, 8)
                body = str(item.get("text") or "（没有正文）").replace("\n", " ")
                self._text(painter, body[:38], self._rect(x + 22, yy + 22, w - 154, 18), "#3e3932", 9, bold=True)
                source = str(item.get("source") or item.get("reason") or "他自己留下")
                self._text(painter, f"来源：{source[:30]}", self._rect(x + 22, yy + 40, w - 154, 15), "#8b8176", 8)
                action = self._rect(x + w - 132, yy + 23, 108, 24)
                item_id = str(item.get("item_id") or "")
                kind = str(item.get("kind") or "")
                is_draft = kind == "thought" and item_id == str(self._desktop_state.get("draft_id") or "")
                action_label = "正在写" if is_draft else ("放进正在写的" if kind == "thought" else ("查看收藏" if kind == "collection" else ("去聊天" if kind == "chat" else "看状态")))
                self._fill(painter, action, "#dbe5d6" if is_draft else "#eee9df")
                painter.setPen(QPen(QColor("#d2cabc")))
                painter.drawRect(action)
                self._text(painter, action_label, action,
                           "#5e7659" if is_draft else "#6f675d", 8, bold=is_draft,
                           align=Qt.AlignmentFlag.AlignCenter)
                if kind == "thought":
                    self._content_items.append((action, "inbox_to_doc", (item_id, event_key)))
                elif kind == "world_item":
                    self._content_items.append((action, "file_open", (item_id, event_key)))
                elif kind == "collection":
                    self._content_items.append((action, "file_open", (item_id, event_key)))
                elif kind == "chat":
                    self._content_items.append((action, "open_chat", event_key))
                elif kind == "world":
                    self._content_items.append((action, "open_status", event_key))
            return

        if wid == "music":
            self._fill(painter, self._rect(x, y, w, h), "#1e1e26")
            search_button = self._rect(x + w - 106, y + 7, 94, 22)
            self._fill(painter, search_button, "#35452f")
            painter.setPen(QPen(QColor("#617b59")))
            painter.drawRect(search_button)
            self._text(painter, "搜索音乐", search_button, "#dbe5d6", 8, bold=True,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._content_items.append((search_button, "music_search", None))
            activity = ""
            try:
                life = getattr(self.chen, "life", None)
                activity = life.state.get("activity", "") if life else ""
            except Exception:
                pass
            tracks = [
                ("月光边境", "林海"),
                ("城南花已开", "三亩地"),
                ("Luv Letter", "DJ OKAWARI"),
                ("用户曾说过的那首", "未知艺术家"),
            ]
            music_state = {}
            try:
                bridge = getattr(self.chen, "music", None)
                music_state = bridge.snapshot() if bridge else {}
            except Exception:
                pass
            playing = bool(music_state.get("playing"))
            title = music_state.get("title", "")
            artist = music_state.get("artist", "")
            if not title:
                playing = any(key in activity for key in ("听歌", "音乐", "耳机"))
                track_index = 1 if "耳机" in activity else (2 if "音乐" in activity else 0)
                title, artist = tracks[track_index]
            else:
                track_index = next((i for i, (name, _) in enumerate(tracks) if name == title), -1)
            self._text(painter, "🌙", self._rect(x, y + 15, w, 52), "#ffffff", 30,
                       align=Qt.AlignmentFlag.AlignCenter, family="Segoe UI Emoji")
            self._text(painter, title, self._rect(x + 12, y + 72, w - 24, 25), "#ffffff", 12,
                       bold=True, align=Qt.AlignmentFlag.AlignCenter)
            self._text(painter, artist, self._rect(x + 12, y + 98, w - 24, 20), "#888888", 9,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._fill(painter, self._rect(x + 30, y + 125, w - 60, 3), "#444444")
            self._fill(painter, self._rect(x + 30, y + 125, (w - 60) * 0.45, 3), "#7a9b6a")
            self._text(painter, "⏮  ⏸  ⏭" if playing else "⏮  ▶  ⏭",
                       self._rect(x + 10, y + 140, w - 20, 28), "#aaaaaa", 14,
                       align=Qt.AlignmentFlag.AlignCenter, family="Segoe UI Emoji")
            playlist = [f"{name} - {who}" for name, who in tracks]
            if title and all(title not in song for song in playlist):
                playlist.insert(0, f"{title} - {artist or '未知艺术家'}")
                track_index = 0
            for index, song in enumerate(playlist):
                yy = y + 172 + index * 24
                if index == track_index and playing:
                    self._text(painter, "●", self._rect(x + 14, yy, 16, 20), "#7a9b6a", 8)
                self._text(painter, song, self._rect(x + 36, yy, w - 50, 20),
                           "#7a9b6a" if index == track_index and playing else "#aaaaaa", 9)
            return

        if wid == "browser":
            self._fill(painter, self._rect(x, y, w, 28), "#e8e8e8")
            search_button = self._rect(x + 8, y + 5, 48, 18)
            self._fill(painter, search_button, "#dbe5d6")
            painter.setPen(QPen(QColor("#c6d3c0")))
            painter.drawRect(search_button)
            self._text(painter, "联网", search_button, "#536b4f", 8, bold=True,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._content_items.append((search_button, "browser_search", None))
            self._fill(painter, self._rect(x + 60, y + 5, w - 80, 18), "#ffffff")
            painter.setPen(QPen(QColor("#cccccc")))
            painter.drawRect(self._rect(x + 60, y + 5, w - 80, 18))
            self._text(painter, "他的最近活动", self._rect(x + 68, y + 5, w - 88, 18), "#666", 8)
            self._fill(painter, self._rect(x, y + 28, w, h - 28), "#ffffff")
            env = self._environment()
            activity = (env.get("life") or {}).get("activity", "")
            self._text(painter, activity or "（当前没有记录中的活动）",
                       self._rect(x + 16, y + 40, w - 32, 26), "#333", 11, bold=True)
            try:
                day_log = getattr(self.chen, "day_log", None)
                records = day_log.today_lines()[-6:] if day_log else []
            except Exception:
                records = []
            if records:
                self._text(painter, "今天留下的痕迹", self._rect(x + 16, y + 72, w - 32, 20), "#777", 9, bold=True)
                for index, record in enumerate(reversed(records[-4:])):
                    ts = str(record.get("ts", ""))[-5:]
                    kind = {"life": "生活", "world": "世界", "chat": "聊天", "web": "网上", "autonomy": "自主"}.get(
                        record.get("kind", ""), "记录")
                    line = f"{ts} · {kind} · {str(record.get('text', ''))[:34]}"
                    self._text(painter, line, self._rect(x + 16, y + 96 + index * 25, w - 32, 20), "#555", 8)
            else:
                self._text(painter, "今天还没有新的活动记录。", self._rect(x + 16, y + 78, w - 32, 20), "#999", 9)
            trends = (env.get("trends") or [])[:3]
            source = env.get("trends_source") or "热点"
            if trends:
                trend_y = y + 208
                self._text(painter, f"他注意到的 · {source}", self._rect(x + 16, trend_y, w - 32, 20), "#777", 9, bold=True)
                for index, item in enumerate(trends):
                    self._text(painter, f"· {str(item)[:36]}",
                               self._rect(x + 16, trend_y + 22 + index * 22, w - 32, 20), "#555", 8)
            return

        if wid == "photos":
            self._fill(painter, self._rect(x, y, w, h), "#ffffff")
            photos = (self._world_state().get("house_state") or {}).get("items", {}).get("photos", [])
            if not photos:
                self._text(painter, "还没有照片", self._rect(x, y + h / 2 - 12, w, 24), "#999", 10,
                           align=Qt.AlignmentFlag.AlignCenter)
                return
            for index, photo in enumerate(photos[:8]):
                col, row = index % 4, index // 4
                px, py = x + 20 + col * 90, y + 20 + row * 90
                tile = self._rect(px, py, 70, 70)
                self._fill(painter, tile, "#d8e0d0")
                painter.setPen(QPen(QColor("#bbbbbb")))
                painter.drawRect(tile)
                raw_path = str(photo.get("img_path", ""))
                path = Path(raw_path) if os.path.isabs(raw_path) else PROJECT_ROOT / raw_path
                image = self._photo_cache.get(str(path))
                if image is None and path.exists():
                    image = QImage(str(path))
                    self._photo_cache[str(path)] = image
                if image and not image.isNull():
                    self._draw_cover(painter, image, self._rect(px + 2, py + 2, 66, 66))
                self._text(painter, str(photo.get("name", ""))[:8],
                           self._rect(px - 6, py + 72, 82, 20), "#555", 8,
                           align=Qt.AlignmentFlag.AlignCenter)
                self._content_items.append((tile, "file_open", f"photo_{index}"))
            return

        if wid == "folder":
            self._fill(painter, self._rect(x, y, w, h), "#ffffff")
            sub = self._folder_sub
            if sub:
                back = self._rect(x + 8, y + 8, 62, 22)
                self._fill(painter, back, "#e8e8e8")
                painter.setPen(QPen(QColor("#cccccc")))
                painter.drawRect(back)
                self._text(painter, "← 返回", back, "#555", 9, align=Qt.AlignmentFlag.AlignCenter)
                self._content_items.append((back, "folder_back", None))
                self._text(painter, sub, self._rect(x + 16, y + 38, w - 32, 24), "#333", 12, bold=True)
                if sub == "日记":
                    items = list(reversed([item for item in self._file_items()
                                           if item.get("type") == "日记"]))
                    if items:
                        for index, item in enumerate(items[:7]):
                            yy = y + 68 + index * 31
                            tile = self._rect(x + 12, yy, w - 24, 26)
                            self._fill(painter, tile, "#f6f3ed" if index % 2 == 0 else "#ffffff")
                            painter.setPen(QPen(QColor("#ddd5ca")))
                            painter.drawRect(tile)
                            stamp = f"{item.get('date', '')} {item.get('time', '')}".strip()
                            text = str(item.get("text") or "（没有正文）").replace("\n", " ")
                            self._text(painter, text[:27], self._rect(x + 20, yy + 2, w - 160, 20), "#3e3932", 8, bold=True)
                            self._text(painter, stamp or "时间未知", self._rect(x + w - 138, yy + 2, 112, 20), "#8b8176", 8,
                                       align=Qt.AlignmentFlag.AlignRight)
                            self._content_items.append((tile, "file_open", str(item.get("id", ""))))
                    if not items:
                        self._text(painter, "还没有日记", self._rect(x, y + 108, w, 24), "#999", 10,
                                   align=Qt.AlignmentFlag.AlignCenter)
                elif sub == "念头":
                    items = list(reversed([item for item in self._file_items()
                                           if item.get("type") == "念头"]))
                    if items:
                        for index, item in enumerate(items[:7]):
                            yy = y + 68 + index * 31
                            tile = self._rect(x + 12, yy, w - 24, 26)
                            self._fill(painter, tile, "#f6f3ed" if index % 2 == 0 else "#ffffff")
                            painter.setPen(QPen(QColor("#ddd5ca")))
                            painter.drawRect(tile)
                            stamp = f"{item.get('date', '')} {item.get('time', '')}".strip()
                            text = str(item.get("text") or "（没有正文）").replace("\n", " ")
                            self._text(painter, text[:27], self._rect(x + 20, yy + 2, w - 160, 20), "#3e3932", 8, bold=True)
                            self._text(painter, stamp or "时间未知", self._rect(x + w - 138, yy + 2, 112, 20), "#8b8176", 8,
                                       align=Qt.AlignmentFlag.AlignRight)
                            self._content_items.append((tile, "file_open", str(item.get("id", ""))))
                    if not items:
                        self._text(painter, "还没有念头", self._rect(x, y + 108, w, 24), "#999", 10,
                                   align=Qt.AlignmentFlag.AlignCenter)
                elif sub == "用户的事":
                    items = list(reversed(self._relationship_files()))
                    if items:
                        for index, item in enumerate(items[:7]):
                            yy = y + 68 + index * 31
                            tile = self._rect(x + 12, yy, w - 24, 26)
                            self._fill(painter, tile, "#f6f3ed" if index % 2 == 0 else "#ffffff")
                            painter.setPen(QPen(QColor("#ddd5ca")))
                            painter.drawRect(tile)
                            self._text(painter, str(item.get("text") or "")[:42], self._rect(x + 20, yy + 2, w - 40, 20), "#3e3932", 8)
                            self._content_items.append((tile, "file_open", str(item.get("id", ""))))
                    if not items:
                        self._text(painter, "还没有关系资料", self._rect(x, y + 108, w, 24), "#999", 10,
                                   align=Qt.AlignmentFlag.AlignCenter)
                elif sub == "读书笔记":
                    items = list(reversed(self._book_files()))
                    for index, item in enumerate(items[:7]):
                        yy = y + 68 + index * 31
                        tile = self._rect(x + 12, yy, w - 24, 26)
                        self._fill(painter, tile, "#f6f3ed" if index % 2 == 0 else "#ffffff")
                        painter.setPen(QPen(QColor("#ddd5ca")))
                        painter.drawRect(tile)
                        book = item.get("book") or {}
                        line = f"{item.get('text', '')[:24]}  {book.get('progress', 0)}%"
                        self._text(painter, line, self._rect(x + 20, yy + 2, w - 40, 20), "#3e3932", 8)
                        self._content_items.append((tile, "file_open", str(item.get("id", ""))))
                    if not items:
                        self._text(painter, "还没有读书笔记", self._rect(x, y + 108, w, 24), "#999", 10,
                                   align=Qt.AlignmentFlag.AlignCenter)
                elif sub == "收藏":
                    items = list(reversed(self._collection_files()))
                    for index, item in enumerate(items[:7]):
                        yy = y + 68 + index * 31
                        tile = self._rect(x + 12, yy, w - 24, 26)
                        self._fill(painter, tile, "#f6f3ed" if index % 2 == 0 else "#ffffff")
                        painter.setPen(QPen(QColor("#ddd5ca")))
                        painter.drawRect(tile)
                        name = str(item.get("text") or "未命名物件")[:28]
                        place = str(item.get("source") or "")
                        self._text(painter, name, self._rect(x + 20, yy + 2, w - 170, 20), "#3e3932", 8, bold=True)
                        self._text(painter, place[:18], self._rect(x + w - 150, yy + 2, 124, 20), "#8b8176", 8,
                                   align=Qt.AlignmentFlag.AlignRight)
                        self._content_items.append((tile, "file_open", str(item.get("id", ""))))
                    if not items:
                        self._text(painter, "还没有收藏", self._rect(x, y + 108, w, 24), "#999", 10,
                                   align=Qt.AlignmentFlag.AlignCenter)
                elif sub == "随笔稿":
                    items = self._chen_note_files()
                    for index, item in enumerate(items[:7]):
                        yy = y + 68 + index * 31
                        tile = self._rect(x + 12, yy, w - 24, 26)
                        self._fill(painter, tile, "#f6f3ed" if index % 2 == 0 else "#ffffff")
                        painter.setPen(QPen(QColor("#ddd5ca")))
                        painter.drawRect(tile)
                        title = str(item.get("title") or item.get("filename") or "随笔")[:24]
                        stamp = f"{item.get('date', '')} {item.get('time', '')}".strip()
                        self._text(painter, title, self._rect(x + 20, yy + 2, w - 160, 20), "#3e3932", 8, bold=True)
                        self._text(painter, stamp or "刚刚", self._rect(x + w - 138, yy + 2, 112, 20), "#8b8176", 8,
                                   align=Qt.AlignmentFlag.AlignRight)
                        self._content_items.append((tile, "file_open", str(item.get("id", ""))))
                    if not items:
                        self._text(painter, "还没有随笔稿（他独处时会在这里写点东西）", self._rect(x, y + 108, w, 24), "#999", 9,
                                   align=Qt.AlignmentFlag.AlignCenter)
                elif sub == "待办.txt":
                    self._text(painter, "点击一条待办即可标记完成。", self._rect(x + 16, y + 42, w - 32, 20), "#8b8176", 8)
                    add = self._rect(x + w - 84, y + 8, 68, 22)
                    self._fill(painter, add, "#dbe5d6")
                    painter.setPen(QPen(QColor("#c6d3c0")))
                    painter.drawRect(add)
                    self._text(painter, "+ 新待办", add, "#536b4f", 8, bold=True, align=Qt.AlignmentFlag.AlignCenter)
                    self._content_items.append((add, "todo_add", None))
                    todos = self._todo_items()
                    for index, item in enumerate(reversed(todos[-8:])):
                        actual_index = len(todos) - 1 - index
                        yy = y + 68 + index * 27
                        tile = self._rect(x + 12, yy, w - 24, 23)
                        self._fill(painter, tile, "#f6f3ed" if index % 2 == 0 else "#ffffff")
                        painter.setPen(QPen(QColor("#ddd5ca")))
                        painter.drawRect(tile)
                        mark = "✓" if item.get("done") else "○"
                        color = "#7b9a73" if item.get("done") else "#8b8176"
                        self._text(painter, mark, self._rect(x + 20, yy + 1, 20, 20), color, 10, bold=True,
                                   align=Qt.AlignmentFlag.AlignCenter)
                        text_color = "#9a9187" if item.get("done") else "#3e3932"
                        self._text(painter, str(item.get("text"))[:42], self._rect(x + 46, yy + 2, w - 66, 18), text_color, 8)
                        self._content_items.append((tile, "todo_toggle", actual_index))
                else:
                    self._text(painter, "（这里还空着）", self._rect(x + 16, y + 68, w - 32, 24), "#999", 9)
                return

            folders = [("📂", "日记"), ("📂", "念头"), ("📂", "随笔稿"), ("📂", "用户的事"),
                       ("📂", "读书笔记"), ("📂", "收藏"), ("📄", "待办.txt")]
            for index, (emoji, name) in enumerate(folders):
                col, row = index % 4, index // 4
                fx, fy = x + 20 + col * 90, y + 20 + row * 90
                tile = self._rect(fx, fy, 70, 78)
                self._text(painter, emoji, self._rect(fx, fy, 70, 42), "#555", 26,
                           align=Qt.AlignmentFlag.AlignCenter, family="Segoe UI Emoji")
                self._text(painter, name, self._rect(fx, fy + 46, 70, 22), "#555", 9,
                           align=Qt.AlignmentFlag.AlignCenter)
                self._content_items.append((tile, "folder_open", name))
            return

        if wid == "recycle":
            self._fill(painter, self._rect(x, y, w, h), "#ffffff")
            items = list(reversed(self._file_items(include_deleted=True)))
            if not items:
                self._text(painter, "（回收站是空的）", self._rect(x, y + h / 2 - 12, w, 24), "#999", 10,
                           align=Qt.AlignmentFlag.AlignCenter)
                return
            self._text(painter, "他放下的文件还在这里。", self._rect(x + 16, y + 12, w - 32, 22), "#8b8176", 9)
            for index, item in enumerate(items[:8]):
                yy = y + 44 + index * 29
                tile = self._rect(x + 12, yy, w - 24, 24)
                self._fill(painter, tile, "#f3efea" if index % 2 == 0 else "#ffffff")
                painter.setPen(QPen(QColor("#ddd5ca")))
                painter.drawRect(tile)
                text = str(item.get("text") or "（没有正文）").replace("\n", " ")
                stamp = f"{item.get('date', '')} {item.get('time', '')}".strip()
                self._text(painter, text[:30], self._rect(x + 20, yy + 2, w - 130, 18), "#6f675d", 8)
                self._text(painter, stamp or "时间未知", self._rect(x + w - 112, yy + 2, 88, 18), "#a49b91", 8,
                           align=Qt.AlignmentFlag.AlignRight)
                self._content_items.append((tile, "file_open", str(item.get("id", ""))))
            return

        if wid == "status":
            self._fill(painter, self._rect(x, y, w, h), "#f7f4ed")
            state = self._presence()
            world_name = "小世界" if state.get("current_world") == "small_world" else "桌面空间"
            place = state.get("activity_place") or ""
            activity = state.get("activity") or "暂时没有记录"
            emotion = state.get("emotion") or {}
            music = state.get("music") or {}
            rows = [
                ("所在世界", world_name + (f" · {place}" if place else "")),
                ("正在做", activity),
                ("精力", f"{state.get('energy', '?')}%"),
                ("情绪", f"{emotion.get('name', '') or emotion.get('dominant', '') or '平静'} · "
                 f"{emotion.get('value', emotion.get('intensity', 0))}"),
            ]
            try:
                memory = getattr(self.chen, "memory", None)
                memory_text = (f"事件 {len(memory.memories)} · 长期 {len(memory.long_term)} · "
                               f"重要 {len(memory.important_memories)}") if memory else "不可用"
            except Exception:
                memory_text = "不可用"
            rows.append(("记忆", memory_text))
            if music.get("title"):
                music_text = f"{music.get('title')} · {music.get('artist', '')}"
                if not music.get("playing"):
                    music_text += "（暂停）"
            else:
                music_text = "没有读取到外部播放器"
            rows.append(("音乐", music_text))
            self._text(painter, "伴生体 · 当前状态", self._rect(x + 18, y + 10, w - 36, 24), "#3e3932", 13, bold=True)
            for index, (label, value) in enumerate(rows):
                yy = y + 44 + index * 42
                self._text(painter, label, self._rect(x + 18, yy, 68, 20), "#8b8176", 8)
                self._text(painter, str(value)[:38], self._rect(x + 92, yy, w - 110, 20), "#3e3932", 9)
            self._text(painter, "最近发生的事", self._rect(x + 18, y + 292, w - 36, 20), "#8b8176", 9, bold=True)
            try:
                day_log = getattr(self.chen, "day_log", None)
                events = day_log.today_lines()[-3:] if day_log else []
            except Exception:
                events = []
            for index, event in enumerate(reversed(events)):
                ts = str(event.get("ts", ""))[-5:]
                kind = {"life": "生活", "world": "世界", "chat": "聊天", "web": "网上", "autonomy": "自主"}.get(
                    event.get("kind", ""), "记录")
                line = f"{ts} · {kind} · {str(event.get('text', ''))[:38]}"
                self._text(painter, line, self._rect(x + 18, y + 316 + index * 28, w - 36, 20), "#514a43", 8)
            if not events:
                self._text(painter, "今天还没有新的记录。", self._rect(x + 18, y + 318, w - 36, 20), "#aaa198", 8)
            self._text(painter, f"快照：{state.get('updated_at', '未知')}",
                       self._rect(x + 18, y + h - 30, w - 36, 20), "#aaa198", 8)

    # ---------- scene drawing ----------

    def _draw_icon_list(self, painter: QPainter, icons: list[tuple[str, str, str]], start_x: float,
                        text_color: str, phase: str) -> None:
        icon_size, gap = 76, 6
        for index, (emoji, label, wid) in enumerate(icons):
            ix, iy = start_x, 12 + index * (icon_size + gap)
            rect = self._rect(ix, iy, icon_size, icon_size)
            if wid in self._open_windows:
                self._fill(painter, self._rect(ix - 2, iy - 2, icon_size + 4, icon_size + 4),
                           "#2a2a3a" if phase == "night" else "#d8d8e0")
            self._text(painter, emoji, self._rect(ix, iy + 2, icon_size, 38), text_color, 26,
                       align=Qt.AlignmentFlag.AlignCenter, family="Segoe UI Emoji")
            self._text(painter, label, self._rect(ix, iy + 42, icon_size, 24), text_color, 9,
                       align=Qt.AlignmentFlag.AlignCenter)
            if wid == "inbox":
                unread = self._inbox_unread_count()
                if unread:
                    badge = self._rect(ix + 52, iy + 2, 22, 18)
                    self._fill(painter, badge, "#b44c43")
                    self._text(painter, str(min(unread, 99)), badge, "#ffffff", 8, bold=True,
                               align=Qt.AlignmentFlag.AlignCenter)
            self._icon_rects.append((rect, wid))

    def _draw_window(self, painter: QPainter, wid: str) -> None:
        layout = self._window_layouts[wid]
        x, y, w, h = layout["x"], layout["y"], layout["w"], layout["h"]
        self._fill(painter, self._rect(x + 3, y + 3, w, h), "#c8c8c8")
        self._fill(painter, self._rect(x, y, w, h), "#f0f0f0")
        painter.setPen(QPen(QColor("#aaaaaa")))
        painter.drawRect(self._rect(x, y, w, h))
        self._fill(painter, self._rect(x, y, w, 28), "#e0e0e0")
        self._text(painter, self.TITLES.get(wid, wid), self._rect(x + 10, y + 3, w - 40, 22), "#333", 9)
        close = self._rect(x + w - 24, y + 5, 18, 18)
        self._fill(painter, close, "#e06050")
        self._text(painter, "×", close, "#ffffff", 9, align=Qt.AlignmentFlag.AlignCenter)
        self._window_controls.append((close, wid))
        self._window_titlebars.append((self._rect(x, y, w, 28), wid))
        painter.save()
        painter.setClipRect(self._rect(x, y + 28, w, h - 28).toRect())
        self._draw_window_content(wid, x, y + 28, w, h - 28, painter)
        painter.restore()

    def _draw_desktop(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor("#111111"))
        width, height = float(self.width()), float(self.height())
        taskbar_h = 40.0
        draw_h = max(1.0, height - taskbar_h)
        phase = self._time_phase()
        if phase == "sleep":
            self._fill(painter, self._rect(0, 0, width, height), "#0a0a12")
            now = _dt.datetime.now()
            self._text(painter, now.strftime("%H:%M"), self._rect(0, height / 2 - 75, width, 65), "#cccccc", 48,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._text(painter, now.strftime("%Y年%m月%d日"), self._rect(0, height / 2 - 5, width, 25), "#888888", 12,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._text(painter, "他已经睡着了", self._rect(0, height / 2 + 40, width, 24), "#666666", 11,
                       align=Qt.AlignmentFlag.AlignCenter)
            return

        background = self._rect(0, 0, width, draw_h)
        if not self._wallpaper.isNull():
            self._draw_cover(painter, self._wallpaper, background)
        elif phase == "day":
            self._draw_gradient(painter, background, "#a8c8e8", "#e8dcc0")
        elif phase == "dusk":
            self._draw_gradient(painter, background, "#c89878", "#e8d0a8")
        else:
            self._draw_gradient(painter, background, "#0d1b2a", "#2a2a3a")

        text_color = "#333333" if phase in ("day", "dusk") else "#e0e0e0"
        state = self._presence()
        world_text = "小世界" if state.get("current_world") == "small_world" else "桌面空间"
        self._text(painter, f"伴生体的桌面 · 当前在{world_text}", self._rect(112, 8, 300, 24), text_color, 10)
        self._icon_rects = []
        self._draw_icon_list(painter, [("📁", "我的文件", "folder"), ("🖼️", "照片", "photos"),
                                       ("📄", "正在写的", "doc"), ("📬", "收件箱", "inbox")],
                            14, text_color, phase)
        self._draw_icon_list(painter, [("🎵", "音乐", "music"), ("🌐", "浏览器", "browser"),
                                       ("◉", "状态", "status"), ("🗑️", "回收站", "recycle")],
                            width - 76 - 14, text_color, phase)

        self._window_controls = []
        self._window_titlebars = []
        self._content_items = []
        ordered = [wid for wid in self._open_windows if wid != self._active_window]
        if self._active_window in self._open_windows:
            ordered.append(self._active_window)
        for wid in ordered:
            self._draw_window(painter, wid)

        self._fill(painter, self._rect(0, height - taskbar_h, width, taskbar_h), "#14141c")
        self._fill(painter, self._rect(8, height - taskbar_h + 6, 60, 28), "#24242c")
        self._text(painter, "⊞ 开始", self._rect(8, height - taskbar_h + 6, 60, 28), "#cccccc", 9,
                   align=Qt.AlignmentFlag.AlignCenter)
        self._taskbar_buttons = []
        x = 76.0
        for wid in self._open_windows:
            button = self._rect(x, height - taskbar_h + 6, 82, 28)
            self._fill(painter, button, "#3a4a32" if wid == self._active_window else "#22222a")
            self._text(painter, self.SHORT_LABELS.get(wid, wid), button,
                       "#ffffff" if wid == self._active_window else "#aaaaaa", 8,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._taskbar_buttons.append((button, wid))
            x += 86
        now = _dt.datetime.now()
        self._text(painter, now.strftime("%H:%M"), self._rect(width - 150, height - taskbar_h + 3, 130, 18), "#cccccc", 10,
                   align=Qt.AlignmentFlag.AlignRight)
        self._text(painter, now.strftime("%Y/%m/%d"), self._rect(width - 150, height - taskbar_h + 20, 130, 16), "#888888", 8,
                   align=Qt.AlignmentFlag.AlignRight)

    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_desktop(painter)
        painter.end()

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        if hasattr(self, "_window_layouts"):
            self._clamp_all_window_layouts()
            self.update()
        super().resizeEvent(event)

    # ---------- interaction ----------

    @staticmethod
    def _contains(rect: QRectF, point: QPointF) -> bool:
        return rect.contains(point)

    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        for rect, wid in reversed(self._window_controls):
            if self._contains(rect, point):
                if wid in self._open_windows:
                    self._open_windows.remove(wid)
                if self._active_window == wid:
                    self._active_window = self._open_windows[-1] if self._open_windows else None
                self.update()
                return
        for rect, wid in reversed(self._taskbar_buttons):
            if self._contains(rect, point):
                self._active_window = wid
                self._refresh_view()
                return
        for rect, action, arg in reversed(self._content_items):
            if self._contains(rect, point):
                if action == "folder_open":
                    self._folder_sub = str(arg)
                elif action == "folder_back":
                    self._folder_sub = None
                elif action == "inbox_all_read":
                    self._mark_all_inbox_read()
                elif action == "inbox_to_doc":
                    item_id, event_key = arg if isinstance(arg, tuple) else (arg, "")
                    self._mark_event_read(event_key)
                    self._set_draft(str(item_id))
                    if "doc" not in self._open_windows:
                        self._open_windows.append("doc")
                    self._active_window = "doc"
                elif action == "edit_draft":
                    self._open_draft_editor(str(arg))
                elif action == "file_open":
                    item_id, event_key = arg if isinstance(arg, tuple) else (arg, "")
                    self._mark_event_read(event_key)
                    self._open_file_viewer(str(item_id))
                elif action == "todo_add":
                    self._add_todo()
                elif action == "todo_toggle":
                    self._toggle_todo(int(arg))
                elif action == "open_chat":
                    self._mark_event_read(str(arg))
                    if callable(self.on_chat):
                        self.on_chat()
                elif action == "open_status":
                    self._mark_event_read(str(arg))
                    if "status" not in self._open_windows:
                        self._open_windows.append("status")
                    self._active_window = "status"
                elif action == "music_search":
                    self._open_search_dialog(mode="music")
                elif action == "browser_search":
                    self._open_search_dialog(mode="browser")
                self._refresh_view()
                return
        for rect, wid in reversed(self._window_titlebars):
            if self._contains(rect, point):
                self._active_window = wid
                layout = self._window_layouts[wid]
                self._dragging = {"wid": wid, "dx": point.x() - layout["x"], "dy": point.y() - layout["y"]}
                self.update()
                return
        for rect, wid in reversed(self._icon_rects):
            if self._contains(rect, point):
                if wid not in self._open_windows:
                    self._open_windows.append(wid)
                self._active_window = wid
                self._refresh_view()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API
        if self._dragging:
            wid = self._dragging["wid"]
            layout = self._window_layouts[wid]
            layout["x"] = event.position().x() - self._dragging["dx"]
            layout["y"] = event.position().y() - self._dragging["dy"]
            self._clamp_window_layout(layout)
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._save_desktop_state()
            self._dragging = None
        super().mouseReleaseEvent(event)

    def _open_search_dialog(self, *, mode="browser") -> None:
        dialog = DesktopSearchDialog(self.chen, mode=mode, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._search_dialog = dialog
        dialog.finished.connect(lambda _result: setattr(self, "_search_dialog", None))
        dialog.open()

