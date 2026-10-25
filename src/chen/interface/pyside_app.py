"""正式 PySide6 主界面入口。

首页由视频场景驱动；关系、生活、空间、修理台和聊天都是独立窗口，
它们使用 pyside_panels.py 的可自定义静态背景。
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from .pyside_home import PySideLivingHome
from .pyside_desktop_pet import PySideDesktopPet
from .pyside_panels import CompanionStudioPanel, LifePanel, PetPanel, RelationshipPanel, SpacePanel, StaticPanelWindow, WorkshopPanel
from .companion_profile import companion_name, is_opensource_unconfigured
from .pyside_virtual_desktop import VirtualDesktopWindow


ROOT = Path(__file__).resolve().parents[3]
TRAY_ICON = ROOT / "assets" / "pet" / "idle.png"


class _TaskbarCreatedFilter(QAbstractNativeEventFilter):
    """让 Windows Explorer 重启后重新注册托盘图标。"""

    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ctypes.c_size_t),
            ("lParam", ctypes.c_ssize_t),
            ("time", ctypes.c_uint),
            ("pt_x", ctypes.c_long),
            ("pt_y", ctypes.c_long),
        ]

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.message_id = 0
        if os.name == "nt":
            try:
                self.message_id = int(
                    ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")
                )
            except Exception:
                self.message_id = 0

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        if not self.message_id or event_type not in (
            b"windows_generic_MSG", b"windows_dispatcher_MSG"
        ):
            return False, 0
        try:
            native_message = ctypes.cast(
                int(message), ctypes.POINTER(self._MSG)
            ).contents
            if native_message.message == self.message_id:
                self.callback()
        except Exception:
            pass
        return False, 0


class PySideShell:
    def __init__(self, chen, app: QApplication):
        self.chen = chen
        self.app = app
        self.windows = {}
        self.remote_server = None
        self.remote_queue = None
        self.tray = None
        self._stopped = False
        self._pet_hidden_for_world = False
        self._native_filter = _TaskbarCreatedFilter(self._restore_tray)
        self.app.installNativeEventFilter(self._native_filter)
        self._tray_watch = QTimer(self.app)
        self._tray_watch.setInterval(5000)
        self._tray_watch.timeout.connect(self._on_periodic_shell_tick)
        self._start_services()
        self.home = PySideLivingHome(
            chen=chen,
            on_relationship=lambda: self.open_panel(
                "relationship", lambda current: RelationshipPanel(current, on_chat=self.home.open_chat)
            ),
            on_life=lambda: self.open_panel("life", LifePanel),
            on_world=lambda: self.open_panel(
                "space",
                lambda current: SpacePanel(
                    current,
                    on_desktop=lambda: self.open_panel(
                        "virtual_desktop",
                        lambda owner: VirtualDesktopWindow(owner, on_chat=self.home.open_chat),
                    ),
                ),
            ),
            on_workshop=lambda: self.open_panel("workshop", WorkshopPanel),
            on_pet=lambda: self.open_panel(
                "pet",
                lambda current: PetPanel(
                    current,
                    desktop_pet=self.desktop_pet,
                    on_chat=self.home.open_chat,
                ),
            ),
            on_studio=lambda: self.open_panel(
                "studio",
                lambda current: CompanionStudioPanel(
                    current,
                    on_applied=self._on_companion_profile_applied,
                ),
            ),
            on_close=self._hide_home_to_tray,
        )
        StaticPanelWindow.set_global_navigator({
            "relationship": lambda: self.home._dispatch("us"),
            "life": lambda: self.home._dispatch("days"),
            "space": lambda: self.home._dispatch("places"),
            "pet": lambda: self.home._dispatch("pet"),
            "studio": lambda: self.home._dispatch("studio"),
            "workshop": lambda: self.home._dispatch("workshop"),
            "chat": self.home.open_chat,
        })
        self.desktop_pet = PySideDesktopPet(
            chen=chen,
            on_chat=self.home.open_chat,
            on_interact=self._desktop_pet_interact,
            parent=self.app,
        )
        if getattr(chen, "runtime", None):
            chen.runtime.register_client("desktop_pet", self.desktop_pet)
            chen.runtime.register_client("pyside_home", self.home)
            chen.runtime.on("tool_confirmation_needed", self._on_tool_confirmation_needed)
        # 托盘程序不应因窗口短暂隐藏/关闭而让 Qt 结束事件循环；
        # 同时保留窗口对象，托盘菜单才能把同一个首页唤回。
        self.home.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.home.show()
        self._setup_tray()
        self._tray_watch.start()
        self._msg_watch = QTimer(self.app)
        self._msg_watch.setInterval(1000)
        self._msg_watch.timeout.connect(self._drain_queues)
        self._msg_watch.start()
        # show() 在 pythonw/Windows 自启动时偶尔会先进入隐藏状态，
        # 在事件循环开始后再做一次可见性修复并记录状态，便于诊断。
        QTimer.singleShot(0, self._ensure_home_visible)
        QTimer.singleShot(350, self._ensure_home_visible)
        if is_opensource_unconfigured():
            QTimer.singleShot(500, lambda: self.home._dispatch("studio"))

    def _desktop_pet_interact(self, state: str):
        """把桌宠点击接回现有关系、经历和意识观测链路。"""
        state = str(state or "idle")
        phrases = {
            "sleep": "……让我再睡一会儿。",
            "music": "这首还不错。",
            "tired": "我没事，就是有点困。",
            "world_return": "刚回来。",
            "idle": "嗯？你找我。",
        }
        phrase = phrases.get(state, "嗯？")
        runtime = getattr(self.chen, "runtime", None)
        if runtime and hasattr(runtime, "emit"):
            runtime.emit("desktop_pet_interaction", {
                "state": state,
                "phrase": phrase,
                "source": "desktop_pet",
            })
        relationship = getattr(self.chen, "relationship", None)
        if relationship and hasattr(relationship, "record_event"):
            try:
                relationship.record_event(
                    "桌宠互动",
                    closeness=0.10,
                    comfort=0.15,
                    ritual="点击桌宠说句话",
                    source="desktop_pet",
                    evidence=f"点了桌宠一下，他说：「{phrase}」",
                    event_type="interaction",
                    importance=0.25,
                )
            except Exception as exc:
                print(f"[桌宠] 关系记录失败：{exc}")
        day_log = getattr(self.chen, "day_log", None)
        if day_log and hasattr(day_log, "log"):
            try:
                day_log.log(
                    "life", f"和桌宠互动：{phrase}", world="desktop",
                    extra={"source": "desktop_pet", "state": state},
                )
            except Exception as exc:
                print(f"[桌宠] 经历记录失败：{exc}")
        workspace = getattr(self.chen, "conscious_workspace", None)
        if workspace and hasattr(workspace, "observe_decision"):
            try:
                workspace.observe_decision(
                    decision_type="desktop_pet_interaction",
                    reason=f"桌宠状态：{state}",
                    source="desktop_pet",
                    delivered=True,
                    message_available=True,
                )
            except Exception as exc:
                print(f"[桌宠] 意识观测失败：{exc}")
        try:
            self.desktop_pet.say(phrase, 2400)
        except Exception:
            pass

    def _on_periodic_shell_tick(self):
        self._ensure_tray()
        self._sync_desktop_pet_state()
        self._drain_queues()

    def _on_tool_confirmation_needed(self, event):
        tool_id = (event or {}).get("tool", "未知工具")
        workshop = self.windows.get("workshop")
        if workshop is not None and not workshop.isHidden():
            QTimer.singleShot(0, workshop.refresh)
        if self.tray is not None:
            QTimer.singleShot(
                0,
                lambda: self.tray.showMessage("伴生体请求工具确认", f"待确认工具：{tool_id}（可在修理台→权限与安全中处理）"),
            )

    def _drain_queues(self):
        """消费伴生体主动发言队列与手机端同步队列，驱动聊天窗、桌宠气泡与托盘提醒。"""
        if self._stopped or self.chen is None:
            return
        import queue as _q
        proactive_q = getattr(self.chen, "proactive_speech_queue", None)
        had_new = False
        if proactive_q is not None:
            while True:
                try:
                    msg = proactive_q.get_nowait()
                except _q.Empty:
                    break
                if not msg:
                    continue
                had_new = True
                try:
                    if getattr(self, "desktop_pet", None) is not None:
                        self.desktop_pet.say(msg, 4500)
                except Exception:
                    pass
                chat_win = getattr(self.home, "_chat_window", None)
                chat_visible = bool(chat_win and chat_win.isVisible() and chat_win.isActiveWindow())
                if not chat_visible and self.tray is not None:
                    try:
                        self.tray.showMessage("伴生体", msg)
                    except Exception:
                        pass
        if self.remote_queue is not None:
            while True:
                try:
                    kind, payload = self.remote_queue.get_nowait()
                except _q.Empty:
                    break
                if kind in ("remote_he", "remote_reply"):
                    had_new = True
                    if kind == "remote_reply" and payload and self.home.isHidden() and self.tray is not None:
                        try:
                            self.tray.showMessage("伴生体", str(payload))
                        except Exception:
                            pass
        if had_new:
            chat_win = getattr(self.home, "_chat_window", None)
            if chat_win is not None:
                try:
                    chat_win.sync()
                except Exception:
                    pass
            if hasattr(self.home, "overlay"):
                self.home.overlay.update()

    def _sync_desktop_pet_state(self):
        """让桌宠显示同一份伴生体状态；不创建第二套世界/活动状态。"""
        pet = getattr(self, "desktop_pet", None)
        chen = getattr(self, "chen", None)
        if pet is None or chen is None:
            return
        try:
            snap = chen.get_presence_snapshot(persist=False) or {}
            in_small_world = snap.get("current_world") == "small_world"
            # 小世界中的人物只由 Godot 渲染；桌宠窗口在同一期间隐藏，
            # 回到桌面世界后再恢复显示，避免出现两个伴生体。
            pet_visible = bool(getattr(getattr(pet, "pet", None), "_visible", lambda: False)())
            if in_small_world:
                if pet_visible:
                    pet.hide()
                    self._pet_hidden_for_world = True
                return
            desktop_pet_intent = None
            consume_intent = getattr(chen, "consume_desktop_pet_intent", None)
            if callable(consume_intent):
                desktop_pet_intent = consume_intent()
            if desktop_pet_intent:
                # 只响应意识层的一次性“去桌面”意图；世界状态变化本身不唤醒桌宠。
                pet.show()
                pet.set_state("idle")
                self._pet_hidden_for_world = False
            pose = str(snap.get("activity_pose", "") or "").strip().lower()
            activity = str(snap.get("activity", "") or "")
            awake = bool(snap.get("awake", True))
            if not awake or pose == "sleep":
                state = "sleep"
            elif pose in {"drink", "music", "stretch", "sit"}:
                state = pose
            elif any(word in activity for word in ("听歌", "听音乐", "音乐")):
                state = "music"
            elif any(word in activity for word in ("休息", "发呆", "趴")):
                state = "tired"
            else:
                state = "idle"
            current = getattr(getattr(pet, "pet", None), "_base_state", "")
            if current != state:
                pet.set_state(state)
            if desktop_pet_intent and awake:
                pet.play_sequence([("happy", 700), ("world_return", 2200)])
        except Exception:
            pass

    def _setup_tray(self):
        """正式 PySide 入口也创建托盘，避免自启动后只有进程没有入口。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("系统托盘不可用，PySide 窗口仍可正常使用。")
            return
        icon = QIcon(str(TRAY_ICON)) if TRAY_ICON.exists() else QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_ComputerIcon
        )
        # 托盘对象挂在 QApplication 上，首页被最小化/关闭时托盘仍然存活。
        self.tray = QSystemTrayIcon(icon, self.app)
        self.tray.setToolTip("伴生体（后台运行中）")
        menu = QMenu(self.home)
        chat_action = menu.addAction("打开独立聊天窗")
        chat_action.triggered.connect(self.home.open_chat)
        open_action = menu.addAction("打开房间首页")
        open_action.triggered.connect(self._show_home)
        pet_panel_action = menu.addAction("桌宠控制台")
        pet_panel_action.triggered.connect(lambda: self.home._dispatch("pet"))
        studio_action = menu.addAction("伴生体工坊（定制人设）")
        studio_action.triggered.connect(lambda: self.home._dispatch("studio"))
        pet_action = menu.addAction("显示/隐藏桌宠")
        pet_action.triggered.connect(self.desktop_pet.toggle)
        menu.addSeparator()
        quit_action = menu.addAction("退出伴生体")
        quit_action.triggered.connect(self.shutdown)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        print("[pyside] 系统托盘已启动")

    def _restore_tray(self):
        """Explorer 重启后，隐藏再显示一次以重新建立托盘图标。"""
        if self._stopped:
            return
        QTimer.singleShot(300, self._re_register_tray)

    def _re_register_tray(self):
        if self._stopped or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.tray is None:
            self._setup_tray()
            return
        self.tray.hide()
        self.tray.show()
        print("[pyside] 已重新注册系统托盘")

    def _ensure_tray(self):
        """托盘图标被系统隐藏/重置时，周期性补回。"""
        if self._stopped or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.tray is None:
            self._setup_tray()
        elif not self.tray.isVisible():
            self.tray.show()
            print("[pyside] 托盘图标已恢复")

    def _tray_activated(self, reason):
        print(f"[pyside] 托盘触发唤回 reason={int(reason)}")
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_home()

    def _show_home(self):
        print("[pyside] 开始唤回主窗口")
        self.home.showNormal()
        self.home.setWindowState(
            self.home.windowState() & ~Qt.WindowState.WindowMinimized
        )
        self.home.show()
        self.home.raise_()
        self.home.activateWindow()
        # Windows 的前台窗口限制可能让 Qt 的 activateWindow() 只恢复状态、
        # 却不把窗口带到桌面前面；同一线程内用原生 API 做最后一步兜底。
        if os.name == "nt":
            try:
                hwnd = int(self.home.winId())
                if hwnd:
                    user32 = ctypes.WinDLL("user32", use_last_error=True)
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
            except Exception as exc:
                print(f"[pyside] 原生唤回失败：{exc}")
        self._log_home_state("唤回后")

    def _hide_home_to_tray(self):
        """首页点关闭只收起窗口，后台服务和托盘继续运行。"""
        if self._stopped:
            return
        self.home.hide()
        if self.tray is not None:
            self.tray.show()
        print("[pyside] 首页已隐藏到系统托盘")

    def _ensure_home_visible(self):
        """在自启动环境里修复 show() 尚未落到原生窗口的情况。"""
        if self._stopped:
            return
        self._show_home()

    def _log_home_state(self, label):
        try:
            handle = int(self.home.winId())
        except Exception:
            handle = 0
        try:
            geometry = self.home.geometry().getRect()
        except Exception:
            geometry = None
        print(
            f"[pyside] {label}: visible={self.home.isVisible()} "
            f"hidden={self.home.isHidden()} handle={handle} "
            f"state={self.home.windowState().value} geometry={geometry}"
        )

    def _start_services(self):
        self.chen.start()
        remote_cfg = dict((getattr(self.chen, "config", {}) or {}).get("remote", {}) or {})
        if not bool(remote_cfg.get("enabled", False)):
            print("[remote] 手机接口默认关闭；可在修理台的设备与连接中手动开启。")
            return
        try:
            from .remote_server import RemoteServer
            import queue
            self.remote_queue = queue.Queue()
            self.remote_server = RemoteServer(
                self.chen,
                self.remote_queue,
                port=int(remote_cfg.get("port", 8765) or 8765),
                host=str(remote_cfg.get("host", "127.0.0.1") or "127.0.0.1"),
            )
            self.chen.world_bridge = self.remote_server.world_bridge
            self.remote_server.start()
            if getattr(self.chen, "runtime", None):
                self.chen.runtime.register_client("phone", self.remote_server)
        except Exception as exc:
            print(f"手机接口启动失败（不影响 PySide）：{exc}")

    def _on_companion_profile_applied(self, profile: dict):
        from .pyside_home import load_scene_settings
        try:
            from .companion_profile import normalise_interest_keywords
            interests = normalise_interest_keywords(profile.get("interests"))
            if getattr(self.chen, "internet", None):
                self.chen.internet.interests = interests
            if getattr(self.chen, "hot_trends", None):
                self.chen.hot_trends.interests = interests
        except Exception:
            pass
        cname = companion_name()
        self.home.setWindowTitle(f"{cname}的世界")
        self.home.settings = load_scene_settings()
        self.home._scene_key = None
        self.home._load_period_scene()
        if hasattr(self.home, "overlay"):
            self.home.overlay._place_buttons()
            self.home.overlay.update()
        if getattr(self.home, "_chat_window", None) is not None:
            self.home._chat_window.setWindowTitle(f"{cname} · 聊天")
            self.home._chat_window.reload_background()
            self.home._chat_window.sync()

    def open_panel(self, key, panel_cls):
        panel = self.windows.get(key)
        if panel is None or panel.isHidden():
            panel = panel_cls(self.chen)
            self.windows[key] = panel
        panel.show()
        panel.raise_()
        panel.activateWindow()

    def shutdown(self):
        if self._stopped:
            return
        self._stopped = True
        self._tray_watch.stop()
        if hasattr(self, "_msg_watch"):
            self._msg_watch.stop()
        if self.tray is not None:
            self.tray.hide()
        for window in list(self.windows.values()):
            try:
                window.close()
            except Exception:
                pass
        try:
            if self.remote_server:
                self.remote_server.stop()
        except Exception:
            pass
        try:
            self.chen.stop()
        except Exception:
            pass
        try:
            self.desktop_pet.destroy()
        except Exception:
            pass
        try:
            self.home._allow_close = True
            self.home.close()
        except Exception:
            pass
        # 托盘模式关闭了最后一个窗口后，Qt 不会自动结束事件循环。
        # 显式退出，避免留下“进程还在但没有窗口/托盘”的幽灵实例。
        self.app.quit()


def run_pyside(chen) -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    shell = PySideShell(chen, app)
    app.aboutToQuit.connect(shell.shutdown)
    return app.exec()

