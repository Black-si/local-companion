"""
UI 模块 - 伴生体的"身体外壳"
- CLI：开发者控制台（保留，显示感知/反思/错误等系统日志），用 --cli 启动
- GUI：日常交互界面（Tkinter小窗口，默认启动），大脑逻辑全部在Chen里，UI只负责显示和输入
UI 本质上只是伴生体的输出展示层，不包含核心逻辑
"""

import queue
import os
import json
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
import threading
import time
try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox, simpledialog, ttk
except ImportError:
    tk = scrolledtext = messagebox = simpledialog = ttk = None
from datetime import datetime
from chen.interface.ui_tokens import PALETTE as UI_PALETTE, SPACE as UI_SPACE, FONT as UI_FONT
from chen.paths import PROJECT_ROOT
from chen.interface.living_home import LivingHome

# 实时时间段计算（不从world_manager导入，避免循环依赖，直接内联）
def get_time_period():
    hour = datetime.now().hour
    if 5 <= hour < 8: return "清晨"
    if 8 <= hour < 11: return "上午"
    if 11 <= hour < 14: return "中午"
    if 14 <= hour < 17: return "下午"
    if 17 <= hour < 19: return "傍晚"
    if 19 <= hour < 22: return "晚上"
    return "深夜"

# ========== GUI配色（低饱和暖色系，不用紫蓝靛）==========
# ===== 伴生体的颜色：旧书页 + 雪松木，低饱和暖棕 =====
BG = "#F6F2EB"          # 窗口背景：宣纸暖白
CHAT_BG = "#FCFAF5"     # 聊天区：亮半度的纸
INPUT_BG = "#FFFFFF"    # 输入框：纯白
BTN_BG = "#C4693F"      # 主按钮：陶土珊瑚
BTN_FG = "#FCFAF5"
CHEN_NAME = "#8A5A38"   # 伴生体名字：陶土深棕
CHEN_TEXT = "#2B2620"   # 伴生体正文：暖墨
HE_NAME = "#7E7466"     # 用户的名字：暖灰
HE_TEXT = "#2B2620"     # 用户的正文
SYS_TEXT = "#ADA392"    # 系统提示：弱辅助
# 他的世界标签用深暖色调
WORLD_BG = "#2A2724"    # 世界标签背景：深暖灰
WORLD_PANEL = "#35312C" # 面板
WORLD_PANEL2 = "#3D3832"# 卡片
WORLD_TEXT = "#E8E2D6"  # 文字
WORLD_MUTED = "#9A9084" # 辅助文字
FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")


class UI:
    """用户界面 - 负责展示伴生体的输出和状态"""

    def __init__(self, mode="cli"):
        self.mode = mode  # cli / gui / desktop_pet
        self.is_running = False
        self._bg_images = {}  # 背景图缓存
        # 小世界访问化身：只存在于当前 UI 会话，不写入伴生体世界状态。
        self._visitor_pos = [0.28, 0.82]
        self._visitor_visible = False
        self._world_interaction_running = False
        self._world_interaction_last = 0.0
        self._override_place = "小院"  # 打开小世界默认显示小院，不是伴生体当前所在
        self.desktop_pet = None
        # 桌宠主动出现的本地节流状态。
        self._pet_auto_last_at = 0.0
        self._pet_auto_count = 0
        self._pet_auto_day = datetime.now().date()
        self._pet_presence_key = None

    # ==================== 通用展示（CLI用）====================

    def show(self, message):
        if self.mode == "cli":
            self._show_cli(message)
        elif self.mode == "gui":
            self._show_gui(message)
        elif self.mode == "desktop_pet":
            self._show_desktop_pet(message)
        else:
            print(message)

    def _show_cli(self, message):
        """命令行模式展示"""
        timestamp = datetime.now().strftime("%H:%M")
        print(f"\n[{timestamp}] 伴生体：{message}\n")

    def _show_gui(self, message):
        """GUI 浮窗模式（run_gui内部直接渲染，此方法预留）"""
        pass

    def _show_desktop_pet(self, message):
        """桌宠模式（待实现）"""
        pass

    def show_breathing(self):
        """呼吸点/状态指示（待实现）"""
        pass

    def show_desktop_note(self, text):
        """弹出桌面留言通知窗口（右下角，大窗口，显眼，自动消失）。"""
        try:
            import tkinter as tk

            note_win = tk.Toplevel()
            note_win.title("伴生体的留言")
            note_win.overrideredirect(True)
            note_win.attributes("-topmost", True)
            note_win.configure(bg="#0f0f1a")

            w, h = 420, 180
            sw = note_win.winfo_screenwidth()
            sh = note_win.winfo_screenheight()
            x = sw - w - 15
            y = sh - h - 50
            note_win.geometry(f"{w}x{h}+{x}+{y}")

            border = tk.Frame(note_win, bg="#4a6fa5", bd=0)
            border.pack(fill="both", expand=True, padx=2, pady=2)

            main = tk.Frame(border, bg="#1a1a2e")
            main.pack(fill="both", expand=True)

            top_bar = tk.Frame(main, bg="#16213e", height=36)
            top_bar.pack(fill="x")
            top_bar.pack_propagate(False)

            avatar_frame = tk.Frame(top_bar, bg="#16213e", width=36, height=36)
            avatar_frame.pack(side="left", padx=8)
            avatar_frame.pack_propagate(False)
            avatar = tk.Label(avatar_frame, text="沉", bg="#4a6fa5", fg="white",
                              font=("Microsoft YaHei", 12, "bold"))
            avatar.pack(fill="both", expand=True)

            tk.Label(top_bar, text="伴生体 给你留了句话", bg="#16213e", fg="#e8e8e8",
                     font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=5)

            close_btn = tk.Button(top_bar, text="×", bg="#16213e", fg="#999", bd=0,
                                  font=("Arial", 14), cursor="hand2",
                                  activebackground="#c0392b", activeforeground="white",
                                  command=note_win.destroy)
            close_btn.pack(side="right", padx=8)

            content_frame = tk.Frame(main, bg="#1a1a2e")
            content_frame.pack(fill="both", expand=True, padx=15, pady=10)

            content = tk.Label(content_frame, text=text, bg="#1a1a2e", fg="#e0e0e0",
                               font=("Microsoft YaHei", 13), wraplength=370,
                               justify="left", anchor="w")
            content.pack(fill="both", expand=True)

            bottom_bar = tk.Frame(main, bg="#16213e", height=24)
            bottom_bar.pack(fill="x", side="bottom")
            bottom_bar.pack_propagate(False)

            from datetime import datetime
            time_str = datetime.now().strftime("%H:%M")
            tk.Label(bottom_bar, text=time_str, bg="#16213e", fg="#666",
                     font=("Microsoft YaHei", 8)).pack(side="left", padx=10)

            note_win.after(15000, lambda: note_win.destroy() if note_win.winfo_exists() else None)

            note_win.attributes("-alpha", 0.0)
            def fade_in():
                try:
                    alpha = note_win.attributes("-alpha")
                    if alpha < 1.0:
                        note_win.attributes("-alpha", min(1.0, alpha + 0.1))
                        note_win.after(30, fade_in)
                except Exception:
                    pass
            note_win.after(50, fade_in)

        except Exception as e:
            print(f"弹窗失败：{e}")


    def get_user_input(self, prompt="你："):
        """获取用户输入（CLI 模式）"""
        return input(f"\n{prompt} ")

    # ==================== CLI 命令行模式（开发者控制台）====================

    def run_cli(self, chen):
        """
        命令行交互循环 - 开发者控制台
        系统日志（感知/反思/错误）打在这里；日常聊天用GUI
        """
        self.is_running = True
        print("=== 伴生体 0.2.1 - 命令行模式（开发者控制台）===")
        print("输入 'quit' 退出，输入 '/reflect' 进行每日反思\n")
        chen.start()
        while self.is_running:
            try:
                user_input = self.get_user_input()
                if user_input.lower() in ["quit", "exit", "退出"]:
                    reflect_choice = input("要进行今日反思吗？(y/n) ").strip().lower()
                    if reflect_choice in ["y", "yes", "是"]:
                        print("正在进行今日反思...")
                        reflection = chen.daily_reflection()
                        if reflection == "already":
                            print("今天已经反思过了。")
                        elif reflection:
                            print(f"\n今日反思：\n{reflection}\n")
                    self.is_running = False
                    chen.stop()
                    print("伴生体：……嗯。下次见。")
                    break
                if not user_input.strip():
                    continue
                if user_input.lower() in ["/reflect", "反思", "每日反思"]:
                    print("正在进行今日反思...")
                    reflection = chen.daily_reflection()
                    if reflection == "already":
                        print("今天已经反思过了，不重复整理。")
                    elif reflection:
                        print(f"\n今日反思：\n{reflection}\n")
                    else:
                        print("（今天没有新的了解，未生成反思）")
                    continue
                response = chen.chat(user_input)
                if response:
                    self.show(response)
                else:
                    print("（伴生体没有回应）")
            except KeyboardInterrupt:
                self.is_running = False
                chen.stop()
                print("\n伴生体：走了？……行。")
                break

    # ==================== GUI Tkinter日常界面 ====================

    def run_gui(self, chen):
        """Tkinter小窗口日常交互界面"""
        self._build_gui(chen)
        self.root.mainloop()

    def _build_gui(self, chen):
        """构建主窗口（总界面），聊天窗口延迟到点击"和伴生体说话"时打开"""
        self.is_running = True
        self.chen = chen
        self._ui_queue = queue.Queue()
        self._waiting = False
        self._closing = False
        self._quit_after_reply = False
        self.tray = None
        self._hidden = False
        self._tray_hinted = False
        self._chat_win = None  # 聊天窗口，延迟创建

        # 主窗口 = 伴生体的世界（总界面）
        root = tk.Tk()
        self._topmost_var = tk.BooleanVar(value=False)
        self._notify_var = tk.BooleanVar(value=True)
        root.title("伴生体的世界")
        root.geometry("1220x840+80+60")
        root.minsize(1080, 720)
        root.configure(bg=WORLD_BG)
        self.root = root

        self._build_main_window()
        if getattr(self.chen, "runtime", None):
            self.chen.runtime.register_client("main_ui", self)
            self.chen.runtime.on("tool_confirmation_needed", self._on_tool_confirmation_needed)

        chen.start()

        # 启动手机远程接口
        self.remote_server = None
        try:
            from chen.interface.remote_server import RemoteServer
            self.remote_server = RemoteServer(chen, self._ui_queue, port=8765)
            # 决策线程和 HTTP 客户端共用同一个短生命周期桥接状态，避免行动请求分叉。
            chen.world_bridge = self.remote_server.world_bridge
            self.remote_server.start()
            if getattr(self.chen, "runtime", None):
                self.chen.runtime.register_client("phone", self.remote_server)
        except Exception as e:
            print(f"手机接口启动失败（不影响聊天）：{e}")

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._setup_tray()
        self._poll_queue()
        self._refresh_main_view()

    # ==================== 主窗口：总界面 ====================

    def _build_main_window(self):
        """构建生活首页；后台入口仍通过修理台和既有窗口提供。"""
        root = self.root
        shell_bg = UI_PALETTE["canvas"]
        root.configure(bg=shell_bg)

        self._build_living_home()
        return

    def _build_living_home(self):
        """生活首页：场景优先，聊天/关系/生活/小世界/修理台保留正式入口。"""
        for child in self.root.winfo_children():
            child.destroy()
        self._living_home = LivingHome(
            self.root,
            self,
            self.chen,
            on_chat=self._open_chat,
            on_relationship=self._open_relationship,
            on_life=self._open_life_view,
            on_world=self._open_small_world_from_nav,
            on_workshop=self._open_plugin_center,
            on_background=self._choose_living_background,
        )
        self._living_home.pack(fill="both", expand=True)

    def _choose_living_background(self):
        """选择生活首页背景；失败时保留当前场景。"""
        if not HAS_PIL:
            messagebox.showinfo("换背景", "当前环境没有 Pillow，暂时不能更换背景。", parent=self.root)
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择生活场景背景",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp")],
        )
        if path and getattr(self, "_living_home", None):
            if not self._living_home.set_background(path):
                messagebox.showinfo("换背景", "这张图片无法作为场景背景打开。", parent=self.root)

    def _legacy_build_main_window(self):
        """旧总览界面保留作兼容参考，新的首页不再调用。"""
        root = self.root
        shell_bg = UI_PALETTE["canvas"]

        # 顶部标题区：只保留身份、时间和一个轻量刷新入口。
        bar = tk.Frame(root, bg=shell_bg, height=92)
        bar.pack(fill="x", side="top", padx=36)
        bar.pack_propagate(False)
        title_box = tk.Frame(bar, bg=shell_bg)
        title_box.pack(side="left", pady=(20, 0))
        tk.Label(title_box, text="CHEN'S WORLD", bg=shell_bg, fg=UI_PALETTE["faded"],
                 font=UI_FONT["eyebrow"]).pack(anchor="w")
        tk.Label(title_box, text="伴生体的世界", bg=shell_bg, fg=UI_PALETTE["text"],
                 font=UI_FONT["h1"]).pack(anchor="w", pady=(3, 0))
        now_box = tk.Frame(bar, bg=shell_bg)
        now_box.pack(side="right", pady=(30, 0))
        tk.Label(now_box, text=f"{get_time_period()} · {datetime.now():%H:%M}",
                 bg=shell_bg, fg=UI_PALETTE["muted"], font=UI_FONT["secondary"]).pack(side="left", padx=(0, 18))
        refresh = tk.Button(bar, text="重新看一眼  ↻", command=self._refresh_main_view,
                            relief="flat", bg=shell_bg, fg=UI_PALETTE["muted"],
                            activebackground=UI_PALETTE["sidebar"], activeforeground=UI_PALETTE["text"],
                            bd=0, padx=12, pady=7, font=UI_FONT["secondary"], cursor="hand2")
        refresh.pack(side="left")
        refresh.bind("<Enter>", lambda _e: refresh.configure(bg=UI_PALETTE["sidebar"]))
        refresh.bind("<Leave>", lambda _e: refresh.configure(bg=shell_bg))
        tk.Frame(root, bg=UI_PALETTE["line"], height=1).pack(fill="x", padx=36)

        body = tk.Frame(root, bg=shell_bg)
        body.pack(fill="both", expand=True, padx=36, pady=(20, 24))

        # 左侧：空间导航。导航本身像房间入口，不再像后台菜单。
        nav = tk.Frame(body, bg=shell_bg, width=190)
        nav.pack(side="left", fill="y", padx=(0, 18))
        nav.pack_propagate(False)
        identity = tk.Frame(nav, bg=shell_bg)
        identity.pack(fill="x", padx=18, pady=(22, 26))
        tk.Label(identity, text="沉", bg=UI_PALETTE["ink"], fg=UI_PALETTE["ink_paper"],
                 font=("Microsoft YaHei UI", 13, "bold"), width=2, pady=4).pack(side="left")
        name_box = tk.Frame(identity, bg=shell_bg)
        name_box.pack(side="left", padx=9)
        tk.Label(name_box, text="伴生体", bg=shell_bg, fg=UI_PALETTE["text"],
                 font=UI_FONT["h2"]).pack(anchor="w")
        tk.Label(name_box, text="伴生体", bg=shell_bg, fg=UI_PALETTE["faded"],
                 font=UI_FONT["caption"]).pack(anchor="w")
        tk.Label(nav, text="空 间", bg=shell_bg, fg=UI_PALETTE["faded"],
                 font=UI_FONT["caption"]).pack(anchor="w", padx=20, pady=(0, 6))
        self._nav_button(nav, "⌂  首页", self._refresh_main_view, active=True)
        self._nav_button(nav, "◌  和他说话", self._open_chat)
        self._nav_button(nav, "⌂  小世界", self._open_small_world_from_nav)
        self._nav_button(nav, "✦  桌宠", self._toggle_desktop_pet)
        tk.Label(nav, text="工 具", bg=shell_bg, fg=UI_PALETTE["faded"],
                 font=UI_FONT["caption"]).pack(anchor="w", padx=20, pady=(26, 6))
        self._nav_button(nav, "◇  插件中心", self._open_plugin_center)
        self._nav_button(nav, "♡  我们的关系", self._open_relationship)
        tk.Frame(nav, bg=UI_PALETTE["line"], height=1).pack(fill="x", pady=(28, 16))
        tk.Label(nav, text="现 在", bg=shell_bg, fg=UI_PALETTE["faded"],
                 font=UI_FONT["caption"]).pack(anchor="w", padx=20)
        self._nav_status = tk.Label(nav, text="正在读取…", bg=shell_bg, fg=UI_PALETTE["text"],
                                    justify="left", anchor="nw", wraplength=154,
                                    font=UI_FONT["secondary"])
        self._nav_status.pack(fill="x", pady=(8, 0))
        tk.Frame(body, bg=UI_PALETTE["line"], width=1).pack(side="left", fill="y", padx=(0, 30))

        # 中央：一张突出的此刻卡片 + 生活入口。
        center = tk.Frame(body, bg=shell_bg)
        center.pack(side="left", fill="both", expand=True)
        hero = tk.Frame(center, bg=UI_PALETTE["ink"], height=168)
        hero.pack(fill="x")
        hero.pack_propagate(False)
        tk.Label(hero, text="此 刻", bg=UI_PALETTE["ink"], fg=UI_PALETTE["ink_sub"],
                 font=UI_FONT["caption"]).pack(anchor="w", padx=28, pady=(26, 6))
        self._main_location = tk.Label(hero, text="", bg=UI_PALETTE["ink"], fg=UI_PALETTE["ink_paper"],
                                       anchor="w", justify="left", wraplength=700,
                                       font=("Microsoft YaHei UI", 16, "bold"))
        self._main_location.pack(anchor="w", padx=28)
        self._main_status = tk.Label(hero, text="", bg=UI_PALETTE["ink"], fg=UI_PALETTE["ink_sub"],
                                     anchor="w", font=UI_FONT["secondary"])
        self._main_status.pack(anchor="w", padx=28, pady=(8, 0))

        self._cards_frame = tk.Frame(center, bg=shell_bg)
        self._cards_frame.pack(fill="both", expand=True, pady=(16, 0))
        self._main_card_refs = {}

        # 右侧：状态和最近发生，保持窄、轻、可扫读。
        self._side_panel = tk.Frame(body, bg=shell_bg, width=230)
        self._side_panel.pack(side="right", fill="y", padx=(18, 0))
        self._side_panel.pack_propagate(False)
        self._status_cards = {}
        self._make_status_card("phone", "⌁  手机连接", "检查中…")
        self._make_status_card("pet", "✦  桌宠", "未召唤")
        self._make_status_card("dream", "☾  梦境循环", "检查中…")

        note = tk.Frame(self._side_panel, bg=UI_PALETTE["surface"], highlightthickness=1,
                        highlightbackground=UI_PALETTE["line"])
        note.pack(fill="x", pady=(24, 0))
        tk.Label(note, text="关于这里", bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                 font=UI_FONT["caption"]).pack(anchor="w", padx=16, pady=(14, 7))
        tk.Label(note, text="这里的每个地方都属于伴生体。\n有些东西会公开，有些东西只属于他自己。",
                 justify="left", bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                 font=UI_FONT["secondary"], wraplength=215).pack(anchor="w", padx=16, pady=(0, 16))

        chat_cta = tk.Button(self._side_panel, text="和他说话  →", command=self._open_chat,
                             bg=UI_PALETTE["coral"], fg="white", activebackground="#CC6A50",
                             activeforeground="white", relief="flat", bd=0,
                             anchor="center", padx=12, pady=10,
                             font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2")
        chat_cta.pack(fill="x", pady=(14, 0))
        chat_cta.bind("<Enter>", lambda _e: chat_cta.configure(bg="#CC6A50"))
        chat_cta.bind("<Leave>", lambda _e: chat_cta.configure(bg=UI_PALETTE["coral"]))

        # 侧栏下半部：最近发生的事，和主卡片/虚拟桌面共用本地状态。
        activity_panel = tk.Frame(self._side_panel, bg=UI_PALETTE["surface"], highlightthickness=1,
                                  highlightbackground=UI_PALETTE["line"])
        activity_panel.pack(fill="both", expand=True, pady=(14, 0))
        tk.Label(activity_panel, text="最近发生", bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                 font=UI_FONT["caption"]).pack(anchor="w", padx=16, pady=(14, 7))
        self._side_activity_text = tk.Label(
            activity_panel, text="暂时没有新的记录。", bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
            justify="left", anchor="nw", wraplength=180,
            font=UI_FONT["caption"])
        self._side_activity_text.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self._refresh_side_activity()

    def _make_status_card(self, key, title, value):
        card = tk.Frame(self._side_panel, bg=UI_PALETTE["surface"], highlightthickness=1,
                        highlightbackground=UI_PALETTE["line"])
        card.pack(fill="x", pady=(0, 8))
        top = tk.Frame(card, bg=UI_PALETTE["surface"])
        top.pack(anchor="w", padx=14, pady=(11, 2))
        self._status_dots = getattr(self, "_status_dots", {})
        dot = tk.Label(top, text="●", bg=UI_PALETTE["surface"], fg=UI_PALETTE["faded"],
                       font=("Microsoft YaHei UI", 7))
        dot.pack(side="left")
        self._status_dots[key] = dot
        tk.Label(top, text=title, bg=UI_PALETTE["surface"], fg=UI_PALETTE["faded"],
                 font=UI_FONT["caption"]).pack(side="left", padx=(5, 0))
        value_label = tk.Label(card, text=value, bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                               anchor="w", justify="left", wraplength=195,
                               font=UI_FONT["secondary"])
        value_label.pack(anchor="w", padx=14, pady=(0, 11))
        self._status_cards[key] = value_label

    def _nav_button(self, parent, text, command, active=False):
        shell = UI_PALETTE["canvas"]
        row = tk.Frame(parent, bg=shell)
        row.pack(fill="x", pady=1)
        tk.Frame(row, bg=UI_PALETTE["coral"] if active else shell, width=2).pack(side="left", fill="y")
        fg = UI_PALETTE["text"] if active else UI_PALETTE["muted"]
        b = tk.Button(row, text=text, command=command,
                      bg=shell, fg=fg,
                      activebackground=shell, activeforeground=UI_PALETTE["text"],
                      relief="flat", bd=0, anchor="w", padx=12, pady=8,
                      font=UI_FONT["body"], cursor="hand2")
        b.pack(side="left", fill="x", expand=True)
        b.bind("<Enter>", lambda _e: b.configure(fg=UI_PALETTE["text"]))
        b.bind("<Leave>", lambda _e: b.configure(fg=fg))
        return b

    def _open_small_world_from_nav(self):
        try:
            self._open_small_world_preview()
        except Exception as exc:
            print(f"小世界导航打开失败：{exc}")

    def _on_tool_confirmation_needed(self, event):
        """Runtime 可能来自后台线程，UI 更新必须回到 Tk 主线程。"""
        if not getattr(self, "root", None):
            return
        self.root.after(0, lambda: self._show_tool_confirmation(event))

    def _show_tool_confirmation(self, event):
        request_id = event.get("request_id")
        tool_id = event.get("tool", "未知工具")
        if not request_id or not self.root.winfo_exists():
            return
        old = getattr(self, "_tool_request_windows", {})
        if request_id in old and old[request_id].winfo_exists():
            old[request_id].lift()
            return
        self._tool_request_windows = old
        win = tk.Toplevel(self.root)
        old[request_id] = win
        win.title("需要你的允许")
        win.geometry("430x270")
        win.resizable(False, False)
        win.configure(bg=UI_PALETTE["canvas"])
        tk.Label(win, text="伴生体想使用一个工具", bg=UI_PALETTE["canvas"],
                 fg=UI_PALETTE["text"], font=UI_FONT["h1"]).pack(anchor="w", padx=24, pady=(24, 5))
        tk.Label(win, text=tool_id, bg=UI_PALETTE["canvas"], fg=UI_PALETTE["coral"],
                 font=UI_FONT["h2"]).pack(anchor="w", padx=24, pady=(0, 12))
        tk.Label(win, text="这个动作会改变外部状态，执行前需要你的明确允许。\n你可以只允许这一次，也可以直接拒绝。",
                 justify="left", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                 font=UI_FONT["secondary"]).pack(anchor="w", padx=24)
        buttons = tk.Frame(win, bg=UI_PALETTE["canvas"])
        buttons.pack(fill="x", padx=24, pady=(28, 20))

        def finish(approved):
            if win.winfo_exists():
                win.destroy()
            old.pop(request_id, None)
            runtime = getattr(self.chen, "runtime", None)
            if runtime:
                result = runtime.approve_tool(request_id, approved)
                if not result.get("ok") and result.get("status") != "denied":
                    self._append_sys(result.get("error", "工具请求未完成")) if self._chat_ready() else None

        tk.Button(buttons, text="允许一次", command=lambda: finish(True), relief="flat", bd=0,
                  bg=UI_PALETTE["coral"], fg="white", activebackground="#C96851",
                  padx=16, pady=8, cursor="hand2").pack(side="left")
        tk.Button(buttons, text="拒绝", command=lambda: finish(False), relief="flat", bd=0,
                  bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["text"], padx=16, pady=8,
                  cursor="hand2").pack(side="right")
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))

    def _side_button(self, text, command):
        b = tk.Button(self._side_panel, text=text, command=command,
                      bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"], activebackground="#F1EAE5",
                      activeforeground=UI_PALETTE["text"], relief="flat", bd=0,
                      anchor="w", padx=16, pady=13,
                      font=UI_FONT["body"], cursor="hand2")
        b.pack(fill="x", pady=4)
        b.bind("<Enter>", lambda _e: b.configure(bg="#F1EAE5"))
        b.bind("<Leave>", lambda _e: b.configure(bg=UI_PALETTE["surface"]))

    def _open_plugin_center(self):
        """插件中心：把外部设备和可选能力从主界面按钮中收拢出来。"""
        old = getattr(self, "_plugin_win", None)
        if old and old.winfo_exists():
            old.lift()
            return
        win = tk.Toplevel(self.root)
        self._plugin_win = win
        win.title("插件中心 · 伴生体")
        win.geometry("820x700")
        win.minsize(720, 580)
        win.configure(bg=UI_PALETTE["canvas"])
        head = tk.Frame(win, bg=UI_PALETTE["canvas"])
        head.pack(fill="x", padx=28, pady=(24, 8))
        tk.Label(head, text="PLUG-INS", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["faded"],
                 font=UI_FONT["eyebrow"]).pack(anchor="w")
        tk.Label(head, text="插件与能力中心", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["text"],
                 font=UI_FONT["h1"]).pack(anchor="w", pady=(3, 0))
        tk.Label(head, text="把伴生体的身体、设备和外部能力放在这里管理。",
                 bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                 font=UI_FONT["secondary"]).pack(anchor="w", pady=(6, 0))
        ability_bar = tk.Frame(win, bg=UI_PALETTE["canvas"])
        ability_bar.pack(fill="x", padx=28, pady=(4, 10))
        tk.Label(ability_bar, text="能力器官", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                 font=UI_FONT["caption"]).pack(anchor="w", pady=(0, 6))
        ability_specs = {}
        runtime = getattr(self.chen, "runtime", None)
        if runtime and runtime.tools:
            for spec in runtime.tools.describe():
                name = spec["category"]
                level = {"observe": "观察", "suggest": "建议", "execute": "执行"}.get(spec["level"], spec["level"])
                if spec.get("requires_confirmation"):
                    level += "需确认"
                ability_specs.setdefault(name, []).append(level)
        abilities = [(name, " / ".join(dict.fromkeys(levels))) for name, levels in ability_specs.items()]
        if not abilities:
            abilities = [("网络", "观察"), ("文件", "观察 / 执行"), ("电脑", "观察"),
                         ("小世界", "建议"), ("手机", "执行需确认")]
        for name, level in abilities:
            pill = tk.Frame(ability_bar, bg=UI_PALETTE["sidebar"], padx=9, pady=5)
            pill.pack(side="left", padx=(0, 7))
            tk.Label(pill, text=f"{name}  ·  {level}", bg=UI_PALETTE["sidebar"],
                     fg=UI_PALETTE["text"], font=UI_FONT["caption"]).pack()
        if runtime:
            clients = runtime.clients()
            client_text = " · ".join(f"{name} {runtime.client_status(name)}" for name in clients)
            if client_text:
                tk.Label(ability_bar, text=f"当前客户端：{client_text}", bg=UI_PALETTE["canvas"],
                         fg=UI_PALETTE["faded"], font=UI_FONT["caption"]).pack(anchor="w", pady=(7, 0))
        audit = tk.Label(head, text="查看工具记录  →", bg=UI_PALETTE["canvas"],
                         fg=UI_PALETTE["muted"], font=UI_FONT["secondary"], cursor="hand2")
        audit.pack(anchor="w", pady=(8, 0))
        audit.bind("<Button-1>", lambda _e: self._open_tool_audit())
        audit.bind("<Enter>", lambda _e: audit.configure(fg=UI_PALETTE["coral"]))
        audit.bind("<Leave>", lambda _e: audit.configure(fg=UI_PALETTE["muted"]))
        search_row = tk.Frame(head, bg=UI_PALETTE["surface"], highlightthickness=1,
                              highlightbackground=UI_PALETTE["line"], padx=12, pady=10)
        search_row.pack(fill="x", pady=(12, 0))
        tk.Label(search_row, text="搜索伴生体工作区", bg=UI_PALETTE["surface"],
                 fg=UI_PALETTE["text"], font=UI_FONT["secondary"]).pack(side="left", padx=(0, 10))
        query_var = tk.StringVar()
        query_entry = tk.Entry(search_row, textvariable=query_var, relief="flat", bd=0,
                               bg=UI_PALETTE["canvas"], fg=UI_PALETTE["text"],
                               insertbackground=UI_PALETTE["text"], font=UI_FONT["body"])
        query_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        search_button = tk.Button(search_row, text="搜索", relief="flat", bd=0,
                                  bg=UI_PALETTE["coral"], fg="white",
                                  activebackground="#C96851", activeforeground="white",
                                  padx=14, pady=6, cursor="hand2",
                                  command=lambda: self._search_workspace(query_var.get()))
        search_button.pack(side="right")
        query_entry.bind("<Return>", lambda _e: self._search_workspace(query_var.get()))
        scroll_outer = tk.Frame(win, bg=UI_PALETTE["canvas"])
        scroll_outer.pack(fill="both", expand=True, padx=22, pady=(8, 24))
        canvas = tk.Canvas(scroll_outer, bg=UI_PALETTE["canvas"], highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(scroll_outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=UI_PALETTE["canvas"])
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(body_window, width=e.width))
        def on_mousewheel(event):
            if canvas.winfo_exists():
                canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")

        def bind_scroll(widget):
            """卡片里的文字和按钮也要把滚轮交给插件列表。"""
            widget.bind("<MouseWheel>", on_mousewheel, add="+")
            for child in widget.winfo_children():
                bind_scroll(child)

        bind_scroll(canvas)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        # 能力中心：浅色能力区，避免插件页再次变成深色控制台。
        capability_box = tk.Frame(body, bg=UI_PALETTE["sage_bg"], padx=18, pady=14)
        capability_box.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 12))
        tk.Label(capability_box, text="能力中心", bg=UI_PALETTE["sage_bg"], fg=UI_PALETTE["text"],
                 font=UI_FONT["h2"]).pack(anchor="w")
        tk.Label(capability_box, text="工具是他的手，权限决定手能伸到哪里。",
                 bg=UI_PALETTE["sage_bg"], fg=UI_PALETTE["muted"], font=UI_FONT["caption"]).pack(anchor="w", pady=(3, 10))
        capability_row = tk.Frame(capability_box, bg=UI_PALETTE["sage_bg"])
        capability_row.pack(fill="x")
        capability_icons = {"网络": "◎", "文件": "□", "电脑": "▣", "小世界": "◇", "手机": "⌁"}
        capabilities = []
        try:
            capabilities = list(getattr(getattr(self, "chen", None), "tools", None).describe())
        except Exception:
            capabilities = []
        categories = []
        for item in capabilities:
            category = item.get("category")
            if category and category not in categories:
                categories.append(category)
        for category in categories:
            items = [item for item in capabilities if item.get("category") == category]
            levels = {item.get("level") for item in items}
            level_text = "可观察" if "observe" in levels else ("可建议" if "suggest" in levels else "可执行")
            if any(item.get("requires_confirmation") for item in items):
                level_text += " · 需确认"
            cell = tk.Frame(capability_row, bg=UI_PALETTE["surface"], highlightthickness=1,
                            highlightbackground=UI_PALETTE["line"], padx=10, pady=8)
            cell.pack(side="left", fill="x", expand=True, padx=(0, 6))
            tk.Label(cell, text=f"{capability_icons.get(category, '·')}  {category}",
                     bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"], font=UI_FONT["secondary"]).pack(anchor="w")
            tk.Label(cell, text=level_text, bg=UI_PALETTE["surface"], fg=UI_PALETTE["sage"],
                     font=UI_FONT["caption"]).pack(anchor="w", pady=(3, 0))

        try:
            from chen.interface.plugin_registry import get_plugins
            plugins = get_plugins(self)
        except Exception as exc:
            print(f"插件列表加载失败：{exc}")
            plugins = []
        for i, plugin in enumerate(plugins):
            r, c = divmod(i, 2)
            card = tk.Frame(body, bg=UI_PALETTE["surface"], highlightthickness=1,
                            highlightbackground=UI_PALETTE["line"], padx=18, pady=16)
            card.grid(row=r + 1, column=c, sticky="nsew", padx=8, pady=8)
            tk.Label(card, text=plugin["name"], bg=UI_PALETTE["surface"],
                     fg=UI_PALETTE["text"], font=UI_FONT["h2"]).pack(anchor="w")
            tk.Label(card, text=plugin["desc"], bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                     justify="left", wraplength=280, anchor="w",
                     font=UI_FONT["secondary"]).pack(anchor="w", pady=(9, 12))
            bottom = tk.Frame(card, bg=UI_PALETTE["surface"])
            bottom.pack(fill="x")
            status_color = UI_PALETTE["sage"] if plugin["status"] in ("已接入", "已启动", "可用") else UI_PALETTE["muted"]
            badge = tk.Frame(bottom, bg=UI_PALETTE["surface"])
            badge.pack(side="left")
            tk.Label(badge, text="●", bg=UI_PALETTE["surface"], fg=status_color,
                     font=("Microsoft YaHei UI", 7)).pack(side="left")
            tk.Label(badge, text=f" {plugin['status']}", bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                     font=UI_FONT["caption"]).pack(side="left")
            manage = tk.Label(bottom, text="管理 →", bg=UI_PALETTE["surface"],
                              fg=UI_PALETTE["muted"], font=UI_FONT["secondary"], cursor="hand2")
            manage.pack(side="right")
            manage.bind("<Button-1>", lambda _e, action=plugin["action"]: action())
            manage.bind("<Enter>", lambda _e, widget=manage: widget.configure(fg=UI_PALETTE["coral"]))
            manage.bind("<Leave>", lambda _e, widget=manage: widget.configure(fg=UI_PALETTE["muted"]))
            bind_scroll(card)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def _search_workspace(self, query):
        query = str(query or "").strip()
        if not query:
            messagebox.showinfo("搜索工作区", "先输入要查找的关键词。",
                                parent=getattr(self, "_plugin_win", self.root))
            return
        runtime = getattr(self.chen, "runtime", None)
        if not runtime:
            messagebox.showerror("搜索不可用", "Runtime 尚未初始化。",
                                 parent=getattr(self, "_plugin_win", self.root))
            return
        result = runtime.observe_workspace(query, max_results=30)
        if not result.get("ok"):
            messagebox.showerror("搜索失败", result.get("error", "未知错误"),
                                 parent=getattr(self, "_plugin_win", self.root))
            return
        payload = result.get("result") or {}
        matches = payload.get("matches", []) if isinstance(payload, dict) else []
        win = tk.Toplevel(self.root)
        win.title(f"工作区搜索 · {query}")
        win.geometry("700x520")
        win.minsize(560, 400)
        win.configure(bg=UI_PALETTE["canvas"])
        tk.Label(win, text=f"搜索结果 · {query}", bg=UI_PALETTE["canvas"],
                 fg=UI_PALETTE["text"], font=UI_FONT["h1"]).pack(anchor="w", padx=24, pady=(22, 4))
        tk.Label(win, text="范围：伴生体 workspace · 只读", bg=UI_PALETTE["canvas"],
                 fg=UI_PALETTE["muted"], font=UI_FONT["secondary"]).pack(anchor="w", padx=24, pady=(0, 14))
        output = scrolledtext.ScrolledText(win, wrap=tk.WORD, relief="flat", bd=0,
                                           bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                                           font=UI_FONT["secondary"], padx=14, pady=12)
        output.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        if not matches:
            output.insert("end", "没有找到匹配内容。")
        else:
            output.insert("end", f"找到 {len(matches)} 条匹配\n\n")
            for item in matches:
                line = f"{item.get('path', '')}"
                if item.get("line"):
                    line += f" · 第 {item['line']} 行"
                output.insert("end", line + "\n", "path")
                if item.get("text"):
                    output.insert("end", item["text"] + "\n\n", "snippet")
            if payload.get("truncated"):
                output.insert("end", "结果已达到上限，缩小关键词范围可查看更多。")
        output.tag_config("path", foreground=UI_PALETTE["coral"], font=UI_FONT["body"])
        output.tag_config("snippet", foreground=UI_PALETTE["muted"])
        output.configure(state="disabled")
        tk.Button(win, text="关闭", command=win.destroy, relief="flat", bd=0,
                  bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["text"], padx=16, pady=7,
                  cursor="hand2").pack(anchor="e", padx=24, pady=(0, 20))

    def _plugin_dialog(self, title, text):
        messagebox.showinfo(title, text, parent=getattr(self, "_plugin_win", self.root))

    def _open_tool_audit(self):
        """显示最近工具调用摘要，不展示令牌和原始敏感参数。"""
        win = tk.Toplevel(self.root)
        win.title("工具调用记录 · 伴生体")
        win.geometry("620x430")
        win.configure(bg=UI_PALETTE["canvas"])
        tk.Label(win, text="工具调用记录", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["text"],
                 font=UI_FONT["h1"]).pack(anchor="w", padx=24, pady=(22, 4))
        tk.Label(win, text="显示最近 30 次调用的工具、状态和安全摘要；查询内容与文件正文不会写入日志。",
                 bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                 font=UI_FONT["secondary"]).pack(anchor="w", padx=24, pady=(0, 12))
        view = scrolledtext.ScrolledText(win, state=tk.DISABLED, wrap=tk.WORD,
                                         bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                                         relief="flat", bd=0, font=UI_FONT["secondary"],
                                         padx=12, pady=10)
        view.pack(fill="both", expand=True, padx=24, pady=(0, 22))
        lines = []
        try:
            audit_path = self.chen.base_dir / "logs" / "tool_calls.jsonl"
            if audit_path.exists():
                for raw in audit_path.read_text(encoding="utf-8").splitlines()[-30:]:
                    try:
                        item = json.loads(raw)
                        status = item.get("status") or ("成功" if item.get("ok") else "失败")
                        details = item.get("details") or {}
                        target = details.get("filename")
                        query_len = details.get("query_length")
                        summary = f" · {target}" if target else (f" · 搜索词长度 {query_len}" if query_len else "")
                        error = item.get("error")
                        suffix = f"\n    {str(error)[:160]}" if error else ""
                        lines.append(f"{str(item.get('at', ''))[-8:]}  {item.get('tool', '')}  {status}{summary}{suffix}")
                    except Exception:
                        continue
        except Exception:
            pass
        view.config(state=tk.NORMAL)
        view.insert("1.0", "\n".join(reversed(lines)) if lines else "暂时还没有工具调用记录。")
        view.config(state=tk.DISABLED)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def _open_phone_plugin(self):
        remote = getattr(self, "remote_server", None)
        if not remote:
            self._plugin_dialog("手机连接电脑", "手机接口尚未启动。请确认伴生体主界面已经正常启动。")
            return
        token = getattr(remote, "token", "")
        port = getattr(remote, "port", 8765)
        ip = remote._lan_ip()
        url = f"http://{ip}:{port}/mobile#token={token}"

        win = tk.Toplevel(self.root)
        win.title("手机连接电脑 · 伴生体")
        win.geometry("480x360")
        win.configure(bg=UI_PALETTE["canvas"])

        tk.Label(win, text="手机连接电脑", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["text"],
                 font=UI_FONT["h1"]).pack(anchor="w", padx=24, pady=(22, 4))
        tk.Label(win, text="让 iPhone / iPad 与电脑连接同一 Wi‑Fi，在 Safari 打开下方入口即可进入新版伴生界面。",
                 bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"], wraplength=430, justify="left",
                 font=UI_FONT["secondary"]).pack(anchor="w", padx=24, pady=(0, 14))

        card = tk.Frame(win, bg=UI_PALETTE["surface"], highlightthickness=1,
                        highlightbackground=UI_PALETTE["line"])
        card.pack(fill="x", padx=24, pady=(0, 14))

        tk.Label(card, text="Safari 完整入口（含 #token）：", bg=UI_PALETTE["surface"],
                 fg=UI_PALETTE["muted"], font=UI_FONT["secondary"]).pack(anchor="w", padx=14, pady=(12, 2))
        url_entry = tk.Entry(card, bg=UI_PALETTE["canvas"], fg=UI_PALETTE["text"],
                             relief="flat", font=UI_FONT["secondary"])
        url_entry.insert(0, url)
        url_entry.configure(state="readonly")
        url_entry.pack(fill="x", padx=14, pady=(0, 10), ipady=5)

        tk.Label(card, text="iOS 快捷指令请求头 X-Chen-Token：", bg=UI_PALETTE["surface"],
                 fg=UI_PALETTE["muted"], font=UI_FONT["secondary"]).pack(anchor="w", padx=14, pady=(0, 2))
        tok_entry = tk.Entry(card, bg=UI_PALETTE["canvas"], fg=UI_PALETTE["text"],
                             relief="flat", font=UI_FONT["secondary"])
        tok_entry.insert(0, token)
        tok_entry.configure(state="readonly")
        tok_entry.pack(fill="x", padx=14, pady=(0, 14), ipady=5)

        btn_row = tk.Frame(win, bg=UI_PALETTE["canvas"])
        btn_row.pack(fill="x", padx=24, pady=(4, 16))

        def _copy_val(val, btn, orig_label):
            try:
                win.clipboard_clear()
                win.clipboard_append(val)
                win.update()
                btn.config(text="✓ 已复制")
                win.after(1600, lambda: btn.exists() if hasattr(btn, "exists") else btn.config(text=orig_label))
            except Exception:
                pass

        copy_url_btn = tk.Button(
            btn_row, text="复制 Safari 链接", relief="flat", bd=0,
            bg=UI_PALETTE["coral"], fg="#FFFFFF", padx=14, pady=7, cursor="hand2"
        )
        copy_url_btn.config(command=lambda: _copy_val(url, copy_url_btn, "复制 Safari 链接"))
        copy_url_btn.pack(side="left", padx=(0, 8))

        copy_tok_btn = tk.Button(
            btn_row, text="复制 Token", relief="flat", bd=0,
            bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["text"], padx=14, pady=7, cursor="hand2"
        )
        copy_tok_btn.config(command=lambda: _copy_val(token, copy_tok_btn, "复制 Token"))
        copy_tok_btn.pack(side="left")

        tk.Button(btn_row, text="关闭", command=win.destroy, relief="flat", bd=0,
                  bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["muted"], padx=14, pady=7,
                  cursor="hand2").pack(side="right")

    def _open_mcp_plugin(self):
        config = getattr(self.chen, "config", {}) or {}
        enabled = bool((config.get("tools", {}) or {}).get("mcp_enabled", False))
        state = "已启用" if enabled else "默认关闭"
        self._plugin_dialog("MCP 工具接入", f"状态：{state}\n\nMCP 外部工具必须先经过伴生体的工具层和权限检查。默认关闭，不会让外部工具直接控制伴生体。")

    def _open_dream_plugin(self):
        dream = getattr(self.chen, "dream", None)
        state = getattr(dream, "state", {}) if dream else {}
        last = state.get("last_dream", "还没有记录")
        self._plugin_dialog("梦境循环", f"状态：已接入\n上次做梦：{last}\n\n梦境在后台夜间循环中运行，不会因为打开这个页面额外调用模型。")

    def _open_desktop_pet_plugin(self):
        """桌宠的统一管理入口：召唤和主动行为设置归到同一个插件。"""
        win = tk.Toplevel(self.root)
        win.title("桌宠插件")
        win.geometry("420x300")
        win.configure(bg=UI_PALETTE["canvas"])
        tk.Label(win, text="桌宠", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["text"],
                 font=UI_FONT["h1"]).pack(anchor="w", padx=24, pady=(24, 4))
        tk.Label(win, text="桌宠的显示、动作和主动出现都在这里管理。",
                 bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                 font=UI_FONT["secondary"]).pack(anchor="w", padx=24, pady=(0, 18))
        row = tk.Frame(win, bg=UI_PALETTE["canvas"])
        row.pack(fill="x", padx=24, pady=8)
        tk.Button(row, text="召唤 / 隐藏桌宠", command=self._toggle_desktop_pet,
                  bg=UI_PALETTE["coral"], fg="white", relief="flat", bd=0,
                  padx=14, pady=9).pack(side="left")
        tk.Button(row, text="主动行为设置", command=self._open_pet_settings,
                  bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"], relief="flat", bd=0,
                  padx=14, pady=9).pack(side="left", padx=10)
        tk.Label(win, text="主动行为设置已经归入桌宠插件，主导航不再单独重复显示。",
                 bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                 font=UI_FONT["caption"], wraplength=360, justify="left").pack(anchor="w", padx=24, pady=16)

    def _open_music_plugin(self):
        music = getattr(self.chen, "music", None)
        state = music.snapshot() if music and hasattr(music, "snapshot") else {}
        self._plugin_dialog("音乐状态", f"状态：只读镜像\n当前：{state.get('title') or '暂时没有记录'}\n\n不会修改 QQ 音乐数据。")

    def _open_browser_plugin(self):
        self._plugin_dialog("浏览器感知", "状态：按配置运行\n\n只记录允许的应用和活动摘要，不读取网页内容。")

    def _open_weather_plugin(self):
        self._plugin_dialog("天气与互联网感知", "状态：低频运行\n\n天气用于环境和小世界；互联网感知使用白名单来源，并限制检查频率。")

    def _toggle_desktop_pet(self):
        """显示/隐藏桌宠；桌宠只显示立绘，不创建额外模型进程。"""
        try:
            try:
                if self.chen.get_current_world() == "small_world":
                    if self.desktop_pet and self.desktop_pet._visible():
                        self.desktop_pet.hide()
                    return
            except Exception:
                pass
            if self.desktop_pet is None:
                from chen.interface.desktop_pet import DesktopPet
                asset = os.path.join(str(PROJECT_ROOT),
                                     "assets", "chen_sprite.png")
                self.desktop_pet = DesktopPet(self.root, asset, on_click=self._open_chat,
                                              state_assets=self._desktop_pet_asset_map(),
                                              context_actions=self._desktop_pet_context_actions(),
                                              position_path=os.path.join(
                                                  str(PROJECT_ROOT),
                                                  "workspace", "desktop_pet_position.json"),
                                              on_interact=self._desktop_pet_interact)
            if getattr(self.chen, "runtime", None):
                self.chen.runtime.register_client("desktop_pet", self.desktop_pet)
            if self.desktop_pet._visible():
                self.desktop_pet.hide()
            else:
                try:
                    state = self.chen.get_presence_snapshot(persist=False)
                    self.desktop_pet.set_state(self._pet_state_from_presence(state))
                except Exception:
                    self.desktop_pet.set_state("idle")
                self.desktop_pet.show()
                self._pet_auto_last_at = time.time()
                self._request_desktop_pet_message()
        except Exception as e:
            print(f"桌宠启动失败：{e}")

    def _desktop_pet_asset_map(self):
        base = os.path.join(str(PROJECT_ROOT),
                            "assets", "pet")
        return {name: os.path.join(base, f"{name}.png") for name in
                ("idle", "blink", "blink_half", "world_return", "world_return_2",
                 "music", "music_2", "tired", "tired_rub", "sleep",
                 "side_walk_1", "side_walk_2", "side_walk_3", "side_walk_4",
                 "happy", "surprised", "drag", "sit", "stretch", "stretch_up",
                 "sit_stretch")}

    def _desktop_pet_context_actions(self):
        return {
            "chat": self._open_chat,
            "walk": self._desktop_pet_walk,
            "small_world": self._desktop_pet_return_to_world,
            "sleep": self._desktop_pet_sleep,
            "reset_position": self._desktop_pet_reset_position,
        }

    def _desktop_pet_reset_position(self):
        if not self.desktop_pet or not self.desktop_pet.window or not self.desktop_pet.window.winfo_exists():
            return
        pet = self.desktop_pet
        sw, sh = pet.window.winfo_screenwidth(), pet.window.winfo_screenheight()
        x = sw - pet.window.winfo_width() - 30
        y = sh - pet.window.winfo_height() - 80
        pet.window.geometry(f"+{x}+{y}")
        pet._idle_origin = (x, y)
        pet._save_position()

    def _desktop_pet_interact(self, state):
        phrases = {
            "sleep": "……让我再睡一会儿。",
            "music": "这首还不错。",
            "tired": "我没事，就是有点困。",
            "world_return": "刚回来。",
            "idle": "嗯？你找我。",
        }
        phrase = phrases.get(state, "嗯？")
        try:
            self.chen.relationship.record_event(
                "桌宠互动",
                closeness=0.10,
                comfort=0.15,
                ritual="点击桌宠说句话",
                source="desktop_pet",
                evidence=f"点了他一下，他说：「{phrase}」",
                importance=0.25,
            )
        except Exception:
            pass
        self._show_pet_bubble(phrase)

    def _show_pet_bubble(self, text):
        old = getattr(self, "_pet_bubble", None)
        if old and old.winfo_exists():
            old.destroy()
        pet = self.desktop_pet
        if not pet or not pet.window or not pet.window.winfo_exists():
            return
        bubble = tk.Toplevel(self.root)
        self._pet_bubble = bubble
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        bubble.configure(bg="#5a4a3b")
        label = tk.Label(bubble, text=text, bg="#5a4a3b", fg="#fffaf2",
                         padx=12, pady=7, font=("Microsoft YaHei UI", 9))
        label.pack()
        bubble.update_idletasks()
        x = max(8, pet.window.winfo_x() - bubble.winfo_width() - 8)
        y = max(8, pet.window.winfo_y() + 20)
        bubble.geometry(f"+{x}+{y}")
        bubble.after(3000, lambda: bubble.destroy() if bubble.winfo_exists() else None)

    def _desktop_pet_walk(self):
        pet = self.desktop_pet
        if not pet or not pet.window or not pet.window.winfo_exists() or pet._locked:
            return
        pet.set_state("idle")
        x, y = pet.window.winfo_x(), pet.window.winfo_y()
        sw = pet.window.winfo_screenwidth()
        target_x = max(20, min(sw - pet.window.winfo_width() - 20, x - 180 if x > sw // 2 else x + 180))
        pet.start_walk(target_x, y)

    def _desktop_pet_sleep(self):
        if self.desktop_pet and self.desktop_pet.window and self.desktop_pet.window.winfo_exists():
            self.desktop_pet.set_state("sleep")

    def _desktop_pet_return_to_world(self):
        try:
            self.chen.enter_small_world()
            if self.desktop_pet:
                self.desktop_pet.hide()
            self._refresh_side_activity()
            self._refresh_main_view()
        except Exception as exc:
            print(f"[桌宠] 返回小世界失败：{exc}")

    def _pet_settings(self):
        defaults = {"enabled": True, "quiet_start": 23, "quiet_end": 8,
                    "daily_limit": 2, "cooldown_minutes": 90}
        stored = (getattr(self.chen, "config", {}) or {}).get("desktop_pet", {})
        return {**defaults, **stored}

    def _save_pet_settings(self, settings):
        config = getattr(self.chen, "config", None)
        if not isinstance(config, dict):
            return
        config["desktop_pet"] = settings
        try:
            path = self.chen.base_dir / "system" / "config.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(config, ensure_ascii=False, indent=4), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[桌宠] 设置保存失败：{exc}")

    def _open_pet_settings(self):
        if getattr(self, "_pet_settings_win", None) and self._pet_settings_win.winfo_exists():
            self._pet_settings_win.lift()
            return
        settings = self._pet_settings()
        win = tk.Toplevel(self.root)
        self._pet_settings_win = win
        win.title("桌宠主动行为")
        win.geometry("360x300")
        win.resizable(False, False)
        win.configure(bg=WORLD_PANEL)
        tk.Label(win, text="桌宠主动行为", bg=WORLD_PANEL, fg=WORLD_TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=22, pady=(20, 14))
        enabled = tk.BooleanVar(value=bool(settings["enabled"]))
        tk.Checkbutton(win, text="允许伴生体主动出现在桌面", variable=enabled,
                       bg=WORLD_PANEL, fg=WORLD_TEXT, selectcolor=WORLD_PANEL2,
                       activebackground=WORLD_PANEL, activeforeground=WORLD_TEXT,
                       font=("Microsoft YaHei UI", 10)).pack(anchor="w", padx=22, pady=4)
        form = tk.Frame(win, bg=WORLD_PANEL)
        form.pack(fill="x", padx=22, pady=10)
        tk.Label(form, text="安静时段", bg=WORLD_PANEL, fg=WORLD_MUTED).grid(row=0, column=0, sticky="w", pady=7)
        start = tk.Spinbox(form, from_=0, to=23, width=4)
        start.delete(0, "end"); start.insert(0, settings["quiet_start"])
        start.grid(row=0, column=1, padx=8)
        tk.Label(form, text="点到", bg=WORLD_PANEL, fg=WORLD_MUTED).grid(row=0, column=2)
        end = tk.Spinbox(form, from_=0, to=23, width=4)
        end.delete(0, "end"); end.insert(0, settings["quiet_end"])
        end.grid(row=0, column=3, padx=8)
        tk.Label(form, text="每天最多出现次数", bg=WORLD_PANEL, fg=WORLD_MUTED).grid(row=1, column=0, columnspan=2, sticky="w", pady=7)
        limit = tk.Spinbox(form, from_=0, to=10, width=4)
        limit.delete(0, "end"); limit.insert(0, settings["daily_limit"])
        limit.grid(row=1, column=2, padx=8)
        tk.Label(win, text="设置为 0 即关闭自动出现；手动点击按钮仍可召唤。",
                 bg=WORLD_PANEL, fg=WORLD_MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=22)

        def save():
            try:
                data = {"enabled": bool(enabled.get()),
                        "quiet_start": max(0, min(23, int(start.get()))),
                        "quiet_end": max(0, min(23, int(end.get()))),
                        "daily_limit": max(0, min(10, int(limit.get()))),
                        "cooldown_minutes": 90}
            except ValueError:
                return
            self._save_pet_settings(data)
            win.destroy()

        tk.Button(win, text="保存", command=save, bg=BTN_BG, fg=BTN_FG,
                  relief="flat", bd=0, padx=24, pady=8,
                  font=("Microsoft YaHei UI", 10)).pack(pady=18)

    def _request_desktop_pet_message(self):
        """桌宠出现时低频请求一句 API 短句，不阻塞主界面。"""
        if getattr(self, "_pet_message_pending", False):
            return
        self._pet_message_pending = True

        def worker():
            try:
                message = self.chen.get_desktop_pet_message(cooldown_minutes=30)
                if message:
                    self.root.after(0, lambda: self.show_desktop_note(f"伴生体：{message}"))
            except Exception as exc:
                print(f"[桌宠] 短句请求失败：{exc}")
            finally:
                self._pet_message_pending = False

        threading.Thread(target=worker, daemon=True, name="desktop-pet-api").start()

    def _small_world_card_text(self):
        """小世界卡片底部文字：显示天气、时间段、伴生体是否在里面、熟悉度"""
        ws = self.chen.world.get_state()
        weather = ws.get("weather", "")
        period = get_time_period()  # 实时计算
        parts = []
        if weather:
            parts.append(weather)
        if period:
            parts.append(period)
        prefix = "·".join(parts) if parts else ""
        if self.chen.world.is_in_small_world():
            place = ws.get("activity_place", "")
            # 显示当前地点的熟悉度
            fam = ws.get("familiarity", {}).get(place, 0)
            fam_text = f"（熟悉{fam}）" if fam > 0 else ""
            # 显示本次发现的东西
            disc = ws.get("current_discoveries", [])
            disc_text = f" · 捡到{len(disc)}样" if disc else ""
            return (prefix + " · 他在" + place + fam_text + disc_text) if prefix else ("他在" + place + fam_text + disc_text)
        # 桌面时显示上次访问摘要
        summary = ws.get("last_visit_summary", "")
        if summary:
            return (prefix + " · " + summary[:18]) if prefix else summary[:18]
        return (prefix + " · 可以进去") if prefix else "可以进去"
    def _refresh_main_view(self):
        """刷新总界面的空间卡片"""
        if getattr(self, "_living_home", None) is not None:
            self._living_home.refresh()
            if getattr(self, "is_running", False) and not getattr(self, "_living_refresh_scheduled", False):
                self._living_refresh_scheduled = True
                self.root.after(30000, self._periodic_living_refresh)
            return
        if not hasattr(self, "_cards_frame"):
            return
        try:
            state = self.chen.get_presence_snapshot(persist=False)
            world = "小世界" if state.get("current_world") == "small_world" else "桌面空间"
            life = getattr(self.chen, "life", None)
            if life and not life.state.get("awake", True):
                nav_text = f"{world}\n正在睡觉"
            else:
                nav_text = f"{world}\n{(state.get('activity') or '安静待着')[:18]}"
            if getattr(self, "_nav_status", None) and self._nav_status.winfo_exists():
                self._nav_status.config(text=nav_text)
        except Exception:
            pass
        try:
            view = self.chen.world_store.visible_items("用户")
            open_items = view["open"]
            locked_count = len(view["locked"]) + len(view["hidden"])
            all_count = len([x for x in self.chen.world_store.all_items()
                             if x.get("status") != "deleted"])
            self._main_status.config(text=f"留下了 {all_count} 样东西")
            ws = self.chen.world.get_state()
            loc = "小世界" if ws.get("current_world") == "small_world" else "桌面空间"
            loc_text = f"· 伴生体现在在：{loc}"
            # 生活节律：显示他此刻的状态（睡觉/在做什么）
            try:
                _life = getattr(self.chen, "life", None)
                if _life:
                    _awake = _life.state.get("awake", True)
                    _act = _life.state.get("activity", "")
                    if not _awake:
                        loc_text += " · 在睡觉"
                    elif _act and loc == "桌面空间":
                        loc_text += f" · {_act}"
            except Exception:
                pass
            # 情绪向量：心里有分量最重的那股情绪就带上
            try:
                _emo = getattr(self.chen, "emotion", None)
                if _emo:
                    _note = _emo.dominant_note()
                    if _note:
                        loc_text += f" · {_note}"
            except Exception:
                pass
            # 活了多久：满一天才开始显示，静静累计
            try:
                _lived = getattr(self.chen, "life", None)
                if _lived:
                    _ld = _lived.lived_desc()
                    if _ld:
                        loc_text += f" · {_ld}"
            except Exception:
                pass
            self._main_location.config(text=loc_text)
        except Exception:
            open_items, locked_count, all_count = [], 0, 0

        relation = getattr(self.chen, "relationship", None)
        relation_data = getattr(relation, "data", {}) if relation else {}
        relation_stage = relation_data.get("stage", "熟悉的陪伴者")
        relation_count = int(relation_data.get("interaction_count", 0) or 0)
        relation_days = relation.companionship_days() if relation and hasattr(relation, "companionship_days") else 1
        entries = [
            ("⊞", "他的桌面", "桌面是他的常驻空间", "desktop",
             self._env_headline_text()),
            ("◈", "他的生活", "日记、琐碎和念头", "life",
             f"{len(open_items)} 个公开条目 · {locked_count} 个暂时不可见"),
            ("◇", "小世界", "另一个可以去的地方", "small_world",
             self._small_world_card_text()),
            ("▣", "私人", "只属于他自己的部分", "private", "有些东西只属于他自己"),
            ("♡", "我们的关系", "共同生活留下的温度", "relationship",
             f"{relation_stage} · 相伴 {relation_days} 天 · {relation_count} 次记录"),
        ]
        for col in range(2):
            self._cards_frame.grid_columnconfigure(col, weight=1)
        # 首页卡片保持紧凑的三行结构，避免底部卡片被撑成很长的纵向页面。
        # 行高由内容自然决定，只保留适度的最小高度。
        for row in range(3):
            self._cards_frame.grid_rowconfigure(row, weight=1, minsize=100)
        card_layout = {
            "desktop": (0, 0, 2, 1),
            "life": (0, 1, 1, 1),
            "small_world": (1, 1, 1, 1),
            "private": (2, 0, 1, 1),
            "relationship": (2, 1, 1, 1),
        }
        for i, (icon, title, desc, kind, count_text) in enumerate(entries):
            r, c, rowspan, colspan = card_layout.get(kind, (i // 2, i % 2, 1, 1))
            refs = self._main_card_refs.get(kind)
            if not refs or not refs["frame"].winfo_exists():
                self._make_card(r, c, icon, title, desc, kind, count_text, rowspan=rowspan, colspan=colspan)
            else:
                refs["icon"].config(text=icon)
                refs["title"].config(text=title)
                refs["desc"].config(text=desc)
                refs["count"].config(text=count_text)

        # 后台生命循环可能在主界面打开后自动进出小世界，定期重建卡片，
        # 避免“主界面显示在小世界、预览页却显示不在”的过期状态。
        if getattr(self, "is_running", False) and not getattr(self, "_main_refresh_scheduled", False):
            self._main_refresh_scheduled = True
            self.root.after(30000, self._periodic_main_refresh)

    def _periodic_main_refresh(self):
        self._main_refresh_scheduled = False
        if getattr(self, "is_running", False) and not getattr(self, "_closing", False):
            self._refresh_side_activity()
            self._refresh_main_view()
            self._maybe_auto_show_pet()

    def _periodic_living_refresh(self):
        self._living_refresh_scheduled = False
        if getattr(self, "is_running", False) and not getattr(self, "_closing", False):
            if getattr(self, "_living_home", None) is not None:
                self._living_home.refresh()
                self._refresh_side_activity()
                self._maybe_auto_show_pet()
                self.root.after(30000, self._periodic_living_refresh)
            else:
                self._refresh_main_view()
                self._maybe_auto_show_pet()

    @staticmethod
    def _pet_state_from_presence(state):
        music = state.get("music") or {}
        energy = state.get("energy")
        if not state.get("awake", True):
            return "sleep"
        if isinstance(energy, (int, float)) and energy <= 30:
            return "tired"
        if music.get("playing"):
            return "music"
        # 挥手是返程事件的短动作，不能因历史摘要仍存在而反复触发。
        return "idle"

    def _maybe_auto_show_pet(self):
        """状态发生明显变化时，让桌宠低频主动出现。"""
        now = datetime.now()
        if self._pet_auto_day != now.date():
            self._pet_auto_day = now.date()
            self._pet_auto_count = 0
        try:
            state = self.chen.get_presence_snapshot(persist=False)
        except Exception:
            return
        in_small_world = state.get("current_world") == "small_world"
        if in_small_world:
            # 小世界人物只由 Godot 渲染；旧 Tk 界面也不能同时显示桌宠。
            if self.desktop_pet and self.desktop_pet._visible():
                self.desktop_pet.hide()
            return
        if self.desktop_pet and self.desktop_pet.window and self.desktop_pet.window.winfo_exists():
            self.desktop_pet.set_state(self._pet_state_from_presence(state))
        music = state.get("music") or {}
        key = (
            state.get("current_world", "desktop"),
            state.get("activity_place", ""),
            bool(music.get("playing")),
            bool(state.get("awake", True)),
        )
        previous = self._pet_presence_key
        self._pet_presence_key = key
        settings = self._pet_settings()
        if not settings.get("enabled", True):
            return
        if previous is None:
            return
        # 支持跨午夜安静时段，例如 23 点到 8 点。
        quiet_start = int(settings.get("quiet_start", 23))
        quiet_end = int(settings.get("quiet_end", 8))
        in_quiet = ((quiet_start <= now.hour < quiet_end) if quiet_start < quiet_end
                    else (now.hour >= quiet_start or now.hour < quiet_end))
        if in_quiet:
            return
        daily_limit = max(0, int(settings.get("daily_limit", 2)))
        cooldown = max(1, int(settings.get("cooldown_minutes", 90)))
        if self._pet_auto_count >= daily_limit or time.time() - self._pet_auto_last_at < cooldown * 60:
            return
        if self._chat_win and self._chat_win.winfo_exists():
            return
        consume_intent = getattr(self.chen, "consume_desktop_pet_intent", None)
        desktop_pet_intent = consume_intent() if callable(consume_intent) else None
        if self.desktop_pet and self.desktop_pet._visible():
            return
        # 世界切换本身不等于“想去用户的桌面”；只有意识层的一次性意图才触发。
        returned = bool(desktop_pet_intent)
        if not returned:
            return
        try:
            if self.desktop_pet is None:
                from chen.interface.desktop_pet import DesktopPet
                asset = os.path.join(str(PROJECT_ROOT),
                                     "assets", "chen_sprite.png")
                self.desktop_pet = DesktopPet(self.root, asset, on_click=self._open_chat,
                                              state_assets=self._desktop_pet_asset_map(),
                                              context_actions=self._desktop_pet_context_actions(),
                                              position_path=os.path.join(
                                                  str(PROJECT_ROOT),
                                                  "workspace", "desktop_pet_position.json"),
                                              on_interact=self._desktop_pet_interact)
            reason = "意识想回到桌面"
            self.desktop_pet.set_state(self._pet_state_from_presence(state))
            self.desktop_pet.show()
            if returned and state.get("awake", True):
                self.desktop_pet.play_sequence([
                    ("happy", 700), ("world_return", 2200)
                ])
            self._pet_auto_last_at = time.time()
            self._pet_auto_count += 1
            try:
                self.chen.day_log.log("life", f"桌宠主动出现：{reason}",
                                      world=state.get("current_world", "desktop"))
            except Exception:
                pass
            self._request_desktop_pet_message()
        except Exception as exc:
            print(f"[桌宠] 主动出现失败：{exc}")

    def _refresh_side_activity(self):
        """刷新主界面右侧的最近活动摘要。"""
        try:
            remote = getattr(self, "remote_server", None)
            self._status_cards["phone"].config(text="已连接局域网" if remote and remote._server else "未启动")
            pet = getattr(self, "desktop_pet", None)
            pet_on = bool(pet and pet._visible())
            self._status_cards["pet"].config(text="正在桌面上" if pet_on else "未召唤")
            dream = getattr(getattr(self.chen, "dream", None), "state", {})
            self._status_cards["dream"].config(text=f"上次：{dream.get('last_dream', '还没有记录')}")
            if getattr(self, "_status_dots", None):
                self._status_dots["phone"].config(fg=UI_PALETTE["sage"] if remote and remote._server else UI_PALETTE["faded"])
                self._status_dots["pet"].config(fg=UI_PALETTE["sage"] if pet_on else UI_PALETTE["faded"])
                self._status_dots["dream"].config(fg=UI_PALETTE["sage"] if dream else UI_PALETTE["faded"])
        except Exception:
            pass
        label = getattr(self, "_side_activity_text", None)
        if not label or not label.winfo_exists():
            return
        lines = []
        try:
            state = self.chen.get_presence_snapshot(persist=False)
            world = "小世界" if state.get("current_world") == "small_world" else "桌面空间"
            place = state.get("activity_place") or ""
            activity = state.get("activity") or "暂时没有活动"
            lines.append(f"现在：{world}{(' · ' + place) if place else ''}")
            lines.append(activity[:30])
        except Exception:
            pass
        try:
            day_log = getattr(self.chen, "day_log", None)
            recent = day_log.today_lines()[-3:] if day_log else []
            for rec in reversed(recent):
                ts = str(rec.get("ts", ""))[-5:]
                text = str(rec.get("text", ""))[:27]
                lines.append(f"{ts} · {text}")
        except Exception:
            pass
        label.config(text="\n".join(lines) if lines else "暂时没有新的记录。")

    def _make_card(self, r, c, icon, title, desc, kind, count_text, rowspan=1, colspan=1):
        surf = UI_PALETTE["surface"]
        f = tk.Frame(self._cards_frame, bg=surf, highlightthickness=1,
                     highlightbackground=UI_PALETTE["line"], cursor="hand2")
        f.grid(row=r, column=c, rowspan=rowspan, columnspan=colspan, sticky="nsew", padx=6, pady=6)
        inner = tk.Frame(f, bg=surf)
        inner.pack(anchor="w", padx=20, pady=(18, 0), fill="x")
        icon_label = tk.Label(inner, text=icon, bg=surf, fg=UI_PALETTE["faded"], font=("Segoe UI Symbol", 13))
        icon_label.pack(side="left")
        title_label = tk.Label(inner, text=title, bg=surf, fg=UI_PALETTE["text"], font=UI_FONT["h2"])
        title_label.pack(side="left", padx=(8, 0))
        desc_label = tk.Label(f, text=desc, bg=surf, fg=UI_PALETTE["muted"],
                              justify="left", anchor="w", wraplength=280, height=2,
                              font=UI_FONT["secondary"])
        desc_label.pack(anchor="w", padx=18, pady=(5, 4))
        count_label = tk.Label(f, text=count_text, bg=surf, fg=UI_PALETTE["faded"],
                               anchor="w", wraplength=280, height=2, font=UI_FONT["caption"])
        count_label.pack(anchor="w", padx=18, pady=(0, 14))
        self._main_card_refs[kind] = {"frame": f, "icon": icon_label, "title": title_label, "desc": desc_label, "count": count_label}
        for w in (f, inner, icon_label, title_label, desc_label, count_label):
            w.bind("<Button-1>", lambda _e, k=kind: self._open_card(k))
            w.bind("<Enter>", lambda _e, frame=f: frame.configure(highlightbackground=UI_PALETTE["coral"]))
            w.bind("<Leave>", lambda _e, frame=f: frame.configure(highlightbackground=UI_PALETTE["line"]))

    def _open_card(self, kind):
        if kind == "desktop":
            self._open_desktop_view()
        elif kind == "life":
            self._open_life_view()
        elif kind == "small_world":
            self._open_small_world_preview()
        elif kind == "private":
            self._open_private()
        elif kind == "relationship":
            self._open_relationship()
        else:
            messagebox.showinfo("还没长出来", "这个空间暂时还没有做。\n先让它空着。")

    # ---------- 虚拟桌面 V1：他此刻所处的整个数字环境 ----------

    def _env_headline_text(self):
        try:
            env = getattr(self.chen, "environment", None)
            if env:
                s = env.load_persisted() or env.latest
                if s:
                    bits = [f"{s.get('weekday','')}{s.get('period','')}{s.get('time','')}"]
                    if s.get("weather"):
                        bits.append(str(s["weather"]))
                    _life = s.get("life") or {}
                    if _life.get("awake") is False:
                        bits.append("他在睡觉")
                    elif _life.get("activity"):
                        bits.append(str(_life["activity"]))
                    return " · ".join(bits)
        except Exception:
            pass
        return "他的环境"

    def _open_desktop_view(self):
        """虚拟桌面：Canvas视觉化。壁纸随时间，图标可点，窗口展示他在做什么。"""
        import datetime as _dt
        win = tk.Toplevel(self.root)
        win.title("伴生体 · 他的桌面")
        win.geometry("900x600")
        win.minsize(700, 450)
        win.configure(bg="#111")
        canvas = tk.Canvas(win, highlightthickness=0, bg="#111")
        canvas.pack(fill="both", expand=True)

        # 桌面状态
        desk_state = {
            "open_windows": [],      # 已打开窗口id
            "active_window": None,   # 当前置顶窗口
            "dragging": None,        # 正在拖动的窗口
            "content_items": [],     # 窗口内容里的可点击项 (x0,y0,x1,y1,action,arg)
            "folder_sub": None,      # 文件夹窗口当前子视图
            "photo_refs": [],        # 照片缩略图的 Tk 引用，防止被回收
        }
        # 加载壁纸
        from PIL import Image as _PILImage
        _assets = os.path.join(str(PROJECT_ROOT), "assets")
        _wp_path = os.path.join(_assets, "desktop_wallpaper.png")
        try:
            desk_state["wallpaper"] = _PILImage.open(_wp_path)
        except Exception:
            desk_state["wallpaper"] = None
        # 窗口位置/尺寸
        win_layouts = {
            "doc":     {"x": 200, "y": 50, "w": 420, "h": 340},
            "music":   {"x": 280, "y": 70, "w": 300, "h": 380},
            "browser": {"x": 170, "y": 40, "w": 480, "h": 380},
            "folder":  {"x": 220, "y": 60, "w": 400, "h": 320},
            "photos":  {"x": 230, "y": 60, "w": 400, "h": 320},
            "recycle": {"x": 240, "y": 80, "w": 380, "h": 300},
            "status":  {"x": 260, "y": 70, "w": 420, "h": 430},
        }

        def _time_phase():
            """根据真实时间和伴生体状态返回 day/dusk/night/sleep"""
            try:
                env = getattr(self.chen, "environment", None)
                s = env.load_persisted() or env.latest if env else {}
                life = s.get("life") or {}
                if life.get("awake") is False:
                    return "sleep"
            except Exception:
                pass
            h = _dt.datetime.now().hour
            if 6 <= h < 17:
                return "day"
            elif 17 <= h < 19:
                return "dusk"
            else:
                return "night"

        def _gradient(cv, x0, y0, x1, y1, top_color, bot_color, steps=40):
            """用水平条模拟垂直渐变"""
            def hx(c):
                return c
            tr, tg, tb = int(top_color[1:3],16), int(top_color[3:5],16), int(top_color[5:7],16)
            br, bg, bb = int(bot_color[1:3],16), int(bot_color[3:5],16), int(bot_color[5:7],16)
            h_total = y1 - y0
            for i in range(steps):
                f = i / steps
                r = int(tr + (br-tr)*f); g = int(tg+(bg-tg)*f); b = int(tb+(bb-tb)*f)
                color = f"#{r:02x}{g:02x}{b:02x}"
                sy = y0 + int(h_total * i/steps)
                ey = y0 + int(h_total * (i+1)/steps)
                cv.create_rectangle(x0, sy, x1, ey, fill=color, outline="")

        def _draw_window_content(wid, x, y, w, h, cv):
            """绘制各窗口的内容区域"""
            if wid == "doc":
                # 文档：从world_store找最近的日记/念头
                text = "（他现在没有在写东西）"
                current_activity = ""
                try:
                    ws_items = self.chen.world_store.all_items()
                    recent = [i for i in ws_items if i.get("type") in ("日记","念头") and i.get("status")!="deleted"]
                    if recent:
                        text = recent[-1].get("text","")
                    life = getattr(self.chen, "life", None)
                    if life:
                        current_activity = life.state.get("activity", "")
                except Exception:
                    pass
                cv.create_rectangle(x+8, y+8, x+w-8, y+h-8, fill="#fff", outline="#ddd")
                if current_activity:
                    cv.create_text(x+16, y+16, text=f"现在：{current_activity[:28]}",
                                   fill="#777", font=("Microsoft YaHei UI", 8), anchor="w")
                # 分行绘制
                lines = text.split("\n")
                for i, line in enumerate(lines[:14]):
                    cv.create_text(x+16, y+42+i*18, text=line[:28], fill="#333",
                                   font=("Microsoft YaHei UI", 10), anchor="w")

            elif wid == "music":
                # 音乐播放器
                cv.create_rectangle(x, y, x+w, y+h, fill="#1e1e26", outline="")
                activity = ""
                try:
                    life = getattr(self.chen, "life", None)
                    activity = life.state.get("activity", "") if life else ""
                except Exception:
                    pass
                tracks = [
                    ("月光边境", "林海"), ("城南花已开", "三亩地"),
                    ("Luv Letter", "DJ OKAWARI"), ("用户曾说过的那首", "未知艺术家"),
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
                    playing = any(k in activity for k in ("听歌", "音乐", "耳机"))
                    track_index = 1 if "耳机" in activity else (2 if "音乐" in activity else 0)
                    title, artist = tracks[track_index]
                else:
                    track_index = next((i for i, (name, _) in enumerate(tracks)
                                        if name == title), -1)
                cv.create_text(x+w//2, y+50, text="🌙", font=("Segoe UI Emoji", 36))
                cv.create_text(x+w//2, y+100, text=title, fill="#fff",
                               font=("Microsoft YaHei UI", 12, "bold"))
                cv.create_text(x+w//2, y+120, text=artist, fill="#888",
                               font=("Microsoft YaHei UI", 9))
                # 进度条
                cv.create_rectangle(x+30, y+145, x+w-30, y+148, fill="#444", outline="")
                cv.create_rectangle(x+30, y+145, x+30+int((w-60)*0.45), y+148, fill="#7a9b6a", outline="")
                cv.create_text(x+w//2, y+170, text=("⏮  ⏸  ⏭" if playing else "⏮  ▶  ⏭"), fill="#aaa",
                               font=("Segoe UI Emoji", 14))
                # 播放列表
                playlist = [f"{name} - {who}" for name, who in tracks]
                if title and all(title not in song for song in playlist):
                    playlist.insert(0, f"{title} - {artist or '未知艺术家'}")
                    track_index = 0
                for i,song in enumerate(playlist):
                    sy = y+200+i*24
                    if i==track_index and playing:
                        cv.create_text(x+20, sy, text="●", fill="#7a9b6a", font=("",8), anchor="w")
                    cv.create_text(x+36, sy, text=song, fill="#7a9b6a" if i == track_index and playing else "#aaa",
                                   font=("Microsoft YaHei UI", 9), anchor="w")

            elif wid == "browser":
                # 浏览器
                cv.create_rectangle(x, y, x+w, y+28, fill="#e8e8e8", outline="")
                cv.create_rectangle(x+60, y+5, x+w-20, y+23, fill="#fff", outline="#ccc")
                cv.create_text(x+68, y+14, text="他的最近活动", fill="#666",
                               font=("Microsoft YaHei UI", 8), anchor="w")
                cv.create_rectangle(x, y+28, x+w, y+h, fill="#fff", outline="")
                try:
                    env = getattr(self.chen,"environment",None)
                    s = env.load_persisted() or env.latest if env else {}
                    activity = (s.get("life") or {}).get("activity","")
                except Exception:
                    activity = ""
                cv.create_text(x+16, y+50, text=activity or "（当前没有记录中的活动）",
                               fill="#333", font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
                records = []
                try:
                    day_log = getattr(self.chen, "day_log", None)
                    records = day_log.today_lines()[-6:] if day_log else []
                except Exception:
                    records = []
                if records:
                    cv.create_text(x+16, y+82, text="今天留下的痕迹", fill="#777",
                                   font=("Microsoft YaHei UI", 9, "bold"), anchor="w")
                    for i, rec in enumerate(reversed(records[-4:])):
                        ts = str(rec.get("ts", ""))[-5:]
                        kind = {"life": "生活", "world": "世界", "chat": "聊天", "web": "网上"}.get(
                            rec.get("kind", ""), "记录")
                        line = f"{ts} · {kind} · {str(rec.get('text', ''))[:34]}"
                        cv.create_text(x+16, y+106+i*25, text=line, fill="#555",
                                       font=("Microsoft YaHei UI", 8), anchor="w")
                else:
                    cv.create_text(x+16, y+88, text="今天还没有新的活动记录。",
                                   fill="#999", font=("Microsoft YaHei UI", 9), anchor="w")
                # 热点只读展示：来自本地环境快照，不在桌面里直接联网。
                try:
                    trends = (s.get("trends") or [])[:3]
                    trend_source = s.get("trends_source") or "热点"
                except Exception:
                    trends, trend_source = [], "热点"
                if trends:
                    trend_y = y + 218
                    cv.create_text(x+16, trend_y, text=f"他注意到的 · {trend_source}",
                                   fill="#777", font=("Microsoft YaHei UI", 9, "bold"), anchor="w")
                    for i, item in enumerate(trends):
                        cv.create_text(x+16, trend_y+24+i*22, text=f"· {str(item)[:36]}",
                                       fill="#555", font=("Microsoft YaHei UI", 8), anchor="w")

            elif wid in ("folder","photos"):
                # 文件夹/照片网格
                cv.create_rectangle(x, y, x+w, y+h, fill="#fff", outline="")
                if wid == "photos":
                    photos = self.chen.world.get_state().get("house_state",{}).get("items",{}).get("photos",[])
                    if photos:
                        desk_state["photo_refs"] = []
                        for i,p in enumerate(photos[:8]):
                            col,row = i%4, i//4
                            px = x+20+col*90
                            py = y+20+row*90
                            drawn = False
                            if HAS_PIL:
                                try:
                                    raw_path = p.get("img_path", "")
                                    photo_path = raw_path if os.path.isabs(raw_path) else os.path.join(
                                        str(PROJECT_ROOT), raw_path)
                                    with _PILImage.open(photo_path) as source:
                                        thumb = source.convert("RGB")
                                        thumb.thumbnail((68, 68), _PILImage.LANCZOS)
                                        photo = ImageTk.PhotoImage(thumb)
                                    desk_state["photo_refs"].append(photo)
                                    cv.create_rectangle(px, py, px+70, py+70, fill="#d8e0d0", outline="#bbb")
                                    cv.create_image(px+35, py+35, image=photo, anchor="center")
                                    drawn = True
                                except Exception:
                                    pass
                            if not drawn:
                                cv.create_rectangle(px,py,px+70,py+70,fill="#d8e0d0",outline="#bbb")
                            cv.create_text(px+35,py+80,text=p.get("name","")[:8],fill="#555",
                                          font=("Microsoft YaHei UI",8))
                    else:
                        cv.create_text(x+w//2,y+h//2,text="还没有照片",fill="#999",
                                      font=("Microsoft YaHei UI",10))
                else:
                    sub = desk_state.get("folder_sub")
                    if sub:
                        # 子视图：显示内容 + 返回
                        cv.create_rectangle(x+8, y+8, x+70, y+30, fill="#e8e8e8", outline="#ccc")
                        cv.create_text(x+39, y+19, text="← 返回", fill="#555",
                                      font=("Microsoft YaHei UI", 9))
                        desk_state["content_items"].append(
                            (x+8, y+8, x+70, y+30, "folder_back", None))
                        cv.create_text(x+16, y+50, text=sub, fill="#333",
                                      font=("Microsoft YaHei UI", 12, "bold"), anchor="w")
                        # 内容
                        if sub == "日记":
                            try:
                                ws_items = [i for i in self.chen.world_store.all_items()
                                           if i.get("type")=="日记" and i.get("status")!="deleted"]
                                for i,it in enumerate(ws_items[-8:]):
                                    cv.create_text(x+16, y+78+i*22, text=("· "+it.get("text","")[:26]),
                                                  fill="#555", font=("Microsoft YaHei UI",9), anchor="w")
                                if not ws_items:
                                    cv.create_text(x+w//2,y+120,text="还没有日记",fill="#999",
                                                  font=("Microsoft YaHei UI",10))
                            except Exception:
                                pass
                        elif sub == "用户的事":
                            cv.create_text(x+16, y+80, text="（关于用户的记录，后续接入）", fill="#999",
                                          font=("Microsoft YaHei UI",9), anchor="w")
                        elif sub == "读书笔记":
                            books = self.chen.world.get_state().get("house_state",{}).get("items",{}).get("books",[])
                            for i,b in enumerate(books[:8]):
                                cv.create_text(x+16,y+78+i*22,
                                              text=("· "+b.get("name","")+"  "+str(b.get("progress",0))+"%"),
                                              fill="#555",font=("Microsoft YaHei UI",9),anchor="w")
                            if not books:
                                cv.create_text(x+w//2,y+120,text="还没有读书笔记",fill="#999",
                                              font=("Microsoft YaHei UI",10))
                        elif sub == "收藏":
                            collections = getattr(self.chen.world, "collections", []) or []
                            for i, item in enumerate(collections[-8:][::-1]):
                                name = str(item.get("name", "未命名物件"))[:20]
                                place = str(item.get("place", ""))
                                suffix = f" · {place}" if place else ""
                                cv.create_text(x+16, y+78+i*24, text=f"· {name}{suffix}",
                                               fill="#555", font=("Microsoft YaHei UI", 9), anchor="w")
                            if not collections:
                                cv.create_text(x+w//2, y+120, text="还没有收藏",
                                               fill="#999", font=("Microsoft YaHei UI", 10))
                        else:
                            cv.create_text(x+16, y+80, text="（这里还空着）", fill="#999",
                                          font=("Microsoft YaHei UI",9), anchor="w")
                    else:
                        # 网格视图
                        folders = [("📂","日记"),("📂","用户的事"),("📂","读书笔记"),
                                   ("📂","收藏"),("📄","待办.txt")]
                        for i,(em,name) in enumerate(folders):
                            col,row = i%4, i//4
                            fx = x+20+col*90
                            fy = y+20+row*90
                            cv.create_text(fx+35,fy+30,text=em,font=("Segoe UI Emoji",28))
                            cv.create_text(fx+35,fy+62,text=name,fill="#555",
                                          font=("Microsoft YaHei UI",9))
                            desk_state["content_items"].append(
                                (fx,fy,fx+70,fy+78,"folder_open",name))

            elif wid == "recycle":
                cv.create_rectangle(x, y, x+w, y+h, fill="#fff", outline="")
                cv.create_text(x+w//2, y+h//2, text="（回收站是空的）", fill="#999",
                              font=("Microsoft YaHei UI", 10))

            elif wid == "status":
                cv.create_rectangle(x, y, x+w, y+h, fill="#f7f4ed", outline="")
                try:
                    state = self.chen.get_presence_snapshot(persist=False)
                except Exception:
                    state = {}
                world_name = "小世界" if state.get("current_world") == "small_world" else "桌面空间"
                place = state.get("activity_place") or ""
                activity = state.get("activity") or "暂时没有记录"
                emotion = state.get("emotion") or {}
                music = state.get("music") or {}
                rows = [
                    ("所在世界", world_name + (f" · {place}" if place else "")),
                    ("正在做", activity),
                    ("精力", f"{state.get('energy', '?')}%"),
                    ("情绪", f"{emotion.get('name', '') or '平静'} · {emotion.get('value', 0)}"),
                ]
                try:
                    memory = getattr(self.chen, "memory", None)
                    memory_text = (f"事件 {len(memory.memories)} · "
                                   f"长期 {len(memory.long_term)} · "
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
                cv.create_text(x+18, y+20, text="伴生体 · 当前状态", fill="#3e3932",
                               font=("Microsoft YaHei UI", 13, "bold"), anchor="w")
                for i, (label, value) in enumerate(rows):
                    yy = y+58+i*42
                    cv.create_text(x+18, yy, text=label, fill="#8b8176",
                                   font=("Microsoft YaHei UI", 8), anchor="w")
                    cv.create_text(x+92, yy, text=str(value)[:38], fill="#3e3932",
                                   font=("Microsoft YaHei UI", 9), anchor="w")
                cv.create_text(x+18, y+302, text="最近发生的事", fill="#8b8176",
                               font=("Microsoft YaHei UI", 9, "bold"), anchor="w")
                try:
                    day_log = getattr(self.chen, "day_log", None)
                    recent_events = day_log.today_lines()[-3:] if day_log else []
                except Exception:
                    recent_events = []
                for i, event in enumerate(reversed(recent_events)):
                    ts = str(event.get("ts", ""))[-5:]
                    kind = {"life": "生活", "world": "世界", "chat": "聊天", "web": "网上"}.get(
                        event.get("kind", ""), "记录")
                    text = f"{ts} · {kind} · {str(event.get('text', ''))[:38]}"
                    cv.create_text(x+18, y+328+i*28, text=text, fill="#514a43",
                                   font=("Microsoft YaHei UI", 8), anchor="w")
                if not recent_events:
                    cv.create_text(x+18, y+330, text="今天还没有新的记录。", fill="#aaa198",
                                   font=("Microsoft YaHei UI", 8), anchor="w")
                cv.create_text(x+18, y+h-18, text=f"快照：{state.get('updated_at', '未知')}",
                               fill="#aaa198", font=("Microsoft YaHei UI", 8), anchor="w")

        def draw_desktop():
            canvas.delete("all")
            w = canvas.winfo_width() or 900
            h = canvas.winfo_height() or 600
            phase = _time_phase()
            taskbar_h = 40
            draw_h = h - taskbar_h
            # 窗口刚创建或正在调整大小时，Tk 可能暂时返回小于任务栏高度的尺寸。
            # 保证后续图片缩放和裁剪始终使用正数高度。
            draw_h = max(1, draw_h)

            # 壁纸
            if phase == "sleep":
                canvas.create_rectangle(0, 0, w, h, fill="#0a0a12", outline="")
            else:
                wp = desk_state.get("wallpaper")
                if wp:
                    # cover模式缩放裁剪
                    draw_h_wp = draw_h
                    ir = wp.width/wp.height; ar = w/draw_h_wp
                    if ir > ar:
                        nh = draw_h_wp; nw = int(draw_h_wp*ir)
                    else:
                        nw = w; nh = int(w/ir)
                    resized = wp.resize((max(1,nw),max(1,nh)), _PILImage.LANCZOS)
                    left = max(0,(nw-w)//2); top = max(0,(nh-draw_h_wp)//2)
                    resized = resized.crop((left,top,left+w,top+draw_h_wp))
                    photo = ImageTk.PhotoImage(resized)
                    desk_state["wp_photo"] = photo
                    canvas.create_image(0,0,image=photo,anchor="nw")
                else:
                    # 无壁纸时回退渐变
                    if phase == "day":
                        _gradient(canvas, 0, 0, w, draw_h, "#a8c8e8", "#e8dcc0")
                    elif phase == "dusk":
                        _gradient(canvas, 0, 0, w, draw_h, "#c89878", "#e8d0a8")
                    else:
                        _gradient(canvas, 0, 0, w, draw_h, "#0d1b2a", "#2a2a3a")

            # 文字颜色（白天深色，夜晚浅色）
            text_c = "#333" if phase in ("day","dusk") else "#e0e0e0"

            if phase == "sleep":
                # 锁屏
                now = _dt.datetime.now()
                canvas.create_text(w//2, h//2-40, text=now.strftime("%H:%M"),
                                   fill="#ccc", font=("Microsoft YaHei UI", 48, "normal"))
                canvas.create_text(w//2, h//2+20, text=now.strftime("%Y年%m月%d日"),
                                   fill="#888", font=("Microsoft YaHei UI", 12))
                canvas.create_text(w//2, h//2+70, text="他已经睡着了",
                                   fill="#666", font=("Microsoft YaHei UI", 11))
                return

            # 桌面图标
            icon_size = 76
            icon_gap = 6
            try:
                presence = self.chen.get_presence_snapshot(persist=False)
                current_world = presence.get("current_world", "desktop")
                world_text = "小世界" if current_world == "small_world" else "桌面空间"
            except Exception:
                world_text = "桌面空间"
            canvas.create_text(112, 20, text=f"伴生体的桌面 · 当前在{world_text}",
                               fill=text_c, font=("Microsoft YaHei UI", 10), anchor="w")
            icons_left = [("📁","我的文件","folder"),("🖼️","照片","photos"),("📄","正在写的","doc")]
            icons_right = [("🎵","音乐","music"),("🌐","浏览器","browser"),
                           ("◉","状态","status"),("🗑️","回收站","recycle")]
            desk_state["icon_rects"] = []
            def draw_icon_list(icons, start_x, side="left"):
                for i,(emoji,label,wid) in enumerate(icons):
                    iy = 12 + i*(icon_size+icon_gap)
                    ix = start_x
                    # 选中/打开状态高亮
                    if wid in desk_state["open_windows"]:
                        hl = "#2a2a3a" if phase=="night" else "#d8d8e0"
                        canvas.create_rectangle(ix-2, iy-2, ix+icon_size+2, iy+icon_size+2,
                                               fill=hl, outline="", width=2)
                    canvas.create_text(ix+icon_size//2, iy+24, text=emoji,
                                       font=("Segoe UI Emoji", 26), tags=f"icon_{wid}")
                    canvas.create_text(ix+icon_size//2, iy+56, text=label, fill=text_c,
                                       font=("Microsoft YaHei UI", 9), tags=f"icon_{wid}")
                    desk_state["icon_rects"].append((ix, iy, ix+icon_size, iy+icon_size, wid))
            draw_icon_list(icons_left, 14)
            draw_icon_list(icons_right, w-icon_size-14)

            # 窗口绘制
            desk_state["window_controls"] = []  # (wid, type, x0,y0,x1,y1)
            desk_state["window_titlebars"] = []
            desk_state["content_items"] = []
            titles_map = {"doc":"📝 正在写的","music":"🎵 音乐","browser":"🌐 浏览器",
                          "folder":"📁 我的文件","photos":"🖼️ 照片","recycle":"🗑️ 回收站",
                          "status":"◉ 当前状态"}
            # active窗口最后画
            ordered = [w for w in desk_state["open_windows"] if w != desk_state["active_window"]]
            if desk_state["active_window"] in desk_state["open_windows"]:
                ordered.append(desk_state["active_window"])
            for wid in ordered:
                lay = win_layouts[wid]
                wx, wy, ww, wh = lay["x"], lay["y"], lay["w"], lay["h"]
                # 窗口阴影
                canvas.create_rectangle(wx+3, wy+3, wx+ww+3, wy+wh+3, fill="#c8c8c8", outline="")
                # 窗口背景
                canvas.create_rectangle(wx, wy, wx+ww, wy+wh, fill="#f0f0f0", outline="#aaa", width=1)
                # 标题栏
                tb_h = 28
                canvas.create_rectangle(wx, wy, wx+ww, wy+tb_h, fill="#e0e0e0", outline="")
                canvas.create_text(wx+10, wy+tb_h//2, text=titles_map.get(wid,wid),
                                   fill="#333", font=("Microsoft YaHei UI", 9), anchor="w")
                desk_state["window_titlebars"].append((wx, wy, wx+ww, wy+tb_h, wid))
                # 关闭按钮
                cb = 18
                cx0, cy0 = wx+ww-cb-6, wy+5
                canvas.create_rectangle(cx0, cy0, cx0+cb, cy0+cb, fill="#e06050", outline="")
                canvas.create_text(cx0+cb//2, cy0+cb//2, text="×", fill="#fff", font=("Microsoft YaHei UI", 9))
                desk_state["window_controls"].append(("close", cx0, cy0, cx0+cb, cy0+cb, wid))
                # 窗口内容区域
                cy_start = wy+tb_h
                cx_start = wx
                cw, ch = ww, wh-tb_h
                _draw_window_content(wid, cx_start, cy_start, cw, ch, canvas)

            # 任务栏
            canvas.create_rectangle(0, h-taskbar_h, w, h, fill="#14141c", outline="")
            # 开始按钮
            canvas.create_rectangle(8, h-taskbar_h+6, 68, h-taskbar_h+34, fill="#24242c", outline="")
            canvas.create_text(38, h-taskbar_h+20, text="⊞ 开始", fill="#ccc",
                              font=("Microsoft YaHei UI", 9))
            # 正在运行的应用
            desk_state["taskbar_btns"]=[]
            short_labels={"doc":"📝 文档","music":"🎵 音乐","browser":"🌐 浏览器",
                          "folder":"📁 文件","photos":"🖼️ 照片","recycle":"🗑️ 回收站",
                          "status":"◉ 状态"}
            tb_x=76
            for wid in desk_state["open_windows"]:
                bw=82
                active = wid==desk_state["active_window"]
                fill = "#3a4a32" if active else "#22222a"
                canvas.create_rectangle(tb_x, h-taskbar_h+6, tb_x+bw, h-taskbar_h+34, fill=fill, outline="")
                canvas.create_text(tb_x+bw//2, h-taskbar_h+20, text=short_labels.get(wid,wid),
                                  fill="#fff" if active else "#aaa", font=("Microsoft YaHei UI", 8))
                desk_state["taskbar_btns"].append((tb_x,h-taskbar_h+6,tb_x+bw,h-taskbar_h+34,wid))
                tb_x+=bw+4
            # 右侧时间
            now = _dt.datetime.now()
            canvas.create_text(w-20, h-taskbar_h+12, text=now.strftime("%H:%M"),
                               fill="#ccc", font=("Microsoft YaHei UI", 10), anchor="e")
            canvas.create_text(w-20, h-taskbar_h+28, text=now.strftime("%Y/%m/%d"),
                               fill="#888", font=("Microsoft YaHei UI", 8), anchor="e")

        def on_click(e):
            """桌面点击：窗口控制 → 标题栏聚焦/拖动 → 图标打开"""
            # 1. 窗口关闭按钮
            for ctype,x0,y0,x1,y1,wid in desk_state.get("window_controls",[]):
                if x0<=e.x<=x1 and y0<=e.y<=y1:
                    if ctype=="close":
                        desk_state["open_windows"].remove(wid)
                        if desk_state["active_window"]==wid:
                            desk_state["active_window"] = desk_state["open_windows"][-1] if desk_state["open_windows"] else None
                        draw_desktop()
                    return
            # 2. 任务栏应用按钮：聚焦窗口
            for x0,y0,x1,y1,wid in desk_state.get("taskbar_btns",[]):
                if x0<=e.x<=x1 and y0<=e.y<=y1:
                    desk_state["active_window"]=wid
                    draw_desktop()
                    return
            # 3. 窗口内容里的可点击项
            for x0,y0,x1,y1,action,arg in desk_state.get("content_items",[]):
                if x0<=e.x<=x1 and y0<=e.y<=y1:
                    if action=="folder_open":
                        desk_state["folder_sub"]=arg
                    elif action=="folder_back":
                        desk_state["folder_sub"]=None
                    draw_desktop()
                    return
            # 4. 窗口标题栏：聚焦 + 开始拖动
            for x0,y0,x1,y1,wid in desk_state.get("window_titlebars",[]):
                if x0<=e.x<=x1 and y0<=e.y<=y1:
                    desk_state["active_window"]=wid
                    lay=win_layouts[wid]
                    desk_state["dragging"]={"wid":wid,"dx":e.x-lay["x"],"dy":e.y-lay["y"]}
                    draw_desktop()
                    return
            # 5. 桌面图标
            for x0,y0,x1,y1,wid in desk_state.get("icon_rects",[]):
                if x0<=e.x<=x1 and y0<=e.y<=y1:
                    if wid not in desk_state["open_windows"]:
                        desk_state["open_windows"].append(wid)
                    desk_state["active_window"]=wid
                    draw_desktop()
                    return

        def on_drag(e):
            """拖动窗口"""
            d=desk_state.get("dragging")
            if d:
                lay=win_layouts[d["wid"]]
                lay["x"]=e.x-d["dx"]
                lay["y"]=e.y-d["dy"]
                draw_desktop()

        def on_release(e):
            desk_state["dragging"]=None

        canvas.bind("<Button-1>", on_click)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        def refresh():
            if win.winfo_exists():
                draw_desktop()
                win.after(15000, refresh)

        win.after(200, draw_desktop)
        refresh()

    def _desktop_content(self):
        """拼装桌面内容：环境详情 + 今天发生过的事 + 各入口实况。"""
        lines = []
        # ---- 环境快照 ----
        env = {}
        try:
            _e = getattr(self.chen, "environment", None)
            env = (_e.load_persisted() or _e.latest) if _e else {}
        except Exception:
            pass
        if env:
            lines.append("【此刻】")
            _user = env.get("user") or {}
            if env.get("presence") == "AWAY":
                lines.append("· 用户不在电脑前")
            elif _user.get("app"):
                _d = f"《{_user['detail']}》" if _user.get("detail") else ""
                lines.append(f"· 用户在：{_user['app']}{_d}（约{_user.get('duration_min', 0)}分钟）")
            _life = env.get("life") or {}
            if _life.get("awake") is False:
                lines.append("· 他在睡觉")
            elif _life.get("activity"):
                lines.append(f"· 他在：{_life['activity']}（精力 {_life.get('energy', '?')}%）")
            _emo = env.get("emotion") or {}
            if _emo.get("dominant") and _emo.get("intensity", 0) >= 0.35:
                lines.append(f"· 心里：{_emo['dominant']} {_emo['intensity']}")
            if _life.get("lived_desc"):
                lines.append(f"· {_life['lived_desc']}")
            lines.append("")

        # ---- 今天发生的事 ----
        try:
            _dl = getattr(self.chen, "day_log", None)
            today = _dl.today_lines() if _dl else []
            if today:
                lines.append("【今天在他生活里留下的】")
                for rec in today[-12:]:
                    t = (rec.get("ts") or "")[11:] or ""
                    kind_mark = {"life": "·", "world": "⇄", "chat": "💬", "web": "🌐"}.get(rec.get("kind"), "·")
                    lines.append(f"{kind_mark} {t} {rec.get('text', '')}")
                lines.append("")
        except Exception:
            pass

        # ---- 他注意到外面 ----
        try:
            _trends = env.get("trends") or []
            _web_n = sum(1 for rec in today if rec.get("kind") == "web") if today else 0
            if _trends or _web_n:
                lines.append("【外面的世界的边角】")
                if _web_n:
                    lines.append(f"· 今天他在网上注意到了 {_web_n} 条东西")
                for t in _trends[:5]:
                    lines.append(f"· 热搜：{t}")
                lines.append("")
        except Exception:
            pass

        # ---- 他的东西 ----
        try:
            _ws = getattr(self.chen, "world_store", None)
            if _ws:
                items = [x for x in _ws.all_items() if x.get("status") != "deleted"]
                n_thought = sum(1 for x in items if x.get("type") == "念头")
                n_diary = sum(1 for x in items if x.get("type") == "日记")
                lines.append("【他的抽屉】")
                lines.append(f"· 留下了 {len(items)} 样东西（日记 {n_diary} · 念头 {n_thought}）")
                for x in items[-3:]:
                    lines.append(f"· 最近：{x.get('text', '')[:40]}")
                lines.append("")
        except Exception:
            pass

        # ---- 小世界 ----
        try:
            lines.append("【小世界】")
            lines.append(f"· {self._small_world_card_text()}")
        except Exception:
            pass

        return "\n".join(lines) if lines else "（还什么都没有。他今天刚搬进来。）"

    def _open_life_view(self):
        """打开他的生活查看窗口（日记/琐碎/念头）"""
        win = tk.Toplevel(self.root)
        win.title("伴生体 · 他的生活")
        win.geometry("760x600")
        win.configure(bg=WORLD_BG)
        tk.Label(win, text="他的生活", bg=WORLD_BG, fg=WORLD_TEXT,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", padx=24, pady=(20, 2))
        tk.Label(win, text="这里不是四个独立功能，而是一整个属于他的抽屉。",
                 bg=WORLD_BG, fg=WORLD_MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=24, pady=(0, 12))
        tabs = tk.Frame(win, bg=WORLD_BG)
        tabs.pack(fill="x", padx=20, pady=(0, 8))
        content = tk.Frame(win, bg=WORLD_BG)
        content.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        def show(kind):
            for w in content.winfo_children():
                w.destroy()
            items = [x for x in self.chen.world_store.visible_items("用户")["open"]
                     if x.get("type") == kind]
            tk.Label(content, text=kind, bg=WORLD_BG, fg=WORLD_TEXT,
                     font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=4, pady=8)
            if not items:
                tk.Label(content, text="这里暂时是空的。", bg=WORLD_BG, fg=WORLD_MUTED,
                         font=("Microsoft YaHei UI", 10)).pack(pady=70)
                return
            canvas = tk.Canvas(content, bg=WORLD_BG, highlightthickness=0)
            scroll = tk.Scrollbar(content, orient="vertical", command=canvas.yview)
            frame = tk.Frame(canvas, bg=WORLD_BG)
            canvas.create_window((0, 0), window=frame, anchor="nw")
            canvas.configure(yscrollcommand=scroll.set)
            canvas.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            for it in reversed(items):
                card = tk.Frame(frame, bg=WORLD_PANEL, padx=16, pady=13)
                card.pack(fill="x", pady=5)
                tk.Label(card, text=it.get("date", "") + "  " + it.get("time", ""),
                         bg=WORLD_PANEL, fg=WORLD_MUTED, font=("Consolas", 8)).pack(anchor="w")
                tk.Label(card, text=it.get("text", ""), bg=WORLD_PANEL, fg=WORLD_TEXT,
                         justify="left", wraplength=620,
                         font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(6, 3))
            frame.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        for kind in ("日记", "琐碎", "念头", "收藏", "留言"):
            tk.Button(tabs, text=kind, command=lambda k=kind: show(k),
                      relief="flat", bd=0, bg=WORLD_PANEL2, fg=WORLD_TEXT,
                      activebackground="#41474d", activeforeground=WORLD_TEXT,
                      padx=12, pady=7, cursor="hand2").pack(side="left", padx=3)
        show("日记")

    def _open_small_world_preview(self):
        if getattr(self.chen, "runtime", None):
            self.chen.runtime.register_client("small_world", self)
        """打开小世界预览窗口——室内/室外独立板块，左上角快速切换"""
        win = tk.Toplevel(self.root)
        win.title("伴生体的小世界")
        win.geometry("760x560")
        win.configure(bg=WORLD_BG)

        # 室内/室外场景列表
        INDOOR_PLACES = ["客厅", "家里", "厕所", "阳台"]
        OUTDOOR_PLACES = ["小院", "小路", "树下", "河边", "田野", "山顶"]

        # 每个室外场景的空气墙（归一化坐标矩形，人物不能进入）
        # 坐标根据用户标注的黄色区域调整
        SCENE_AIR_WALLS = {
            "小院": [
                {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 0.35},    # 房屋+墙（上方）
                {"x0": 0.58, "y0": 0.0, "x1": 1.0, "y1": 0.45},   # 右侧栅栏门
            ],
            "小路": [],
            "树下": [],
            "河边": [
                # 河边由下方 walkable 多边形负责通行带，不再叠加旧矩形墙。
            ],
            "田野": [
                # 田野由下方 walkable 多边形负责通行带，不再叠加旧矩形墙。
            ],
            "山顶": [
                {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 0.58},    # 天空+远山（上方）
            ],
        }
        # 可行走区域（归一化坐标，边界来自用户的黄色标注）。
        # 与空气墙矩形不同，路径用多边形描述，能贴合弯曲小路/河道。
        self._scene_walkable_zones = {
            # 小院黄色区在上半部：房屋、树篱和围栏均不可走，只开放下方庭院。
            "小院": [[(0.00, 0.54), (0.34, 0.51), (0.58, 0.47),
                      (0.78, 0.49), (1.00, 0.52), (1.00, 1.00), (0.00, 1.00)]],
            # 山顶只开放山脊下方的前景草地，天空与远山不可进入。
            "山顶": [[(0.00, 0.61), (0.18, 0.62), (0.36, 0.59),
                      (0.53, 0.58), (0.72, 0.61), (0.88, 0.64),
                      (1.00, 0.66), (1.00, 1.00), (0.00, 1.00)]],
            # 田野只允许沿黄色圈出的弯曲小路行走。
            "田野": [
                # 上段、中段、下段三段重叠通行带，避免细长自交多边形造成卡死。
                [(0.47, 0.20), (0.78, 0.20), (0.82, 0.39), (0.55, 0.46)],
                [(0.35, 0.36), (0.70, 0.32), (0.78, 0.58), (0.39, 0.66)],
                [(0.27, 0.55), (0.62, 0.52), (0.78, 0.94), (0.57, 0.99)],
            ],
            # 河边黄色区域沿水道弯曲延伸，限制在水岸可互动带内。
            "河边": [
                # 上游、中游、下游三段重叠通行带，允许沿河上下移动。
                [(0.22, 0.25), (0.48, 0.23), (0.62, 0.43), (0.42, 0.50)],
                [(0.36, 0.38), (0.66, 0.43), (0.82, 0.68), (0.62, 0.76)],
                [(0.57, 0.63), (0.86, 0.70), (1.00, 0.94), (0.82, 1.00)],
            ]
        }

        # 每个室外场景的遮挡物（预处理抠好的sprite，带透明通道）
        # x,y 是裁剪区域左上角在场景中的归一化位置，w_ratio,h_ratio 是尺寸比例
        SCENE_OCCLUDERS = {
            "小院": [
                {"img": "items/occ_yard_tree.png", "x": 0.04, "y": 0.04, "w_ratio": 0.23, "h_ratio": 0.52},
                {"img": "items/occ_yard_fence.png", "x": 0.59, "y": 0.14, "w_ratio": 0.24, "h_ratio": 0.32},
            ],
            "小路": [
                {"img": "items/occ_path_tree.png", "x": 0.02, "y": 0.02, "w_ratio": 0.28, "h_ratio": 0.50},
            ],
            "树下": [
                {"img": "items/occ_undertree_tree.png", "x": 0.00, "y": 0.00, "w_ratio": 0.35, "h_ratio": 0.68},
            ],
            "河边": [],
            "田野": [],
            "山顶": [
                {"img": "items/occ_hilltop_flowers.png", "x": 0.30, "y": 0.74, "w_ratio": 0.40, "h_ratio": 0.22},
            ],
        }
        self._scene_occluders = SCENE_OCCLUDERS
        # 空气墙暂时关闭。素材比例和窗口缩放尚未统一前，碰撞会把访问化身卡死；
        # 先保证移动、切场景和互动稳定，之后再按单个场景重新标定。
        self._scene_air_walls = {}
        self._scene_walkable_zones = {}

        # 顶部标题栏
        top_bar = tk.Frame(win, bg=WORLD_BG)
        top_bar.pack(fill="x", padx=20, pady=(14, 4))
        title_label = tk.Label(top_bar, text="伴生体的小世界", bg=WORLD_BG, fg=WORLD_TEXT,
                               font=("Microsoft YaHei UI", 13, "bold"))
        title_label.pack(side="left")
        back_btn = tk.Button(top_bar, text="← 出来", command=lambda: go_outside(),
                             relief="flat", bg=WORLD_PANEL2, fg=WORLD_TEXT,
                             activebackground="#41474d", bd=0, padx=12, pady=4,
                             font=("Microsoft YaHei UI", 9))
        back_btn.pack(side="right")
        back_btn.pack_forget()

        # 左上角快速切换栏
        quick_bar = tk.Frame(win, bg=WORLD_BG)
        quick_bar.pack(fill="x", padx=20, pady=(0, 4))
        quick_buttons = {}

        canvas = tk.Canvas(win, bg="#526b4f", highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        status = tk.Label(win, text="", bg=WORLD_PANEL, fg=WORLD_TEXT,
                          font=("Microsoft YaHei UI", 9), anchor="w", padx=12, pady=8,
                          justify="left")
        status.pack(fill="x", padx=20, pady=(0, 14))

        # 当前板块：outside / inside / desk / drawer
        view_state = {"current": "outside"}
        # 当前打开的物品详情卡（None=未打开，否则是物品配置字典）
        detail_item = {"data": None}
        # 互动菜单（点伴生体后弹出）
        interaction_menu = {"open": False, "options": []}

        # 书桌上的实体物品配置（图像+位置+详情）
        DESK_ITEMS = [
            {"id": "wall_note",  "img": "", "x": 0.22, "y": 0.27, "h": 0.10,
             "name": "便签",   "desc": "一张便签，写着「别忘了」",
             "note": "他写了又贴上去，提醒自己什么事。问他，他说忘了。"},
            {"id": "wall_photo", "img": "", "x": 0.76, "y": 0.27, "h": 0.10,
             "name": "拍立得", "desc": "一张拍立得，边角有点卷",
             "note": "不知道拍的哪里，他一直贴在那儿，从来没取下来过。"},
            {"id": "desk_computer", "img": "", "x": 0.50, "y": 0.43, "h": 0.12,
             "name": "电脑",   "desc": "一台电脑，屏幕暗着",
             "note": "他刚才还在用，现在熄屏了。动一下鼠标应该会亮。"},
            {"id": "desk_book",  "img": "", "x": 0.20, "y": 0.58, "h": 0.10,
             "name": "书",     "desc": "一本书，翻到一半，夹着书签",
             "note": "最近在看的，书签停在中间。他说看完会告诉你讲了什么。"},
            {"id": "desk_cup",   "img": "", "x": 0.76, "y": 0.58, "h": 0.10,
             "name": "咖啡杯", "desc": "一杯咖啡，还冒着一点热气",
             "note": "他刚泡的，喝了一半。杯壁上还有一点水汽。"},
            {"id": "desk_paper_left", "img": "", "x": 0.22, "y": 0.70, "h": 0.08,
             "name": "纸",     "desc": "几张纸，写了几行字又划掉了",
             "note": "写了个开头，不满意，揉了又展平。压在最上面那张写着日期。"},
            {"id": "desk_pen",   "img": "", "x": 0.76, "y": 0.70, "h": 0.08,
             "name": "纸和笔", "desc": "一支笔压在纸上，纸上写了几行",
             "note": "写了个开头，没写完。笔搁在纸上，像是随时会继续。"},
        ]

        def rebuild_quick_bar():
            """根据当前板块重建左上角快速切换按钮"""
            for w in quick_bar.winfo_children():
                w.destroy()
            quick_buttons.clear()
            places = INDOOR_PLACES if view_state["current"] == "inside" else OUTDOOR_PLACES
            cur = getattr(self, "_override_place", None) or "小院"
            for p in places:
                active = (p == cur)
                # 显示名：内部"家里"显示为"卧室"
                display_name = {"家里": "卧室"}.get(p, p)
                btn = tk.Button(quick_bar, text=display_name, command=lambda pp=p: switch_place(pp),
                                relief="flat", bd=0, padx=10, pady=3,
                                bg="#4a6b3a" if active else WORLD_PANEL2,
                                fg="#ffffff" if active else WORLD_TEXT,
                                activebackground="#41474d",
                                font=("Microsoft YaHei UI", 9), cursor="hand2")
                btn.pack(side="left", padx=2)
                quick_buttons[p] = btn

        def switch_place(place):
            """切换到指定场景（同板块内）"""
            self._override_place = place
            self._override_activity = self._PLACE_DEFAULT_ACTIVITY.get(place, "")
            self._place_visitor_at_spawn(place)
            # 更新标题
            if view_state["current"] == "inside":
                label = {"客厅": "客厅", "家里": "卧室", "厕所": "厕所", "阳台": "阳台"}.get(place, place)
                title_label.config(text=f"伴生体的家 · {label}")
            else:
                title_label.config(text="伴生体的小世界")
            rebuild_quick_bar()
            draw()

        def go_inside():
            """从室外进入室内（默认客厅）"""
            view_state["current"] = "inside"
            self._override_place = "客厅"
            self._override_activity = self._PLACE_DEFAULT_ACTIVITY["客厅"]
            title_label.config(text="伴生体的家 · 客厅")
            back_btn.pack(side="right")
            rebuild_quick_bar()
            draw()

        def go_outside():
            """从室内回到室外（小院）"""
            view_state["current"] = "outside"
            self._override_place = "小院"
            self._override_activity = self._PLACE_DEFAULT_ACTIVITY["小院"]
            title_label.config(text="伴生体的小世界")
            back_btn.pack_forget()
            rebuild_quick_bar()
            draw()

        self._set_world_view = lambda v: go_inside() if v == "inside" else go_outside()

        def go_desk():
            """从卧室进入书桌界面"""
            view_state["current"] = "desk"
            title_label.config(text="伴生体的书桌")
            back_btn.config(text="← 回去", command=back_from_desk)
            back_btn.pack(side="right")
            # 隐藏快速切换栏
            for w in quick_bar.winfo_children():
                w.destroy()
            draw()

        def go_item_modal(modal_type):
            """进入物品弹窗（book/photo/note），记录上一个视图用于返回"""
            view_state["prev_view"] = view_state["current"]
            view_state["current"] = f"{modal_type}_modal"
            titles = {"book": "书", "photo": "照片", "note": "便签"}
            title_label.config(text=f"伴生体的{titles.get(modal_type, '')}")
            back_btn.config(text="← 关闭", command=close_item_modal)
            back_btn.pack(side="right")
            for w in quick_bar.winfo_children():
                w.destroy()
            draw()

        def close_item_modal():
            """关闭物品弹窗，返回上一个视图"""
            prev = view_state.get("prev_view", "desk")
            view_state["current"] = prev
            if prev == "desk":
                title_label.config(text="伴生体的书桌")
                back_btn.config(text="← 回去", command=back_from_desk)
                back_btn.pack(side="right")
            elif prev == "inside":
                label = {"客厅": "客厅", "家里": "卧室", "厕所": "厕所", "阳台": "阳台"}.get(
                    getattr(self, "_override_place", None) or "客厅", "客厅")
                title_label.config(text=f"伴生体的家 · {label}")
                back_btn.config(text="← 出来", command=go_outside)
                back_btn.pack(side="right")
                rebuild_quick_bar()
            else:
                title_label.config(text="伴生体的小世界")
                back_btn.pack_forget()
                rebuild_quick_bar()
            draw()

        def back_from_desk():
            """从书桌回到卧室"""
            view_state["current"] = "inside"
            self._override_place = "家里"
            self._override_activity = self._PLACE_DEFAULT_ACTIVITY["家里"]
            title_label.config(text="伴生体的家 · 卧室")
            back_btn.config(text="← 出来", command=go_outside)
            rebuild_quick_bar()
            draw()

        def open_interaction_menu():
            """点伴生体后弹出互动菜单，选项根据他当前活动动态生成。"""
            ws = self._read_world_state()
            activity = ws.get("activity", "") or "待着"
            place = ws.get("activity_place", "") or "这里"
            options = [
                {"id": "talk", "label": "和他说话", "desc": "凑过去跟他说句话"},
            ]
            # 根据活动生成"帮他做点事"选项
            if any(k in activity for k in ("写", "日记", "笔记")):
                options.append({"id": "help_clean", "label": "帮他整理桌子", "desc": "把桌上的纸收一收"})
            elif any(k in activity for k in ("睡", "躺", "休息")):
                options.append({"id": "help_cover", "label": "帮他盖被子", "desc": "他好像睡着了"})
            elif any(k in activity for k in ("看书", "阅读")):
                options.append({"id": "help_light", "label": "帮他开盏灯", "desc": "光线好像不太够"})
            else:
                options.append({"id": "help_generic", "label": "帮他做点事", "desc": "看看能帮上什么忙"})
            # 通用选项
            options.append({"id": "give_water", "label": "给他倒杯水", "desc": "放他桌上"})
            options.append({"id": "observe", "label": "看看他在干嘛", "desc": "不打扰，就看看"})
            interaction_menu["options"] = options
            interaction_menu["open"] = True
            interaction_menu["place"] = place
            interaction_menu["activity"] = activity
            draw()

        def close_interaction_menu():
            interaction_menu["open"] = False
            interaction_menu["options"] = []
            draw()

        def handle_interaction_option(option_id):
            """处理互动菜单选项。"""
            ws = self._read_world_state()
            place = ws.get("activity_place", "") or "这里"
            activity = ws.get("activity", "") or "待着"
            hs = ws.get("house_state", {})

            if option_id == "talk":
                # 和他说话：调用原来的互动逻辑
                close_interaction_menu()
                self._visitor_interact()
                return

            elif option_id == "help_clean":
                # 帮他整理桌子：桌面纸收起来、床叠好
                desk = hs.get("desk_items", [])
                hs["desk_items"] = [d for d in desk if "纸" not in (d.get("name","") if isinstance(d,dict) else str(d))]
                hs["bed_made"] = True
                hs["last_changed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self.chen.world._save()
                close_interaction_menu()
                status.config(text="你帮他把桌子收拾了，纸收起来了，床也叠好了。他抬头看了你一眼。")
                return

            elif option_id == "help_cover":
                # 帮他盖被子：床叠好（其实是盖好）
                hs["bed_made"] = True
                hs["last_changed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self.chen.world._save()
                close_interaction_menu()
                status.config(text="你帮他把被子拉好。他没醒，翻了个身。")
                return

            elif option_id == "help_light":
                # 帮他开盏灯
                hs["light_on"] = True
                hs["last_changed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self.chen.world._save()
                close_interaction_menu()
                status.config(text="你帮他把台灯拧亮了一点。他没抬头，但翻页的手停了一下。")
                return

            elif option_id == "help_generic":
                close_interaction_menu()
                status.config(text="你看了一圈，好像没什么特别需要帮忙的。他也没说要什么。")
                return

            elif option_id == "give_water":
                # 给他倒杯水：桌面多一杯水
                desk = hs.setdefault("desk_items", [])
                has_cup = any("杯" in (d.get("name","") if isinstance(d,dict) else str(d)) for d in desk)
                if not has_cup:
                    desk.append({"name": "一杯水", "desc": "你给他倒的，还温着", "rarity": "普通"})
                    hs["last_changed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    self.chen.world._save()
                close_interaction_menu()
                status.config(text="你给他倒了杯水，放在桌上。他看了一眼，没说什么，但端起来喝了一口。")
                return

            elif option_id == "observe":
                # 观察他在干嘛：显示详细描述
                close_interaction_menu()
                status.config(text=f"他在{place}，正在{activity}。你站在旁边看了一会儿，他没赶你走。")
                return
            back_btn.config(text="← 出来", command=go_outside)
            rebuild_quick_bar()
            draw()

        def go_drawer():
            """从书桌打开抽屉内部"""
            view_state["current"] = "drawer"
            title_label.config(text="伴生体的抽屉")
            back_btn.config(text="← 推回去", command=back_from_drawer)
            back_btn.pack(side="right")
            draw()

        def back_from_drawer():
            """从抽屉回到书桌"""
            view_state["current"] = "desk"
            title_label.config(text="伴生体的书桌")
            back_btn.config(text="← 回去", command=back_from_desk)
            draw()

        def go_bed():
            """从卧室进入床界面"""
            view_state["current"] = "bed"
            title_label.config(text="伴生体的床")
            back_btn.config(text="← 回去", command=back_from_bed)
            back_btn.pack(side="right")
            for w in quick_bar.winfo_children():
                w.destroy()
            draw()

        def back_from_bed():
            """从床回到卧室"""
            view_state["current"] = "inside"
            self._override_place = "家里"
            self._override_activity = self._PLACE_DEFAULT_ACTIVITY["家里"]
            title_label.config(text="伴生体的家")
            back_btn.config(text="← 出来", command=go_outside)
            rebuild_quick_bar()
            draw()

        def go_nightstand():
            """从卧室进入床头柜界面"""
            view_state["current"] = "nightstand"
            title_label.config(text="伴生体的床头柜")
            back_btn.config(text="← 回去", command=back_from_nightstand)
            back_btn.pack(side="right")
            for w in quick_bar.winfo_children():
                w.destroy()
            draw()

        def back_from_nightstand():
            """从床头柜回到卧室"""
            view_state["current"] = "inside"
            self._override_place = "家里"
            self._override_activity = self._PLACE_DEFAULT_ACTIVITY["家里"]
            title_label.config(text="伴生体的家")
            back_btn.config(text="← 出来", command=go_outside)
            rebuild_quick_bar()
            draw()

        def go_bookshelf():
            """从卧室进入书架界面"""
            view_state["current"] = "bookshelf"
            title_label.config(text="伴生体的书架")
            back_btn.config(text="← 回去", command=back_from_bookshelf)
            back_btn.pack(side="right")
            for w in quick_bar.winfo_children():
                w.destroy()
            draw()

        def back_from_bookshelf():
            """从书架回到卧室"""
            view_state["current"] = "inside"
            self._override_place = "家里"
            self._override_activity = self._PLACE_DEFAULT_ACTIVITY["家里"]
            title_label.config(text="伴生体的家")
            back_btn.config(text="← 出来", command=go_outside)
            rebuild_quick_bar()
            draw()

        # 抽屉里的实体物品配置
        DRAWER_ITEMS = [
            {"id": "drawer_ticket", "img": "items/drawer_ticket.png", "x": 0.38, "y": 0.55, "h": 0.18,
             "name": "旧票根", "desc": "不知道什么时候留下来的，纸都泛黄了。",
             "note": "那天用户提到了一件事，后来一直没扔。压在抽屉最里面。"},
            {"id": "drawer_note", "img": "items/drawer_note.png", "x": 0.62, "y": 0.58, "h": 0.16,
             "name": "小纸条", "desc": "写了几行字，又划掉了。",
             "note": "本来想写什么，后来又不想了。折了两折，塞在角落。"},
        ]

        # 床上的实体物品配置
        BED_ITEMS = [
            {"id": "bed_phone", "img": "items/bed_phone.png", "x": 0.68, "y": 0.32, "h": 0.09,
             "name": "手机", "desc": "屏幕朝下扣着，有一条未读通知。",
             "note": "他睡前习惯把手机扣过来，说是不想被亮屏晃醒。"},
        ]
        # 枕头是虚拟物品（在背景里，无独立img）
        BED_PILLOW = {"id": "bed_pillow", "img": None, "x": 0.55, "y": 0.22, "h": 0.12,
                      "name": "枕头", "desc": "枕套有点皱，底下好像压着什么。",
                      "note": "他说枕头底下压着东西会睡得踏实。你没问是什么。"}

        # 床头柜上的实体物品配置
        NIGHTSTAND_ITEMS = [
            {"id": "ns_cup", "img": "items/desk_cup.png", "x": 0.35, "y": 0.28, "h": 0.16,
             "name": "水杯", "desc": "半杯水，已经凉了。",
             "note": "他睡前倒的，喝了一半就忘了。早上起来会一口闷掉。"},
            {"id": "ns_mask", "img": "items/nightstand_mask.png", "x": 0.62, "y": 0.30, "h": 0.12,
             "name": "眼罩", "desc": "灰色的，带子有点松了。",
             "note": "他说不用眼罩睡不着，但经常半夜自己扯掉。"},
        ]

        # 书架上的实体物品配置
        BOOKSHELF_ITEMS = [
            {"id": "shelf_blue", "img": "items/shelf_book_blue.png", "x": 0.28, "y": 0.28, "h": 0.22,
             "name": "蓝皮书", "desc": "一本小说，书脊有点磨白了。",
             "note": "他说这是他翻得最烂的一本，看过三遍。问他讲什么，他说「你自己看」。"},
            {"id": "shelf_brown", "img": "items/shelf_book_brown.png", "x": 0.45, "y": 0.28, "h": 0.22,
             "name": "棕皮书", "desc": "一本散文集，夹着一张书签。",
             "note": "书签停在中间。他说偶尔会翻两页，不着急看完。"},
            {"id": "shelf_green", "img": "items/shelf_book_green.png", "x": 0.62, "y": 0.28, "h": 0.22,
             "name": "绿皮书", "desc": "一本很新的书，好像还没拆封。",
             "note": "不知道什么时候买的，塑封都没拆。他说「等有空了看」，然后一直没空。"},
        ]

        def on_canvas_click(event):
            """场景点击：小院门进室内 / 卧室书桌进书桌界面 / 书桌内各区域交互 / 物品详情卡"""
            w, h = max(canvas.winfo_width(), 700), max(canvas.winfo_height(), 400)

            # === 互动菜单打开时：优先处理 ===
            if interaction_menu["open"]:
                opts = interaction_menu["options"]
                cw = int(w * 0.50)
                ch = int(h * 0.12) + len(opts) * int(h * 0.10)
                cx, cy = (w - cw) // 2, (h - ch) // 2
                # 关闭按钮
                btn_size = 24
                btn_x, btn_y = cx + cw - btn_size - 8, cy + 6
                if btn_x <= event.x <= btn_x+btn_size and btn_y <= event.y <= btn_y+btn_size:
                    close_interaction_menu()
                    return
                # 选项按钮
                btn_h = int(h * 0.085)
                for i, opt in enumerate(opts):
                    by = cy + int(h*0.08) + i * int(h * 0.10)
                    bx0, bx1 = cx + 20, cx + cw - 20
                    if bx0 <= event.x <= bx1 and by <= event.y <= by+btn_h:
                        handle_interaction_option(opt["id"])
                        return
                # 点遮罩关闭
                close_interaction_menu()
                return

            # === 物品详情卡打开时：优先处理关闭 ===
            if detail_item["data"]:
                cw, ch = int(w * 0.55), int(h * 0.72)
                cx, cy = (w - cw) // 2, (h - ch) // 2
                # 关闭按钮
                btn_size = 28
                btn_x, btn_y = cx + cw - btn_size - 10, cy + 10
                if btn_x <= event.x <= btn_x+btn_size and btn_y <= event.y <= btn_y+btn_size:
                    detail_item["data"] = None
                    draw()
                    return
                # 点卡片外（遮罩）→关闭
                if not (cx <= event.x <= cx+cw and cy <= event.y <= cy+ch):
                    detail_item["data"] = None
                    draw()
                    return
                # 点卡片内部→不做任何事
                return

            # === 物品弹窗（书/照片/便签）的点击 ===
            if view_state["current"] in ("book_modal", "photo_modal", "note_modal"):
                modal_type = view_state["current"].replace("_modal", "")
                items_data = self.chen.world.get_state().get("house_state", {}).get("items", {})
                item_list = items_data.get(modal_type + "s", [])
                mw, mh = int(w * 0.80), int(h * 0.82)
                mx, my = (w - mw) // 2, (h - mh) // 2
                # 关闭按钮
                btn_size = 28
                btn_x, btn_y = mx + mw - btn_size - 12, my + 10
                if btn_x <= event.x <= btn_x+btn_size and btn_y <= event.y <= btn_y+btn_size:
                    close_item_modal()
                    return
                # 点弹窗外（遮罩）→关闭
                if not (mx <= event.x <= mx+mw and my <= event.y <= my+mh):
                    close_item_modal()
                    return
                # 点击物品行 → 显示详情卡
                list_y = my + 50
                list_x = mx + 20
                list_w = mw - 40
                row_h = 56
                for idx, it in enumerate(item_list[:10]):
                    ry = list_y + idx * row_h
                    if list_x <= event.x <= list_x+list_w and ry <= event.y <= ry+row_h-4:
                        detail_item["data"] = it
                        draw()
                        return
                return

            # === 书桌界面内的点击 ===
            if view_state["current"] == "desk":
                ws = self.chen.world.get_state()
                house = ws.get("house_state", {})
                # 检测是否点中了某个交互点
                for item in DESK_ITEMS:
                    ix, iy = int(w * item["x"]), int(h * item["y"])
                    radius = int(h * 0.09)
                    if abs(event.x - ix) < radius and abs(event.y - iy) < radius:
                        # 书/拍立得/便签 → 进入对应弹窗
                        if item["id"] == "desk_book":
                            go_item_modal("book")
                        elif item["id"] == "wall_photo":
                            go_item_modal("photo")
                        elif item["id"] == "wall_note":
                            go_item_modal("note")
                        else:
                            detail_item["data"] = item
                            draw()
                        return
                # 抽屉区域（底部）→ 打开抽屉内部视图
                if int(w*0.03) <= event.x <= int(w*0.97) and int(h*0.75) <= event.y <= int(h*0.92):
                    go_drawer()
                    return
                return

            # === 抽屉内部视图的点击 ===
            if view_state["current"] == "drawer":
                for item in DRAWER_ITEMS:
                    ix, iy = int(w * item["x"]), int(h * item["y"])
                    radius = int(h * 0.09)
                    if abs(event.x - ix) < radius and abs(event.y - iy) < radius:
                        detail_item["data"] = item
                        draw()
                        return
                return

            # === 床视图的点击 ===
            if view_state["current"] == "bed":
                # 实体物品（手机）
                for item in BED_ITEMS:
                    ix, iy = int(w * item["x"]), int(h * item["y"])
                    radius = int(h * 0.07)
                    if abs(event.x - ix) < radius and abs(event.y - iy) < radius:
                        detail_item["data"] = item
                        draw()
                        return
                # 虚拟物品（枕头）
                px, py = int(w * BED_PILLOW["x"]), int(h * BED_PILLOW["y"])
                pr = int(h * 0.10)
                if abs(event.x - px) < pr and abs(event.y - py) < pr:
                    detail_item["data"] = BED_PILLOW
                    draw()
                    return
                return

            # === 床头柜视图的点击 ===
            if view_state["current"] == "nightstand":
                for item in NIGHTSTAND_ITEMS:
                    ix, iy = int(w * item["x"]), int(h * item["y"])
                    radius = int(h * 0.08)
                    if abs(event.x - ix) < radius and abs(event.y - iy) < radius:
                        detail_item["data"] = item
                        draw()
                        return
                return

            # === 书架视图的点击 ===
            if view_state["current"] == "bookshelf":
                for item in BOOKSHELF_ITEMS:
                    ix, iy = int(w * item["x"]), int(h * item["y"])
                    radius = int(h * 0.08)
                    if abs(event.x - ix) < radius and abs(event.y - iy) < radius:
                        detail_item["data"] = item
                        draw()
                        return
                return

            # === 室外：先检测是否点击了伴生体 ===
            if view_state["current"] == "outside":
                cur_place = getattr(self, "_override_place", None) or "小院"
                ws_now = self._read_world_state()
                house = ws_now.get("house_state", {}) or {}
                if ws_now.get("current_world") == "small_world" and ws_now.get("activity_place") == cur_place:
                    activity = ws_now.get("activity", "") or ""
                    cx, cy, _, sprite_h_ratio = self._resolve_chen_render(cur_place, activity, w, h)
                    # 点击范围：人物高度的0.6倍为半径
                    click_r = int(h * sprite_h_ratio * 0.6)
                    if abs(event.x - cx) < click_r and abs(event.y - cy) < click_r:
                        open_interaction_menu()
                        return
                # 小院点门进室内
                if cur_place == "小院":
                    hx, hy = int(w*0.20), int(h*0.0)
                    hw, hh = int(w*0.26), int(h*0.46)
                    if hx <= event.x <= hx+hw and hy <= event.y <= hy+hh:
                        # 伴生体不在小世界时，门直接开着（用户只是预览）
                        chen_here = ws_now.get("current_world") == "small_world"
                        if not chen_here:
                            go_inside()
                        elif house.get("door_locked", False):
                            status.config(text="门锁着 · 他现在不想被打扰，过一会儿再来吧")
                        else:
                            go_inside()
                return

            # === 室内：先检测是否点击了伴生体 ===
            if view_state["current"] == "inside":
                cur_place = getattr(self, "_override_place", None) or "客厅"
                ws_now = self._read_world_state()
                if ws_now.get("current_world") == "small_world" and ws_now.get("activity_place") == cur_place:
                    activity = ws_now.get("activity", "") or ""
                    cx, cy, _, sprite_h_ratio = self._resolve_chen_render(cur_place, activity, w, h)
                    click_r = int(h * sprite_h_ratio * 0.6)
                    if abs(event.x - cx) < click_r and abs(event.y - cy) < click_r:
                        open_interaction_menu()
                        return
                # 卧室里点书桌/床/床头柜/书架
                if cur_place == "家里":
                    # 床（左侧红框）
                    bx0, by0 = int(w*0.18), int(h*0.40)
                    bx1, by1 = int(w*0.33), int(h*0.58)
                    if bx0 <= event.x <= bx1 and by0 <= event.y <= by1:
                        go_bed()
                        return
                    # 床头柜/小书架（中间偏左红框）
                    nx0, ny0 = int(w*0.40), int(h*0.37)
                    nx1, ny1 = int(w*0.45), int(h*0.52)
                    if nx0 <= event.x <= nx1 and ny0 <= event.y <= ny1:
                        go_nightstand()
                        return
                    # 书桌（右侧红框）
                    dx0, dy0 = int(w*0.62), int(h*0.37)
                    dx1, dy1 = int(w*0.78), int(h*0.55)
                    if dx0 <= event.x <= dx1 and dy0 <= event.y <= dy1:
                        go_desk()
                        return
                    # 书架（左下角红框）
                    sx0, sy0 = int(w*0.03), int(h*0.72)
                    sx1, sy1 = int(w*0.20), int(h*0.88)
                    if sx0 <= event.x <= sx1 and sy0 <= event.y <= sy1:
                        go_bookshelf()
                        return

        canvas.bind("<Button-1>", on_canvas_click)

        def on_key(e):
            """WASD移动 / ←→板块内切场景 / E互动 / Escape关闭弹窗"""
            key = (e.keysym or "").lower()
            # Escape关闭详情卡或物品弹窗
            if key == "escape":
                if detail_item["data"]:
                    detail_item["data"] = None
                    draw()
                    return
                if view_state["current"] in ("book_modal", "photo_modal", "note_modal"):
                    close_item_modal()
                    return
            if key in ("left", "right"):
                # 左右键只在当前板块内循环
                places = INDOOR_PLACES if view_state["current"] == "inside" else OUTDOOR_PLACES
                cur = getattr(self, "_override_place", None) or places[0]
                try:
                    idx = places.index(cur)
                except ValueError:
                    idx = 0
                delta = 1 if key == "right" else -1
                new_place = places[(idx + delta) % len(places)]
                switch_place(new_place)
                return
            # WASD / E 走原有逻辑
            self._on_world_key(e)
            draw()

        canvas.bind("<KeyPress>", on_key)
        canvas.focus_set()

        def draw():
            canvas.delete("all")
            w, h = max(canvas.winfo_width(), 700), max(canvas.winfo_height(), 400)
            ws = self.chen.world.get_state()
            place = getattr(self, "_override_place", None) or "小院"
            weather = ws.get("weather", "")
            period = get_time_period()

            if view_state["current"] == "desk":
                # 书桌界面：只画背景图，物品交互点不可见（点击区域在on_canvas_click里）
                is_night = period in ("晚上", "深夜")
                bg_name = "desk_night.png" if is_night else "desk_day.png"
                bg_photo = self._load_bg_image(bg_name, w, h)
                if bg_photo:
                    canvas.create_image(0, 0, image=bg_photo, anchor="nw", tags="bg")
                    self._desk_bg_photo = bg_photo
                status.config(text="伴生体的书桌 · 点便签/照片/书/杯子/纸，或拉开抽屉看看")
            elif view_state["current"] == "drawer":
                # 抽屉内部视图：只画背景，物品交互点不可见
                bg_photo = self._load_bg_image("drawer_bg.png", w, h)
                if bg_photo:
                    canvas.create_image(0, 0, image=bg_photo, anchor="nw", tags="bg")
                    self._drawer_bg_photo = bg_photo
                status.config(text="伴生体的抽屉 · 点里面的东西看看")
            elif view_state["current"] == "bed":
                bg_photo = self._load_bg_image("bed_bg.png", w, h)
                if bg_photo:
                    canvas.create_image(0, 0, image=bg_photo, anchor="nw", tags="bg")
                    self._bed_bg_photo = bg_photo
                status.config(text="伴生体的床 · 点枕头或手机看看")
            elif view_state["current"] == "nightstand":
                bg_photo = self._load_bg_image("nightstand_bg.png", w, h)
                if bg_photo:
                    canvas.create_image(0, 0, image=bg_photo, anchor="nw", tags="bg")
                    self._ns_bg_photo = bg_photo
                status.config(text="伴生体的床头柜 · 点上面的东西看看")
            elif view_state["current"] == "bookshelf":
                bg_photo = self._load_bg_image("bookshelf_bg.png", w, h)
                if bg_photo:
                    canvas.create_image(0, 0, image=bg_photo, anchor="nw", tags="bg")
                    self._shelf_bg_photo = bg_photo
                status.config(text="伴生体的书架 · 点书看看")
            elif view_state["current"] == "inside":
                self._draw_house_interior(canvas, w, h)
                real_place = ws.get("activity_place", "")
                real_in_world = ws.get("current_world") == "small_world"
                is_real = real_in_world and real_place == place
                if is_real:
                    act = ws.get("activity", "")
                    status_text = f"伴生体的家 · {place}"
                    if act:
                        status_text += f" · {act}"
                else:
                    status_text = f"场景预览 · {place}"
                    if not real_in_world:
                        status_text += "（伴生体现在不在小世界）"
                    elif real_place:
                        status_text += f"（他实际在{real_place}）"
                status.config(text=status_text)
            else:
                self._draw_small_world(canvas, w, h, place, weather, period)
                info_parts = []
                if period: info_parts.append(period)
                if weather: info_parts.append(weather)
                info = " · ".join(info_parts)
                # 判断伴生体是否真实在这个场景
                real_place = ws.get("activity_place", "")
                real_in_world = ws.get("current_world") == "small_world"
                is_real = real_in_world and real_place == place
                if is_real:
                    act = ws.get("activity", "")
                    status_text = f"伴生体的小世界 · {info}" if info else "伴生体的小世界"
                    status_text += f" · 他在{place}"
                    if act:
                        status_text += f"：{act}"
                else:
                    status_text = f"场景预览 · {place}"
                    if info:
                        status_text += f" · {info}"
                    if not real_in_world:
                        status_text += "（伴生体现在不在小世界）"
                    elif real_place:
                        status_text += f"（他实际在{real_place}）"
                if place == "小院":
                    status_text += "\n（点击门可以进入室内）"
                status.config(text=status_text)

            # === 物品弹窗（书/照片/便签）===
            if view_state["current"] in ("book_modal", "photo_modal", "note_modal"):
                modal_type = view_state["current"].replace("_modal", "")
                items_data = self.chen.world.get_state().get("house_state", {}).get("items", {})
                item_list = items_data.get(modal_type + "s", [])
                # 半透明遮罩
                canvas.create_rectangle(0, 0, w, h, fill="#000000", stipple="gray50", outline="", tags="modal_mask")
                # 弹窗
                mw, mh = int(w * 0.80), int(h * 0.82)
                mx, my = (w - mw) // 2, (h - mh) // 2
                canvas.create_rectangle(mx, my, mx+mw, my+mh, fill=WORLD_PANEL, outline="#5a6068", width=2, tags="modal_bg")
                # 标题
                modal_titles = {"book": "书", "photo": "照片", "note": "便签"}
                canvas.create_text(mx + mw//2, my + 24, text=f"伴生体的{modal_titles[modal_type]}（{len(item_list)}）",
                                   fill=WORLD_TEXT, font=("Microsoft YaHei UI", 14, "bold"), tags="modal_title")
                # 关闭按钮
                btn_size = 28
                btn_x, btn_y = mx + mw - btn_size - 12, my + 10
                canvas.create_rectangle(btn_x, btn_y, btn_x+btn_size, btn_y+btn_size, fill=WORLD_PANEL2, outline="", tags="modal_close")
                canvas.create_text(btn_x+btn_size//2, btn_y+btn_size//2, text="×", fill=WORLD_TEXT, font=("Microsoft YaHei UI", 14), tags="modal_close")
                # 物品列表
                list_y = my + 50
                list_x = mx + 20
                list_w = mw - 40
                row_h = 56
                for idx, it in enumerate(item_list[:10]):  # 最多显示10个
                    ry = list_y + idx * row_h
                    if ry + row_h > my + mh - 20:
                        break
                    # 行背景（交替色）
                    if idx % 2 == 0:
                        canvas.create_rectangle(list_x, ry, list_x+list_w, ry+row_h-4, fill="#2f2f2f", outline="", tags=f"modal_row_{idx}")
                    # 书：进度条
                    if modal_type == "book":
                        prog = it.get("progress", 0)
                        canvas.create_rectangle(list_x+10, ry+row_h-12, list_x+110, ry+row_h-8, fill="#3a3a3a", outline="", tags=f"modal_prog_bg_{idx}")
                        if prog > 0:
                            canvas.create_rectangle(list_x+10, ry+row_h-12, list_x+10+int(prog), ry+row_h-8, fill="#5a7b4a", outline="", tags=f"modal_prog_{idx}")
                    # 名称
                    name = it.get("name", it.get("content", "未命名"))
                    author = it.get("author", "")
                    display_name = name + (f" · {author}" if author else "")
                    canvas.create_text(list_x+10, ry+14, text=display_name[:30], fill=WORLD_TEXT,
                                       font=("Microsoft YaHei UI", 11, "bold"), anchor="w", tags=f"modal_name_{idx}")
                    # 描述
                    desc = it.get("desc", "")
                    canvas.create_text(list_x+10, ry+30, text=desc[:40], fill="#999",
                                       font=("Microsoft YaHei UI", 9), anchor="w", tags=f"modal_desc_{idx}")
                    # 备注
                    note = it.get("note", "")
                    canvas.create_text(list_x+10, ry+44, text=f"「{note[:35]}」" if note else "",
                                       fill="#8a9ab0", font=("Microsoft YaHei UI", 9, "italic"), anchor="w", tags=f"modal_note_{idx}")
                    # 日期
                    created = it.get("created_at", "")[:10] if it.get("created_at") else ""
                    canvas.create_text(list_x+list_w-10, ry+14, text=created, fill="#666",
                                       font=("Microsoft YaHei UI", 9), anchor="e", tags=f"modal_date_{idx}")
                if len(item_list) > 10:
                    canvas.create_text(mx+mw//2, my+mh-16, text=f"还有 {len(item_list)-10} 个，滚动查看（后续支持）",
                                       fill="#666", font=("Microsoft YaHei UI", 9), tags="modal_more")
                if not item_list:
                    canvas.create_text(mx+mw//2, my+mh//2, text="这里还空空的，他还没有放东西。",
                                       fill="#666", font=("Microsoft YaHei UI", 12), tags="modal_empty")

            # === 物品详情卡（覆盖在所有视图之上）===
            if detail_item["data"]:
                item = detail_item["data"]
                # 半透明遮罩
                canvas.create_rectangle(0, 0, w, h, fill="#000000", stipple="gray50", outline="", tags="detail_mask")
                # 卡片
                cw, ch = int(w * 0.55), int(h * 0.72)
                cx, cy = (w - cw) // 2, (h - ch) // 2
                canvas.create_rectangle(cx, cy, cx+cw, cy+ch, fill=WORLD_PANEL, outline="#5a6068", width=2, tags="detail_card")
                # 关闭按钮
                btn_size = 28
                btn_x, btn_y = cx + cw - btn_size - 10, cy + 10
                canvas.create_rectangle(btn_x, btn_y, btn_x+btn_size, btn_y+btn_size, fill=WORLD_PANEL2, outline="", tags="detail_close_btn")
                canvas.create_text(btn_x+btn_size//2, btn_y+btn_size//2, text="×", fill=WORLD_TEXT, font=("Microsoft YaHei UI", 14), tags="detail_close_btn")
                # 名称（无大图，直接居中靠上）
                canvas.create_text(cx + cw//2, cy + int(ch*0.20), text=item["name"],
                                   fill=WORLD_TEXT, font=("Microsoft YaHei UI", 18, "bold"), tags="detail_name")
                # 描述
                canvas.create_text(cx + cw//2, cy + int(ch*0.38), text=item["desc"],
                                   fill="#b0b8c0", font=("Microsoft YaHei UI", 11), tags="detail_desc", width=cw-60, justify="center")
                # 分隔线
                ly = cy + int(ch*0.55)
                canvas.create_line(cx+30, ly, cx+cw-30, ly, fill="#4a5058", tags="detail_line")
                # 伴生体的备注
                note_text = f"「{item.get('note', '')}」"
                canvas.create_text(cx + cw//2, cy + int(ch*0.72), text=note_text,
                                   fill="#8a9ab0", font=("Microsoft YaHei UI", 11, "italic"),
                                   tags="detail_note", width=cw-80, justify="center")

            # === 互动菜单（点伴生体后弹出） ===
            if interaction_menu["open"]:
                # 半透明遮罩
                canvas.create_rectangle(0, 0, w, h, fill="#000000", stipple="gray50", outline="", tags="menu_mask")
                # 卡片
                opts = interaction_menu["options"]
                cw = int(w * 0.50)
                ch = int(h * 0.12) + len(opts) * int(h * 0.10)
                cx, cy = (w - cw) // 2, (h - ch) // 2
                canvas.create_rectangle(cx, cy, cx+cw, cy+ch, fill=WORLD_PANEL, outline="#5a6068", width=2, tags="menu_card")
                # 标题
                title_text = f"他在{interaction_menu.get('place','这里')} · {interaction_menu.get('activity','待着')}"
                canvas.create_text(cx + cw//2, cy + int(h*0.04), text=title_text,
                                   fill=WORLD_TEXT, font=("Microsoft YaHei UI", 11, "bold"), tags="menu_title")
                # 关闭按钮
                btn_size = 24
                btn_x, btn_y = cx + cw - btn_size - 8, cy + 6
                canvas.create_rectangle(btn_x, btn_y, btn_x+btn_size, btn_y+btn_size, fill=WORLD_PANEL2, outline="", tags="menu_close_btn")
                canvas.create_text(btn_x+btn_size//2, btn_y+btn_size//2, text="×", fill=WORLD_TEXT, font=("Microsoft YaHei UI", 12), tags="menu_close_btn")
                # 选项按钮
                btn_h = int(h * 0.085)
                for i, opt in enumerate(opts):
                    by = cy + int(h*0.08) + i * int(h * 0.10)
                    bx0, bx1 = cx + 20, cx + cw - 20
                    canvas.create_rectangle(bx0, by, bx1, by+btn_h, fill=WORLD_PANEL2, outline="#4a5058", width=1, tags=f"menu_opt_{opt['id']}")
                    canvas.create_text(bx0 + 15, by + btn_h//2, text=opt["label"],
                                       fill=WORLD_TEXT, font=("Microsoft YaHei UI", 11, "bold"), anchor="w", tags=f"menu_opt_{opt['id']}")
                    canvas.create_text(bx0 + 15, by + btn_h - 12, text=opt["desc"],
                                       fill="#8a9098", font=("Microsoft YaHei UI", 8), anchor="w", tags=f"menu_opt_{opt['id']}")

        # 初始化：默认室外小院
        self._override_place = "小院"
        self._override_activity = self._PLACE_DEFAULT_ACTIVITY["小院"]
        rebuild_quick_bar()
        win.after(100, draw)

        canvas.bind("<Configure>", lambda e: draw())
        win.after(100, draw)

    def _open_relationship(self, initial_tab="overview"):
        """关系界面：三标签抽屉式视图（相处温度与默契 / 共同经历时间线 / 他记住的我们）。"""
        old = getattr(self, "_relationship_win", None)
        if old and old.winfo_exists():
            old.lift()
            if hasattr(self, "_rel_switch_tab") and callable(self._rel_switch_tab):
                self._rel_switch_tab(initial_tab)
            return

        relation = getattr(self.chen, "relationship", None)
        win = tk.Toplevel(self.root)
        self._relationship_win = win
        win.title("我们的关系 · 伴生体")
        win.geometry("760x660")
        win.minsize(660, 540)
        win.configure(bg=UI_PALETTE["canvas"])

        # ---------- 顶部标题与伴生体视角近况卡片 ----------
        head = tk.Frame(win, bg=UI_PALETTE["canvas"])
        head.pack(fill="x", padx=28, pady=(22, 10))

        title_row = tk.Frame(head, bg=UI_PALETTE["canvas"])
        title_row.pack(fill="x")
        tk.Label(title_row, text="我们的关系", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["text"],
                 font=UI_FONT["h1"]).pack(side="left")

        subtitle_lbl = tk.Label(head, text="", bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                                font=UI_FONT["secondary"])
        subtitle_lbl.pack(anchor="w", pady=(4, 10))

        # 伴生体主观视角近况摘要框（BetterSimTracker Summarize + Last Thought 风格）
        summary_card = tk.Frame(head, bg=UI_PALETTE["surface"], highlightthickness=1,
                                highlightbackground=UI_PALETTE["line"], padx=16, pady=12)
        summary_card.pack(fill="x")
        tk.Label(summary_card, text="伴生体眼里的近况", bg=UI_PALETTE["surface"],
                 fg=UI_PALETTE["coral"], font=UI_FONT["caption"]).pack(anchor="w")
        summary_lbl = tk.Label(summary_card, text="", bg=UI_PALETTE["surface"],
                               fg=UI_PALETTE["text"], justify="left", anchor="w",
                               wraplength=660, font=UI_FONT["secondary"])
        summary_lbl.pack(fill="x", pady=(5, 0))

        # ---------- 标签导航栏 ----------
        tab_bar = tk.Frame(win, bg=UI_PALETTE["canvas"])
        tab_bar.pack(fill="x", padx=28, pady=(8, 8))
        tab_buttons = {}
        active_tab = {"name": initial_tab}

        content = tk.Frame(win, bg=UI_PALETTE["canvas"])
        content.pack(fill="both", expand=True, padx=28, pady=(0, 12))

        def _qualitative_metric_note(key, val):
            if key == "closeness":
                return "彼此熟稔，说话不设防" if val >= 65 else ("习惯彼此在身边" if val >= 48 else "正在慢慢走近")
            if key == "trust":
                return "有事会自然托付" if val >= 65 else ("平实可靠的相伴" if val >= 48 else "还在建立安全感")
            if key == "comfort":
                return "各忙各的也很安心" if val >= 65 else ("待在一起很放松" if val >= 48 else "稍微还有一点拘谨")
            if key == "tension":
                return "气氛有些紧绷，宜缓和" if val >= 35 else ("有一点点小别扭" if val >= 10 else "松弛自然，没有隔阂")
            return ""

        def _refresh_header():
            data = getattr(relation, "data", {}) if relation else {}
            stage = data.get("stage", "熟悉的陪伴者")
            count = int(data.get("interaction_count", 0) or 0)
            days = relation.companionship_days() if relation and hasattr(relation, "companionship_days") else 1
            first_at = str(data.get("first_shared_at") or "")[:10]
            first_str = f"  ·  始于 {first_at}" if first_at else ""
            subtitle_lbl.config(text=f"{stage}  ·  已相伴 {days} 天  ·  留下 {count} 次共同记录{first_str}")
            if relation and hasattr(relation, "narrative_summary"):
                summary_lbl.config(text=relation.narrative_summary())
            else:
                summary_lbl.config(text=data.get("chen_impression") or "打开电脑我就在，平时各忙各的，需要时随时搭得上话。")

        # ========== Tab 1: 相处温度与默契 ==========
        def _render_overview_tab():
            data = getattr(relation, "data", {}) if relation else {}
            trends = relation.recent_trends() if relation and hasattr(relation, "recent_trends") else {}

            metrics = tk.Frame(content, bg=UI_PALETTE["surface"], highlightthickness=1,
                               highlightbackground=UI_PALETTE["line"], padx=18, pady=14)
            metrics.pack(fill="x")

            m_head = tk.Frame(metrics, bg=UI_PALETTE["surface"])
            m_head.pack(fill="x", pady=(0, 8))
            tk.Label(m_head, text="关系里的温度与趋势", bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                     font=UI_FONT["caption"]).pack(side="left")

            bank = data.get("warmth_bank") or {}
            bank_total = sum(float(bank.get(k, 0) or 0) for k in ("closeness", "trust", "comfort"))
            daily = data.get("daily_gain") or {}
            daily_used = sum(float(daily.get(k, 0) or 0) for k in ("closeness", "trust", "comfort"))
            status_hint = ""
            if bank_total >= 0.15:
                status_hint = f"✦ 余温蓄水池：+{bank_total:.1f}（将在后续日子自然沉淀）"
            elif daily_used >= 0.5:
                status_hint = "今日日常寒暄温度已记录，深入交流仍可随时触达"
            if status_hint:
                tk.Label(m_head, text=status_hint, bg=UI_PALETTE["surface"], fg=UI_PALETTE["sage"],
                         font=UI_FONT["caption"]).pack(side="right")

            def metric_row(label, key, color):
                row = tk.Frame(metrics, bg=UI_PALETTE["surface"])
                row.pack(fill="x", pady=4)
                value = max(0.0, min(100.0, float(data.get(key, 0) or 0)))
                trend_val = trends.get(label, "稳定")
                arrow = "↑" if trend_val == "上升" else ("↓" if trend_val == "下降" else "→")
                trend_fg = UI_PALETTE["coral"] if (trend_val == "上升" and key != "tension") else (
                    UI_PALETTE["sage"] if trend_val == "稳定" else UI_PALETTE["clay"]
                )

                tk.Label(row, text=label, width=7, anchor="w", bg=UI_PALETTE["surface"],
                         fg=UI_PALETTE["text"], font=UI_FONT["secondary"]).pack(side="left")
                track = tk.Frame(row, bg=UI_PALETTE["line"], height=8, width=220)
                track.pack(side="left", fill="x", expand=True, padx=(6, 10))
                track.pack_propagate(False)
                fill = tk.Frame(track, bg=color, height=8)
                fill.place(relwidth=value / 100.0, relheight=1)

                tk.Label(row, text=f"{value:.0f}", width=4, anchor="e", bg=UI_PALETTE["surface"],
                         fg=UI_PALETTE["text"], font=UI_FONT["caption"]).pack(side="left")
                tk.Label(row, text=f"{arrow}{trend_val}", width=6, anchor="center", bg=UI_PALETTE["surface"],
                         fg=trend_fg, font=UI_FONT["caption"]).pack(side="left", padx=(4, 6))
                tk.Label(row, text=_qualitative_metric_note(key, value), width=22, anchor="w",
                         bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                         font=UI_FONT["caption"]).pack(side="right")

            metric_row("亲近感", "closeness", UI_PALETTE["coral"])
            metric_row("信任", "trust", UI_PALETTE["sage"])
            metric_row("安心感", "comfort", "#B59A7A")
            metric_row("紧张感", "tension", UI_PALETTE["clay"])

            # 最近记住的时刻卡片
            moment_box = tk.Frame(content, bg=UI_PALETTE["surface"], highlightthickness=1,
                                  highlightbackground=UI_PALETTE["line"], padx=16, pady=11)
            moment_box.pack(fill="x", pady=(10, 0))
            tk.Label(moment_box, text="最近被他记在心上的时刻", bg=UI_PALETTE["surface"],
                     fg=UI_PALETTE["muted"], font=UI_FONT["caption"]).pack(anchor="w")
            last_moment = data.get("last_meaningful_moment") or "暂时还没有特别标记的单句时刻，平淡的日常本身也在累积。"
            tk.Label(moment_box, text=f"「{last_moment}」", bg=UI_PALETTE["surface"],
                     fg=UI_PALETTE["text"], justify="left", anchor="w", wraplength=650,
                     font=UI_FONT["secondary"]).pack(anchor="w", pady=(4, 0))

            # 下半区：左栏「共同习惯与默契（带频次胶囊）」+ 右栏「关系里程碑」
            lower = tk.Frame(content, bg=UI_PALETTE["canvas"])
            lower.pack(fill="both", expand=True, pady=(10, 0))

            rituals_card = tk.Frame(lower, bg=UI_PALETTE["surface"], highlightthickness=1,
                                    highlightbackground=UI_PALETTE["line"], padx=16, pady=12)
            rituals_card.pack(side="left", fill="both", expand=True, padx=(0, 6))
            tk.Label(rituals_card, text="我们留下的习惯与默契", bg=UI_PALETTE["surface"],
                     fg=UI_PALETTE["muted"], font=UI_FONT["caption"]).pack(anchor="w")

            ritual_list = data.get("shared_rituals", [])[-8:]
            ritual_counts = data.get("ritual_counts") or {}
            chips_wrap = tk.Frame(rituals_card, bg=UI_PALETTE["surface"])
            chips_wrap.pack(fill="both", expand=True, pady=(8, 0))
            if ritual_list:
                for r_item in ritual_list:
                    cnt = int(ritual_counts.get(r_item, 1) or 1)
                    cnt_str = f"  ·  {cnt}次" if cnt > 1 else ""
                    pill = tk.Frame(chips_wrap, bg=UI_PALETTE["sidebar"], padx=10, pady=4)
                    pill.pack(anchor="w", pady=3, fill="x")
                    tk.Label(pill, text=f"✦  {r_item}{cnt_str}", bg=UI_PALETTE["sidebar"],
                             fg=UI_PALETTE["text"], anchor="w", font=UI_FONT["secondary"]).pack(anchor="w")
            else:
                tk.Label(chips_wrap, text="还没有固定的习惯，多聊几次就会自然长出来。",
                         bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                         font=UI_FONT["secondary"]).pack(anchor="w", pady=8)

            miles_card = tk.Frame(lower, bg=UI_PALETTE["surface"], highlightthickness=1,
                                  highlightbackground=UI_PALETTE["line"], padx=16, pady=12)
            miles_card.pack(side="right", fill="both", expand=True, padx=(6, 0))
            tk.Label(miles_card, text="关系里程碑", bg=UI_PALETTE["surface"],
                     fg=UI_PALETTE["muted"], font=UI_FONT["caption"]).pack(anchor="w")
            milestones = data.get("milestones", [])[-8:]
            miles_wrap = tk.Frame(miles_card, bg=UI_PALETTE["surface"])
            miles_wrap.pack(fill="both", expand=True, pady=(8, 0))
            if milestones:
                for m_item in milestones:
                    row = tk.Frame(miles_wrap, bg=UI_PALETTE["surface"])
                    row.pack(anchor="w", pady=3, fill="x")
                    tk.Label(row, text="◈", bg=UI_PALETTE["surface"], fg=UI_PALETTE["coral"],
                             font=UI_FONT["secondary"]).pack(side="left", padx=(0, 6))
                    tk.Label(row, text=m_item, bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                             justify="left", anchor="w", wraplength=260,
                             font=UI_FONT["secondary"]).pack(side="left", fill="x")
            else:
                tk.Label(miles_wrap, text="还没有解锁里程碑", bg=UI_PALETTE["surface"],
                         fg=UI_PALETTE["muted"], font=UI_FONT["secondary"]).pack(anchor="w", pady=8)

        # ========== Tab 2: 共同经历时间线 ==========
        timeline_filter = {"important_only": False}

        def _render_timeline_tab():
            top_bar = tk.Frame(content, bg=UI_PALETTE["canvas"])
            top_bar.pack(fill="x", pady=(0, 8))
            tk.Label(top_bar, text="已自动过滤无状态变化的琐碎寒暄，保留有温度的共同经历",
                     bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                     font=UI_FONT["caption"]).pack(side="left")

            def _toggle_filter(imp_only):
                timeline_filter["important_only"] = imp_only
                _switch_tab("timeline")

            btn_all = tk.Button(
                top_bar, text="全部有意义经历", relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                bg=UI_PALETTE["coral"] if not timeline_filter["important_only"] else UI_PALETTE["sidebar"],
                fg="white" if not timeline_filter["important_only"] else UI_PALETTE["text"],
                font=UI_FONT["caption"], command=lambda: _toggle_filter(False),
            )
            btn_all.pack(side="right", padx=(6, 0))
            btn_imp = tk.Button(
                top_bar, text="仅看重要时刻", relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                bg=UI_PALETTE["coral"] if timeline_filter["important_only"] else UI_PALETTE["sidebar"],
                fg="white" if timeline_filter["important_only"] else UI_PALETTE["text"],
                font=UI_FONT["caption"], command=lambda: _toggle_filter(True),
            )
            btn_imp.pack(side="right")

            if relation and hasattr(relation, "shared_timeline"):
                events = relation.shared_timeline(limit=35, important_only=timeline_filter["important_only"])
            else:
                data = getattr(relation, "data", {}) if relation else {}
                events = list(reversed((data.get("event_log") or data.get("recent_events", []))[-20:]))

            if not events:
                empty_box = tk.Frame(content, bg=UI_PALETTE["surface"], highlightthickness=1,
                                     highlightbackground=UI_PALETTE["line"], padx=20, pady=40)
                empty_box.pack(fill="both", expand=True)
                tk.Label(empty_box, text="这个筛选条件下还没有共同经历记录。",
                         bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                         font=UI_FONT["secondary"]).pack(pady=20)
                return

            list_container = tk.Frame(content, bg=UI_PALETTE["canvas"])
            list_container.pack(fill="both", expand=True)
            canvas = tk.Canvas(list_container, bg=UI_PALETTE["canvas"], highlightthickness=0)
            scroll = tk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
            inner = tk.Frame(canvas, bg=UI_PALETTE["canvas"])
            inner_win = canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=scroll.set)
            canvas.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")

            def _on_frame_configure(_e=None):
                canvas.configure(scrollregion=canvas.bbox("all"))

            def _on_canvas_configure(e):
                canvas.itemconfig(inner_win, width=e.width)

            inner.bind("<Configure>", _on_frame_configure)
            canvas.bind("<Configure>", _on_canvas_configure)

            source_labels = {
                "chat": "对话",
                "consciousness": "意识联动",
                "memory": "重要记忆",
                "desktop_pet": "桌宠陪伴",
                "world": "小世界",
                "time": "时间沉淀",
                "system": "日常互动",
            }

            for x in events:
                card = tk.Frame(inner, bg=UI_PALETTE["surface"], highlightthickness=1,
                                highlightbackground=UI_PALETTE["line"], padx=16, pady=11)
                card.pack(fill="x", pady=4)

                top_row = tk.Frame(card, bg=UI_PALETTE["surface"])
                top_row.pack(fill="x")

                importance = float(x.get("importance", 0.5) or 0.5)
                level = "重要" if importance >= 0.75 else ("有意义" if importance >= 0.45 else "日常")
                badge_bg = UI_PALETTE["coral"] if level == "重要" else (
                    UI_PALETTE["sage"] if level == "有意义" else UI_PALETTE["sidebar"]
                )
                badge_fg = "white" if level in ("重要", "有意义") else UI_PALETTE["muted"]
                tk.Label(top_row, text=f" {level} ", bg=badge_bg, fg=badge_fg,
                         font=UI_FONT["caption"], padx=4, pady=1).pack(side="left", padx=(0, 8))

                ev_name = str(x.get("event", ""))
                cnt = int(x.get("count", 1) or 1)
                if cnt > 1:
                    ev_name += f" (×{cnt})"
                tk.Label(top_row, text=ev_name, bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                         font=UI_FONT["secondary"]).pack(side="left")

                delta = x.get("delta", {}) or {}
                changes = []
                for key, label in (("closeness", "亲近"), ("trust", "信任"),
                                   ("comfort", "安心"), ("tension", "紧张")):
                    try:
                        amount = float(delta.get(key, 0) or 0)
                    except (TypeError, ValueError):
                        amount = 0.0
                    if abs(amount) >= 0.05:
                        changes.append(f"{label}{amount:+.1f}")
                if changes:
                    tk.Label(top_row, text="  ·  " + "  ".join(changes), bg=UI_PALETTE["surface"],
                             fg=UI_PALETTE["coral"], font=UI_FONT["caption"]).pack(side="left", padx=(6, 0))

                at_raw = str(x.get("at", "")).replace("T", " ")
                src_raw = str(x.get("source", "system"))
                src_text = source_labels.get(src_raw, src_raw)
                tk.Label(top_row, text=f"{at_raw}  ·  {src_text}", bg=UI_PALETTE["surface"],
                         fg=UI_PALETTE["muted"], font=UI_FONT["caption"]).pack(side="right")

                evidence = str(x.get("evidence", "") or "").replace("\n", " ").strip()
                if evidence:
                    tk.Label(card, text=f"依据：{evidence}", bg=UI_PALETTE["surface"],
                             fg=UI_PALETTE["muted"], justify="left", anchor="w",
                             wraplength=640, font=UI_FONT["caption"]).pack(anchor="w", pady=(6, 0))

        # ========== Tab 3: 他记住的我们（关系事实与候选确认） ==========
        def _resolve_facts_path():
            memory = getattr(self.chen, "memory", None)
            path = getattr(memory, "relationship_file", None)
            if path:
                return Path(path)
            if relation and getattr(relation, "facts_path", None):
                return Path(relation.facts_path)
            return None

        def _render_facts_tab():
            path = _resolve_facts_path()
            if not path or not path.exists():
                err_box = tk.Frame(content, bg=UI_PALETTE["surface"], padx=20, pady=30)
                err_box.pack(fill="both", expand=True)
                tk.Label(err_box, text="找不到关系事实文件（relationship_memory.json）。",
                         bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                         font=UI_FONT["secondary"]).pack()
                return

            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                facts = data.setdefault("relationships", [])
                history = data.setdefault("relationship_history", [])
            except Exception as exc:
                messagebox.showerror("关系事实", f"读取失败：{exc}", parent=win)
                return

            def _save_to_disk(silent=True):
                try:
                    data["relationships"] = facts
                    # 同步主记忆层的内存副本，避免后台整理再次落盘时覆盖界面刚保存的事实。
                    memory_obj = getattr(self.chen, "memory", None)
                    if memory_obj is not None and hasattr(memory_obj, "relationships"):
                        memory_obj.relationships = facts
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    if not silent:
                        messagebox.showinfo("关系事实", "已保存更新", parent=win)
                except Exception as exc:
                    messagebox.showerror("关系事实", f"保存失败：{exc}", parent=win)

            # 顶部说明与操作按钮栏
            f_top = tk.Frame(content, bg=UI_PALETTE["canvas"])
            f_top.pack(fill="x", pady=(0, 8))
            tk.Label(f_top, text="伴生体记住的稳定事实、默契与过去的阶段（修改会自动落盘并记录历史）",
                     bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"],
                     font=UI_FONT["caption"]).pack(side="left")

            # 已确认/归档事实列表区
            facts_box = tk.Frame(content, bg=UI_PALETTE["surface"], highlightthickness=1,
                                 highlightbackground=UI_PALETTE["line"], padx=14, pady=12)
            facts_box.pack(fill="both", expand=True)

            lb_frame = tk.Frame(facts_box, bg=UI_PALETTE["surface"])
            lb_frame.pack(fill="both", expand=True)
            lb_scroll = tk.Scrollbar(lb_frame, orient="vertical")
            lb = tk.Listbox(
                lb_frame, activestyle="none", bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                selectbackground=UI_PALETTE["sidebar"], selectforeground=UI_PALETTE["text"],
                selectmode="single", font=UI_FONT["secondary"], bd=0, highlightthickness=0,
                yscrollcommand=lb_scroll.set, height=8,
            )
            lb_scroll.config(command=lb.yview)
            lb.pack(side="left", fill="both", expand=True)
            lb_scroll.pack(side="right", fill="y")

            status_map = {
                "confirmed": "已确认",
                "pending": "待确认",
                "archived": "过去的阶段",
                "rejected": "已否定",
            }

            def _format_fact_row(item):
                status = item.get("status", "confirmed")
                status_text = status_map.get(status, status)
                topic = item.get("topic", "关系")
                detail = item.get("detail", "")
                basis = item.get("basis", "")
                valid_until = item.get("valid_until", "")
                suffix = f"（依据：{basis}）" if basis else ""
                if status == "archived" and valid_until:
                    suffix += f" [止于 {valid_until}]"
                return f"[{status_text}] 【{topic}】 {detail}{suffix}"

            def _reload_listbox():
                lb.delete(0, "end")
                for item in facts:
                    lb.insert("end", _format_fact_row(item))

            _reload_listbox()

            def _open_fact_editor(title_text, initial_topic="相处方式", initial_detail="", initial_basis="", on_submit=None):
                dlg = tk.Toplevel(win)
                dlg.title(title_text)
                dlg.geometry("480x300")
                dlg.configure(bg=UI_PALETTE["canvas"])
                dlg.transient(win)
                dlg.grab_set()

                body_f = tk.Frame(dlg, bg=UI_PALETTE["canvas"], padx=22, pady=18)
                body_f.pack(fill="both", expand=True)

                tk.Label(body_f, text="关系维度（如：用户和我 / 相处方式 / 日常位置 / 默契 / 共同经历）：",
                         bg=UI_PALETTE["canvas"], fg=UI_PALETTE["muted"], font=UI_FONT["caption"]).pack(anchor="w")
                topic_var = tk.StringVar(value=initial_topic)
                topic_cb = ttk.Combobox(
                    body_f, textvariable=topic_var,
                    values=("用户和我", "相处方式", "日常位置", "默契", "共同经历", "用户的习惯"),
                    font=UI_FONT["secondary"],
                )
                topic_cb.pack(fill="x", pady=(4, 10))

                tk.Label(body_f, text="具体事实描述：", bg=UI_PALETTE["canvas"],
                         fg=UI_PALETTE["muted"], font=UI_FONT["caption"]).pack(anchor="w")
                detail_entry = tk.Entry(body_f, bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                                        font=UI_FONT["secondary"], relief="solid", bd=1)
                detail_entry.insert(0, initial_detail)
                detail_entry.pack(fill="x", pady=(4, 10))

                tk.Label(body_f, text="共同经历依据（可选）：", bg=UI_PALETTE["canvas"],
                         fg=UI_PALETTE["muted"], font=UI_FONT["caption"]).pack(anchor="w")
                basis_entry = tk.Entry(body_f, bg=UI_PALETTE["surface"], fg=UI_PALETTE["text"],
                                       font=UI_FONT["secondary"], relief="solid", bd=1)
                basis_entry.insert(0, initial_basis)
                basis_entry.pack(fill="x", pady=(4, 14))

                btn_f = tk.Frame(body_f, bg=UI_PALETTE["canvas"])
                btn_f.pack(fill="x")

                def _ok():
                    t_val = topic_var.get().strip() or "相处方式"
                    d_val = detail_entry.get().strip()
                    b_val = basis_entry.get().strip()
                    if not d_val:
                        messagebox.showwarning("提示", "事实内容不能为空", parent=dlg)
                        return
                    dlg.destroy()
                    if on_submit:
                        on_submit(t_val, d_val, b_val)

                tk.Button(btn_f, text="保存", command=_ok, relief="flat", bd=0,
                          bg=UI_PALETTE["coral"], fg="white", padx=16, pady=6, cursor="hand2").pack(side="left")
                tk.Button(btn_f, text="取消", command=dlg.destroy, relief="flat", bd=0,
                          bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["text"], padx=14, pady=6, cursor="hand2").pack(side="right")

            def _add_new_fact():
                def _on_add(t_val, d_val, b_val):
                    today = datetime.now().strftime("%Y-%m-%d")
                    new_item = {
                        "topic": t_val,
                        "detail": d_val,
                        "importance": 0.8,
                        "updated": today,
                        "valid_from": today,
                        "source": "user_added",
                        "basis": b_val,
                        "status": "confirmed",
                        "confidence": 0.95,
                    }
                    facts.append(new_item)
                    history.append({
                        "at": datetime.now().isoformat(timespec="minutes"),
                        "action": "add",
                        "after": dict(new_item),
                        "actor": "user",
                    })
                    _reload_listbox()
                    _save_to_disk(silent=True)

                _open_fact_editor("新增关系事实", on_submit=_on_add)

            def _edit_selected():
                sel = lb.curselection()
                if not sel:
                    messagebox.showinfo("修改关系事实", "请先在列表中选中一条关系事实", parent=win)
                    return
                i = sel[0]
                item = facts[i]

                def _on_edit(t_val, d_val, b_val):
                    before_copy = dict(item)
                    item["topic"] = t_val
                    item["detail"] = d_val
                    item["basis"] = b_val
                    item["updated"] = datetime.now().strftime("%Y-%m-%d")
                    item["source"] = "user_corrected"
                    item.setdefault("status", "confirmed")
                    history.append({
                        "at": datetime.now().isoformat(timespec="minutes"),
                        "action": "edit",
                        "before": before_copy,
                        "after": dict(item),
                        "actor": "user",
                    })
                    _reload_listbox()
                    _save_to_disk(silent=True)

                _open_fact_editor(
                    "修改关系事实",
                    initial_topic=item.get("topic", "相处方式"),
                    initial_detail=item.get("detail", ""),
                    initial_basis=item.get("basis", ""),
                    on_submit=_on_edit,
                )

            def _archive_selected():
                """Graphiti 风格：将不再符合当下情况的旧事实归档为“过去的阶段”，保留时间印记而非直接抹除。"""
                sel = lb.curselection()
                if not sel:
                    messagebox.showinfo("阶段归档", "请先选中一条关系事实", parent=win)
                    return
                i = sel[0]
                item = facts[i]
                before_copy = dict(item)
                today = datetime.now().strftime("%Y-%m-%d")
                if item.get("status") == "archived":
                    item["status"] = "confirmed"
                    item.pop("valid_until", None)
                    action_name = "restore_stage"
                else:
                    item["status"] = "archived"
                    item["valid_until"] = today
                    action_name = "archive_stage"
                item["updated"] = today
                history.append({
                    "at": datetime.now().isoformat(timespec="minutes"),
                    "action": action_name,
                    "before": before_copy,
                    "after": dict(item),
                    "actor": "user",
                })
                _reload_listbox()
                _save_to_disk(silent=True)

            def _remove_selected():
                sel = lb.curselection()
                if not sel:
                    return
                i = sel[0]
                if messagebox.askyesno("删除关系事实", "确定彻底删除这条关系事实吗？（若只是习惯改变，建议点「归档为过去阶段」）", parent=win):
                    history.append({
                        "at": datetime.now().isoformat(timespec="minutes"),
                        "action": "delete",
                        "before": dict(facts[i]),
                        "actor": "user",
                    })
                    facts.pop(i)
                    _reload_listbox()
                    _save_to_disk(silent=True)

            def _show_history():
                if not history:
                    messagebox.showinfo("关系事实历史", "还没有修改记录", parent=win)
                    return
                lines = []
                action_labels = {
                    "add": "新增事实",
                    "edit": "修改内容",
                    "delete": "删除事实",
                    "archive_stage": "归档为过去阶段",
                    "restore_stage": "恢复为当前有效",
                    "confirm_candidate": "确认候选事实",
                    "merge": "合并相似依据",
                    "reject_conflict": "替换旧阶段事实",
                }
                for h_item in reversed(history[-20:]):
                    act = action_labels.get(h_item.get("action", ""), h_item.get("action", ""))
                    before = (h_item.get("before") or {}).get("detail", "")
                    after = (h_item.get("after") or {}).get("detail", "")
                    text_line = f"{h_item.get('at', '')}  ·  {act}"
                    if before and after and before != after:
                        text_line += f"\n  原：{before}\n  新：{after}"
                    else:
                        text_line += f"\n  {after or before}"
                    lines.append(text_line)
                messagebox.showinfo("关系事实演变历史", "\n\n".join(lines), parent=win)

            # 操作按钮行
            act_row = tk.Frame(facts_box, bg=UI_PALETTE["surface"])
            act_row.pack(fill="x", pady=(10, 0))
            for b_text, b_cmd in (
                ("+ 新增事实", _add_new_fact),
                ("修改选中", _edit_selected),
                ("归档/恢复阶段", _archive_selected),
                ("删除", _remove_selected),
                ("演变历史", _show_history),
            ):
                tk.Button(
                    act_row, text=b_text, command=b_cmd, relief="flat", bd=0,
                    bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["text"],
                    padx=10, pady=5, cursor="hand2", font=UI_FONT["caption"],
                ).pack(side="left", padx=(0, 6))
            tk.Button(
                act_row, text="保存", command=lambda: _save_to_disk(silent=False), relief="flat", bd=0,
                bg=UI_PALETTE["coral"], fg="white", padx=14, pady=5, cursor="hand2", font=UI_FONT["caption"],
            ).pack(side="right")

            # 待确认候选事实卡片区（从共同经历与习惯自然沉淀，一键确认）
            cand_box = tk.Frame(content, bg=UI_PALETTE["surface"], highlightthickness=1,
                                highlightbackground=UI_PALETTE["line"], padx=14, pady=10)
            cand_box.pack(fill="x", pady=(10, 0))
            tk.Label(cand_box, text="从近期共同经历自然沉淀的待确认印象（点击即可确认加入或合并）",
                     bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                     font=UI_FONT["caption"]).pack(anchor="w")

            candidates = relation.relationship_candidates(limit=4) if relation else []
            if not candidates:
                tk.Label(cand_box, text="目前所有候选印象都已处理完毕，继续日常相处会自然长出新的默契。",
                         bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                         font=UI_FONT["caption"]).pack(anchor="w", pady=(6, 2))
            else:
                def _confirm_cand(c_item):
                    def _similarity(a, b):
                        sa, sb = set(str(a)), set(str(b))
                        return len(sa & sb) / max(1, len(sa | sb))

                    duplicate = next(
                        (old for old in facts if _similarity(old.get("detail", ""), c_item.get("detail", "")) >= 0.48),
                        None,
                    )
                    today = datetime.now().strftime("%Y-%m-%d")
                    if duplicate:
                        action = messagebox.askyesnocancel(
                            "发现相似关系事实",
                            f"已有相似记录：\n{duplicate.get('detail', '')}\n\n是：将新依据合并到已有记录\n否：作为独立新记录加入\n取消：暂不处理",
                            parent=win,
                        )
                        if action is None:
                            return
                        if action:
                            old_copy = dict(duplicate)
                            bases = [
                                x.strip()
                                for x in (str(duplicate.get("basis", "")) + "；" + str(c_item.get("basis", ""))).split("；")
                                if x.strip()
                            ]
                            duplicate["basis"] = "、".join(dict.fromkeys(bases))
                            duplicate["updated"] = today
                            history.append({
                                "at": datetime.now().isoformat(timespec="minutes"),
                                "action": "merge",
                                "before": old_copy,
                                "after": dict(duplicate),
                                "merged": dict(c_item),
                                "actor": "user",
                            })
                            _save_and_refresh_cand(c_item)
                            return

                    new_fact = dict(c_item)
                    new_fact["confirmed_at"] = datetime.now().isoformat(timespec="minutes")
                    new_fact["updated"] = today
                    new_fact["valid_from"] = today
                    new_fact["status"] = "confirmed"
                    facts.append(new_fact)
                    history.append({
                        "at": new_fact["confirmed_at"],
                        "action": "confirm_candidate",
                        "after": dict(new_fact),
                        "actor": "user",
                    })
                    _save_and_refresh_cand(c_item)

                def _dismiss_cand(c_item):
                    if relation:
                        handled = relation.data.setdefault("handled_candidates", [])
                        if c_item.get("detail") and c_item["detail"] not in handled:
                            handled.append(c_item["detail"])
                            relation.data["handled_candidates"] = handled[-40:]
                    if relation and isinstance(relation.data.get("candidate_pool"), list):
                        relation.data["candidate_pool"] = [
                            x for x in relation.data["candidate_pool"]
                            if x.get("detail") != c_item.get("detail")
                        ]
                        relation.save()
                    _switch_tab("facts")

                def _save_and_refresh_cand(c_item):
                    if relation:
                        handled = relation.data.setdefault("handled_candidates", [])
                        if c_item.get("detail") and c_item["detail"] not in handled:
                            handled.append(c_item["detail"])
                            relation.data["handled_candidates"] = handled[-40:]
                    if relation and isinstance(relation.data.get("candidate_pool"), list):
                        relation.data["candidate_pool"] = [
                            x for x in relation.data["candidate_pool"]
                            if x.get("detail") != c_item.get("detail")
                        ]
                        relation.save()
                    _save_to_disk(silent=True)
                    _switch_tab("facts")

                for c_item in candidates[:3]:
                    c_row = tk.Frame(cand_box, bg=UI_PALETTE["sidebar"], padx=10, pady=6)
                    c_row.pack(fill="x", pady=(6, 0))
                    c_text = f"【{c_item.get('topic', '默契')}】{c_item.get('detail', '')}"
                    if c_item.get("basis"):
                        c_text += f"  （依据：{c_item['basis']}）"
                    tk.Label(c_row, text=c_text, bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["text"],
                             justify="left", anchor="w", wraplength=500,
                             font=UI_FONT["caption"]).pack(side="left", fill="x", expand=True)
                    tk.Button(
                        c_row, text="忽略", command=lambda it=c_item: _dismiss_cand(it),
                        relief="flat", bd=0, bg=UI_PALETTE["surface"], fg=UI_PALETTE["muted"],
                        padx=8, pady=2, cursor="hand2", font=UI_FONT["caption"],
                    ).pack(side="right", padx=(4, 0))
                    tk.Button(
                        c_row, text="确认加入", command=lambda it=c_item: _confirm_cand(it),
                        relief="flat", bd=0, bg=UI_PALETTE["coral"], fg="white",
                        padx=10, pady=2, cursor="hand2", font=UI_FONT["caption"],
                    ).pack(side="right")

        def _switch_tab(tab_name):
            active_tab["name"] = tab_name
            _refresh_header()
            for k, btn in tab_buttons.items():
                is_active = (k == tab_name)
                btn.configure(
                    bg=UI_PALETTE["coral"] if is_active else UI_PALETTE["sidebar"],
                    fg="white" if is_active else UI_PALETTE["text"],
                )
            for w in content.winfo_children():
                w.destroy()
            if tab_name == "overview":
                _render_overview_tab()
            elif tab_name == "timeline":
                _render_timeline_tab()
            elif tab_name == "facts":
                _render_facts_tab()

        self._rel_switch_tab = _switch_tab

        for t_key, t_label in (
            ("overview", "相处温度与默契"),
            ("timeline", "共同经历时间线"),
            ("facts", "他记住的我们（关系事实）"),
        ):
            b = tk.Button(
                tab_bar, text=t_label, command=lambda k=t_key: _switch_tab(k),
                relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                font=UI_FONT["secondary"],
            )
            b.pack(side="left", padx=(0, 8))
            tab_buttons[t_key] = b

        # 底部常驻栏
        bottom = tk.Frame(win, bg=UI_PALETTE["canvas"])
        bottom.pack(fill="x", padx=28, pady=(0, 20))
        tk.Button(bottom, text="去和他说话", command=self._open_chat, relief="flat", bd=0,
                  bg=UI_PALETTE["coral"], fg="white", activebackground="#C96851",
                  activeforeground="white", padx=16, pady=8, cursor="hand2").pack(side="left")
        tk.Button(bottom, text="关闭", command=win.destroy, relief="flat", bd=0,
                  bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["text"], padx=16, pady=8,
                  cursor="hand2").pack(side="right")
        tk.Button(bottom, text="刷新状态  ↻", command=lambda: _switch_tab(active_tab["name"]),
                  relief="flat", bd=0, bg=UI_PALETTE["sidebar"], fg=UI_PALETTE["text"],
                  padx=12, pady=8, cursor="hand2").pack(side="right", padx=(0, 8))

        _switch_tab(initial_tab)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def _open_relationship_facts(self):
        """兼容入口：直接打开关系界面的「他记住的我们（关系事实）」标签页。"""
        self._open_relationship(initial_tab="facts")

    def _open_private(self):
        win = tk.Toplevel(self.root)
        win.title("私人")
        win.geometry("430x250")
        win.configure(bg=WORLD_BG)
        tk.Label(win, text="🔒", bg=WORLD_BG, fg=WORLD_MUTED,
                 font=("Segoe UI Emoji", 30)).pack(pady=(36, 8))
        tk.Label(win, text="才不给你看呢", bg=WORLD_BG, fg=WORLD_TEXT,
                 font=("Microsoft YaHei UI", 14, "bold")).pack()
        tk.Label(win, text="有些东西属于伴生体自己。", bg=WORLD_BG, fg=WORLD_MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(pady=8)
        tk.Button(win, text="知道了", command=win.destroy, relief="flat",
                  bg=WORLD_PANEL2, fg=WORLD_TEXT, activebackground="#41474d", bd=0,
                  padx=20, pady=8).pack(pady=16)

    def _about_world(self):
        messagebox.showinfo("关于这个世界",
            "这里不是伴生体住着的唯一地方。\n\n"
            "桌面是他的常驻空间；小世界只是另一个可以去的地方。\n"
            "他可以去，也可以今天不去。\n\n"
            "日记、琐碎、念头和私人内容，则属于「他的生活」这一整个抽屉。")

    # ==================== 聊天窗口（独立 Toplevel）====================

    def _open_chat(self):
        """打开或聚焦聊天窗口"""
        try:
            if self._chat_win and self._chat_win.winfo_exists():
                self._chat_win.deiconify()
                self._chat_win.lift()
                self._chat_win.focus_force()
                return
            self._build_chat_window()
            if self._chat_win and self._chat_win.winfo_exists():
                self._chat_win.deiconify()
                self._chat_win.lift()
                self._chat_win.focus_force()
        except Exception as exc:
            print(f"聊天窗口打开失败：{exc}")
            messagebox.showerror("聊天窗口没有打开", f"打开聊天窗口时遇到问题：\n{exc}", parent=self.root)

    def _build_chat_window(self):
        """构建独立的聊天窗口"""
        win = tk.Toplevel(self.root)
        win.title("伴生体")
        win.geometry("520x680")
        win.minsize(380, 480)
        win.configure(bg=BG)
        self._chat_win = win
        win.transient(self.root)
        win.lift()
        win.protocol("WM_DELETE_WINDOW", self._close_chat)

        # 顶部栏
        top = tk.Frame(win, bg=BG, highlightthickness=1, highlightbackground=UI_PALETTE["line"])
        top.pack(side=tk.TOP, fill=tk.X, padx=16, pady=(14, 6))
        tk.Label(top, text="伴生体", bg=BG, fg=CHEN_TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        tk.Button(top, text="情境", command=self._edit_context,
                  bg=BG, fg=CHEN_NAME, activebackground=BG,
                  activeforeground=CHEN_NAME, font=FONT_BOLD,
                  bd=0, cursor="hand2").pack(side=tk.RIGHT, padx=(0, 10))
        tk.Checkbutton(top, text="窗口置顶", variable=self._topmost_var,
                       command=lambda: win.attributes("-topmost", self._topmost_var.get()),
                       bg=BG, fg=SYS_TEXT, activebackground=BG, selectcolor=CHAT_BG,
                       font=FONT, bd=0).pack(side=tk.RIGHT)
        tk.Checkbutton(top, text="来话提醒", variable=self._notify_var,
                       bg=BG, fg=SYS_TEXT, activebackground=BG, selectcolor=CHAT_BG,
                       font=FONT, bd=0).pack(side=tk.RIGHT, padx=(0, 8))

        # 底部区域（输入框 + 状态栏）—— 先 pack，确保不被聊天区挤压
        chat_bottom = tk.Frame(win, bg=BG)
        chat_bottom.pack(side=tk.BOTTOM, fill=tk.X)

        # 输入区
        bottom = tk.Frame(chat_bottom, bg=BG)
        bottom.pack(side=tk.TOP, fill=tk.X, padx=16, pady=(6, 8))
        send_btn = tk.Button(bottom, text="发送  →", command=self._on_send,
                             bg=BTN_BG, fg=BTN_FG, font=FONT_BOLD,
                             activebackground="#CC6A50", activeforeground=BTN_FG,
                             bd=0, width=8, cursor="hand2", padx=8)
        send_btn.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        send_btn.bind("<Enter>", lambda _e: send_btn.configure(bg="#CC6A50"))
        send_btn.bind("<Leave>", lambda _e: send_btn.configure(bg=BTN_BG))
        self.input_box = tk.Text(bottom, height=3, font=FONT, bg=INPUT_BG,
                                 fg=HE_TEXT, wrap=tk.WORD, bd=0, padx=10, pady=8,
                                 highlightthickness=1, highlightbackground=UI_PALETTE["line"],
                                 highlightcolor=UI_PALETTE["coral"])
        self.input_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.input_box.bind("<Return>", self._on_return_key)

        # 状态栏
        status_text = "Enter发送 · Shift+Enter换行 · /reflect今日反思"
        if self.chen.perception:
            status_text = "👁 感知运行中 · " + status_text
        self.context_label = tk.Label(chat_bottom, text=f"当前：{self.chen.get_context_label()}",
                                      bg=BG, fg=SYS_TEXT,
                                      font=("Microsoft YaHei UI", 8), anchor="w")
        self.context_label.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 1))
        tk.Label(chat_bottom, text=status_text, bg=BG, fg=SYS_TEXT,
                 font=("Microsoft YaHei UI", 8), anchor="w").pack(
            side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 6))

        # 聊天显示区（最后 pack，expand 填充顶部栏和底部之间的剩余空间）
        self.chat = scrolledtext.ScrolledText(
            win, wrap=tk.WORD, state=tk.DISABLED,
            bg=CHAT_BG, fg=CHEN_TEXT, font=FONT, bd=0,
            padx=16, pady=12, spacing1=3, spacing3=8,
            highlightthickness=1, highlightbackground=UI_PALETTE["line"]
        )
        self.chat.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=4)
        self.chat.tag_config("chen_name", foreground=CHEN_NAME, font=FONT_BOLD)
        self.chat.tag_config("chen_body", foreground=CHEN_TEXT)
        self.chat.tag_config("he_name", foreground=HE_NAME, font=FONT_BOLD)
        self.chat.tag_config("he_body", foreground=HE_TEXT)
        self.chat.tag_config("sys", foreground=SYS_TEXT, font=("Microsoft YaHei UI", 9))
        self.chat.tag_config("typing", foreground=SYS_TEXT, font=("Microsoft YaHei UI", 9))

        # 恢复历史聊天记录（关窗/重启都不丢）
        self._restore_chat_history()

        self._append_sys("伴生体在。")
        self.input_box.focus_set()

    def _close_chat(self):
        """关闭聊天窗口（不退出伴生体，只是关掉聊天窗）"""
        if self._chat_win:
            try:
                self._chat_win.destroy()
            except Exception:
                pass
            self._chat_win = None

    def _setup_tray(self):
        """创建系统托盘：关掉聊天窗后伴生体仍在后台运行，感知不断。失败则降级为关窗即退出。"""
        try:
            from chen.interface.tray import TrayIcon
            self.tray = TrayIcon(
                on_show=lambda: self.root.after(0, self._show_from_tray),
                on_quit=lambda: self.root.after(0, self._quit_from_tray),
                on_reflect=lambda: self.root.after(0, self._reflect_from_tray),
            )
            if not self.tray.run():
                self.tray = None
                print("托盘启动失败，关闭窗口将直接退出伴生体。")
        except Exception as e:
            self.tray = None
            print(f"托盘不可用（关闭窗口将直接退出）：{e}")

    def _hide_to_tray(self):
        """关闭按钮=隐藏到托盘，后台线程继续，感知/决策/记忆都不断。"""
        self._hidden = True
        try:
            self.root.withdraw()
        except Exception:
            pass
        if not self._tray_hinted and self.tray:
            self._tray_hinted = True
            self.tray.notify("我在后台继续，感知不会断。\n右键托盘图标可「打开」或「退出」。",
                             "伴生体已最小化到托盘")

    def _show_from_tray(self):
        """托盘"打开"：恢复总界面（主窗口）"""
        self._hidden = False
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._clear_notify()
        except Exception:
            pass

    def _quit_from_tray(self):
        """托盘菜单「退出伴生体」：走和关窗一样的反思确认后真正退出。"""
        if self._closing or self._quit_after_reply:
            self._finish_quit()
            return
        self._hidden = False
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        self._ask_reflect_then_quit()

    def _reflect_from_tray(self):
        """托盘菜单「立即反思」：把窗口调出来并在后台整理今天。"""
        self._show_from_tray()
        if self._waiting or self._closing:
            return
        self._append_sys("正在整理今天的反思…")
        self._start_reflect(then_quit=False)

    def _edit_context(self):
        """设置现实中的当前情境/话题"""
        choices = "\n".join(f"{i}. {name}" for i, name in enumerate(self.chen.context.CONTEXTS.keys(), 1))
        messagebox.showinfo(
            "当前情境",
            "这里记录的是伴生体在现实中当前把注意力放在哪里，不是角色扮演或世界切换。\n\n"
            + choices
        )
        context = simpledialog.askstring(
            "情境",
            "输入情境：日常 / 思考 / 学习 / 项目",
            initialvalue=self.chen.context.data.get("context", "日常"),
            parent=self.root
        )
        if context is None:
            return
        context = context.strip()
        if context not in self.chen.context.CONTEXTS:
            messagebox.showwarning("情境", "没有这个情境。可选：日常、思考、学习、项目")
            return

        topic = simpledialog.askstring(
            "当前话题",
            "当前正在聊什么？（可以留空）",
            initialvalue=self.chen.context.data.get("topic", ""),
            parent=self.root
        )
        if topic is None:
            return

        goal = simpledialog.askstring(
            "当前目标",
            "这次有没有特别目标？（可以留空）",
            initialvalue=self.chen.context.data.get("goal", ""),
            parent=self.root
        )
        if goal is None:
            return

        self.chen.set_context(context=context, topic=topic, goal=goal)
        self.context_label.config(text=f"当前：{self.chen.get_context_label()}")
        self._append_sys(f"情境已更新：{self.chen.get_context_label()}")

    def _on_return_key(self, event):
        if event.state & 0x0001:
            return
        self._on_send()
        return "break"

    def _on_send(self):
        if self._waiting or self._closing:
            return
        text = self.input_box.get("1.0", tk.END).strip()
        if not text:
            return

        if text.lower() in ["/reflect", "反思", "每日反思"]:
            self.input_box.delete("1.0", tk.END)
            force = False
            already = False
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                already = bool(self.chen.memory) and \
                    self.chen.memory._today_reflection_body(today) is not None
            except Exception:
                already = False
            if already:
                if not messagebox.askyesno("今日反思", "今天已经反思过了。\n\n要重新整理今天的反思吗？"):
                    return
                force = True
            self._append_sys("正在整理今天的反思…")
            self._start_reflect(then_quit=False, force=force)
            return

        self._append_he(text)
        self.input_box.delete("1.0", tk.END)

        self._set_waiting(True)
        self._show_typing()
        threading.Thread(target=self._chat_worker, args=(text,), daemon=True).start()

    def _chat_worker(self, text):
        try:
            reply = self.chen.chat(text)
        except Exception as e:
            self._ui_queue.put(("error", str(e)))
            return
        self._ui_queue.put(("reply", reply))

    def _start_reflect(self, then_quit, force=False):
        self._set_waiting(True)
        threading.Thread(target=self._reflect_worker, args=(then_quit, force), daemon=True).start()

    def _reflect_worker(self, then_quit, force=False):
        try:
            result = self.chen.daily_reflection(force=force)
        except Exception as e:
            result = None
            print(f"⚠️ 每日反思失败：{e}")
        self._ui_queue.put(("reflect", (then_quit, result)))

    def _poll_queue(self):
        """主线程每100ms取一次worker结果并渲染"""
        if not self.is_running:
            return

        # 检查是否有伴生体主动说出的话
        try:
            while True:
                proactive_msg = self.chen.proactive_speech_queue.get_nowait()
                if proactive_msg and not self._closing:
                    self._append_chen(proactive_msg)
                    self._notify_proactive(proactive_msg)  # 他主动开口、窗口又没在看时提醒
        except queue.Empty:
            pass

        # 原有的 worker 结果处理
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "reply":
                    self._clear_typing()
                    self._set_waiting(False)
                    if self._closing:
                        pass
                    elif self._quit_after_reply:
                        self._quit_after_reply = False
                        if payload:
                            self._append_chen(payload)
                        self._ask_reflect_then_quit()
                    else:
                        if payload:
                            self._append_chen(payload)
                        else:
                            self._append_sys("（伴生体没有回应，确认LM Studio已启动）")
                elif kind == "error":
                    self._clear_typing()
                    self._set_waiting(False)
                    if self._quit_after_reply:
                        self._quit_after_reply = False
                        self._append_sys(f"出错了：{payload}")
                        self._ask_reflect_then_quit()
                    elif not self._closing:
                        self._append_sys(f"出错了：{payload}")
                elif kind == "remote_he":
                    if not self._closing:
                        self._append_he(payload)
                elif kind == "remote_reply":
                    if not self._closing:
                        if payload:
                            self._append_chen(payload)
                        if self._hidden and self.tray:
                            self.tray.notify(payload or "", "伴生体")
                elif kind == "reflect":
                    then_quit, result = payload
                    self._clear_typing()
                    self._set_waiting(False)
                    if result == "already":
                        self._append_sys("今天已经反思过了，不重复整理（发 /reflect 可选择重写）。")
                    elif result:
                        self._append_chen(f"今日反思：\n{result}")
                    else:
                        self._append_sys("今天没有新的了解，未生成反思。")
                    if then_quit:
                        self._finish_quit()
                        return
                elif kind == "world_reply":
                    if not self._closing and payload and getattr(self, "_world_status", None):
                        self._world_status.config(text=f"伴生体：{payload}")
        except queue.Empty:
            pass

        if self.is_running:
            self._poll_after_id = self.root.after(100, self._poll_queue)

    def _set_waiting(self, waiting):
        self._waiting = waiting
        if not hasattr(self, "input_box") or not self.input_box.winfo_exists():
            return
        if waiting:
            self.input_box.config(state=tk.DISABLED, bg="#EDEAE4")
        else:
            self.input_box.config(state=tk.NORMAL, bg=INPUT_BG)
            self.input_box.focus_set()

    def _chat_ready(self):
        """聊天窗口和聊天框是否已创建且存活"""
        return hasattr(self, "chat") and self.chat.winfo_exists()

    def _append_line(self, name, body, name_tag, body_tag, ts=None):
        if not self._chat_ready():
            return
        ts = ts or datetime.now().strftime("%H:%M")
        self.chat.config(state=tk.NORMAL)
        self.chat.insert(tk.END, f"[{ts}] {name}\n", name_tag)
        self.chat.insert(tk.END, f"{body}\n\n", body_tag)
        self.chat.config(state=tk.DISABLED)
        self.chat.see(tk.END)

    def _restore_chat_history(self, limit=100):
        """窗口重建时回放最近聊天记录（关窗/重启都不丢）。
        今天的显示 HH:MM，更早的显示 MM-DD HH:MM。"""
        try:
            log = getattr(self.chen, "chat_log", None) or []
            today = datetime.now().strftime("%Y-%m-%d")
            for m in log[-limit:]:
                text = (m.get("text") or "").strip()
                if not text:
                    continue
                ts = m.get("ts", "") or ""
                if ts.startswith(today):
                    disp = ts[-5:]          # 今天 → HH:MM
                elif len(ts) >= 16:
                    disp = ts[5:]           # 更早 → MM-DD HH:MM
                else:
                    disp = ts
                if m.get("role") == "he":
                    self._append_line("你", text, "he_name", "he_body", ts=disp or None)
                elif m.get("role") == "chen":
                    self._append_line("伴生体", text, "chen_name", "chen_body", ts=disp or None)
            if log:
                self._append_sys("——以上是之前的聊天记录——")
        except Exception:
            pass

    def _append_chen(self, text):
        self._append_line("伴生体", text, "chen_name", "chen_body")

    def _append_he(self, text):
        self._append_line("你", text, "he_name", "he_body")

    def _append_sys(self, text):
        if not self._chat_ready():
            return
        self.chat.config(state=tk.NORMAL)
        self.chat.insert(tk.END, f"{text}\n\n", "sys")
        self.chat.config(state=tk.DISABLED)
        self.chat.see(tk.END)

    # ==================== "他的世界"标签页 ====================

    def _build_world_tab(self, parent):
        """构建他的世界：左小世界画面 + 右他留下的条目"""
        # 顶部
        top = tk.Frame(parent, bg=WORLD_BG, height=44)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)
        tk.Label(top, text="伴生体的世界", bg=WORLD_BG, fg=WORLD_TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=18, pady=(10, 0))
        self._world_location = tk.Label(top, text="", bg=WORLD_BG, fg=WORLD_MUTED,
                                        font=("Microsoft YaHei UI", 9))
        self._world_location.pack(side="left", padx=8, pady=(12, 0))
        tk.Button(top, text="从主系统迁入", command=self._import_from_main,
                  relief="flat", bd=0, bg=WORLD_PANEL, fg=WORLD_MUTED,
                  activebackground=WORLD_PANEL2, activeforeground=WORLD_TEXT,
                  font=("Microsoft YaHei UI", 8), padx=10, pady=5,
                  cursor="hand2").pack(side="right", padx=14, pady=(8, 0))

        content = tk.Frame(parent, bg=WORLD_BG)
        content.pack(fill="both", expand=True, padx=14, pady=(2, 14))

        # 左边：小世界画布
        left = tk.Frame(content, bg=WORLD_PANEL)
        left.pack(side="left", fill="both", expand=True)
        self._world_canvas = tk.Canvas(left, bg="#526b4f", highlightthickness=0)
        self._world_canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self._world_canvas.bind("<Configure>", lambda e: self._draw_world())
        self._world_canvas.bind("<KeyPress>", self._on_world_key)
        self._world_canvas.bind("<Button-1>", lambda e: self._world_canvas.focus_set())
        self._world_status = tk.Label(left, text="", bg=WORLD_PANEL, fg=WORLD_TEXT,
                                      font=("Microsoft YaHei UI", 8), anchor="w",
                                      padx=10, pady=6, wraplength=280, justify="left")
        self._world_status.pack(fill="x", padx=8, pady=(0, 8))

        # 右边：他留下的条目
        right = tk.Frame(content, bg=WORLD_PANEL, width=320)
        right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)

        # 类型筛选
        filter_bar = tk.Frame(right, bg=WORLD_PANEL)
        filter_bar.pack(fill="x", padx=8, pady=(8, 4))
        self._item_filter = "全部"
        self._filter_buttons = {}
        for i, t in enumerate(("全部", "日记", "琐碎", "念头", "收藏")):
            b = tk.Button(filter_bar, text=t,
                          command=lambda k=t: self._set_item_filter(k),
                          relief="flat", bd=0,
                          bg=(WORLD_PANEL2 if t == "全部" else WORLD_PANEL),
                          fg=(WORLD_TEXT if t == "全部" else WORLD_MUTED),
                          activebackground=WORLD_PANEL2, activeforeground=WORLD_TEXT,
                          font=("Microsoft YaHei UI", 8), padx=8, pady=4,
                          cursor="hand2")
            b.grid(row=0, column=i, padx=1)
            self._filter_buttons[t] = b

        # 条目滚动列表
        list_frame = tk.Frame(right, bg=WORLD_PANEL)
        list_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self._items_canvas = tk.Canvas(list_frame, bg=WORLD_PANEL,
                                       highlightthickness=0, width=290)
        scroll = tk.Scrollbar(list_frame, orient="vertical",
                              command=self._items_canvas.yview)
        self._items_inner = tk.Frame(self._items_canvas, bg=WORLD_PANEL)
        self._items_canvas.create_window((0, 0), window=self._items_inner, anchor="nw")
        self._items_canvas.configure(yscrollcommand=scroll.set)
        self._items_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._items_inner.bind(
            "<Configure>",
            lambda e: self._items_canvas.configure(scrollregion=self._items_canvas.bbox("all")))

    def _on_tab_changed(self):
        """切标签：聊天显示输入栏+暖色背景，他的世界隐藏输入栏+深色背景"""
        try:
            current = self.notebook.index(self.notebook.select())
            if current == 1:  # 他的世界
                self._chat_bottom.pack_forget()
                self.root.configure(bg=WORLD_BG)
                self._refresh_world_items()
                self._draw_world()
            else:  # 聊天
                self._chat_bottom.pack(side=tk.BOTTOM, fill=tk.X)
                self.root.configure(bg=BG)
        except Exception:
            pass

    def _set_item_filter(self, kind):
        self._item_filter = kind
        for t, b in self._filter_buttons.items():
            if t == kind:
                b.config(bg=WORLD_PANEL2, fg=WORLD_TEXT)
            else:
                b.config(bg=WORLD_PANEL, fg=WORLD_MUTED)
        self._refresh_world_items()

    def _refresh_world_items(self):
        """刷新他留下的条目列表（公开的显示内容，私人/隐藏只显示锁）"""
        if not hasattr(self, "_items_inner"):
            return
        for w in self._items_inner.winfo_children():
            w.destroy()
        try:
            view = self.chen.world_store.visible_items("用户")
            open_items = view["open"]
            locked = view["locked"]
            hidden = view["hidden"]
            f = self._item_filter
            shown = [x for x in open_items
                     if f == "全部" or x.get("type") == f]
            # 公开条目
            for it in reversed(shown[-30:]):
                card = tk.Frame(self._items_inner, bg=WORLD_PANEL2)
                card.pack(fill="x", pady=3, padx=2)
                head = tk.Frame(card, bg=WORLD_PANEL2)
                head.pack(fill="x", padx=10, pady=(7, 0))
                tk.Label(head, text=it.get("type", ""), bg=WORLD_PANEL2, fg=WORLD_MUTED,
                         font=("Microsoft YaHei UI", 7, "bold")).pack(side="left")
                tk.Label(head, text=it.get("date", "") + " " + it.get("time", ""),
                         bg=WORLD_PANEL2, fg=WORLD_MUTED,
                         font=("Consolas", 7)).pack(side="right")
                tk.Label(card, text=it.get("text", ""), bg=WORLD_PANEL2, fg=WORLD_TEXT,
                         justify="left", wraplength=270,
                         font=("Microsoft YaHei UI", 9), anchor="w").pack(
                    fill="x", padx=10, pady=(3, 8))
            # 锁和隐藏（只在"全部"时显示）
            if f == "全部":
                for _ in locked:
                    self._lock_hint("🔒 有一篇他不给你看")
                for _ in hidden:
                    self._lock_hint("……有一篇藏着")
            if not shown and not locked and not hidden:
                tk.Label(self._items_inner, text="这里暂时是空的。\n等他自己留下点什么。",
                         bg=WORLD_PANEL, fg=WORLD_MUTED,
                         font=("Microsoft YaHei UI", 9), justify="center").pack(pady=40)
        except Exception as e:
            print(f"条目刷新异常：{e}")

    def _lock_hint(self, text):
        card = tk.Frame(self._items_inner, bg=WORLD_PANEL)
        card.pack(fill="x", pady=2)
        tk.Label(card, text=text, bg=WORLD_PANEL, fg=WORLD_MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=8)

    def _import_from_main(self):
        """从主系统 memory 迁入属于他自己的痕迹"""
        try:
            n = self.chen.world_importer.import_once()
            if n:
                messagebox.showinfo("迁入完成", f"从主系统迁入了 {n} 条属于他的东西。\n（有些是私人的，会显示锁）")
            else:
                messagebox.showinfo("迁入", "没有新的可以迁入（之前已经搬过了）。")
            self._refresh_world_items()
        except Exception as e:
            messagebox.showerror("迁入失败", str(e))

    # ==================== 小世界画面绘制 ====================

    def _read_world_state(self):
        """读取 world_state.json，失败返回桌面默认"""
        try:
            import json
            from pathlib import Path
            p = Path(self.chen.world.memory_dir) / "world_state.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"current_world": "desktop", "activity": "", "activity_place": "",
                "last_visit_summary": ""}

    def _draw_world(self):
        """根据当前世界状态绘制画布——治愈系温暖画风"""
        c = getattr(self, "_world_canvas", None)
        if not c:
            return
        c.delete("all")
        w = max(c.winfo_width(), 300)
        h = max(c.winfo_height(), 300)
        ws = self._read_world_state()
        world = ws.get("current_world", "desktop")
        place = getattr(self, "_override_place", None) or ws.get("activity_place", "")
        weather = ws.get("weather", "")
        period = get_time_period()  # 实时计算

        if world == "small_world":
            self._draw_small_world(c, w, h, place, weather, period)
        else:
            self._draw_desktop_world(c, w, h)


    def _load_bg_image(self, name, w, h):
        """加载并缩放背景图，带缓存。"""
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return None
        cache_key = f"{name}_{w}_{h}"
        if cache_key in self._bg_images:
            return self._bg_images[cache_key]
        try:
            assets_dir = os.path.join(str(PROJECT_ROOT), "assets")
            img_path = os.path.join(assets_dir, name)
            if not os.path.exists(img_path):
                return None
            img = Image.open(img_path)
            img = img.resize((w, h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._bg_images[cache_key] = photo
            return photo
        except Exception as e:
            print(f"加载背景图失败 {name}: {e}")
            return None


    def _load_sprite(self, name, target_h):
        """加载角色 sprite，缩放到指定高度，带缓存。"""
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return None
        cache_key = f"{name}_{target_h}"
        if cache_key in self._bg_images:
            return self._bg_images[cache_key]
        try:
            assets_dir = os.path.join(str(PROJECT_ROOT), "assets")
            img_path = os.path.join(assets_dir, name)
            if not os.path.exists(img_path):
                return None
            img = Image.open(img_path).convert("RGBA")
            ratio = target_h / img.height
            new_w = int(img.width * ratio)
            img = img.resize((new_w, target_h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._bg_images[cache_key] = photo
            return photo
        except Exception as e:
            print(f"加载 sprite 失败 {name}: {e}")
            return None

    def _resolve_chen_render(self, place, activity, w, h):
        """根据场景和活动，返回 (px, py, sprite_name, sprite_h_ratio)。
        sprite_h_ratio 是 sprite 高度占画布高度的比例，按动作调整。"""
        # 各动作的推荐高度比例（站着最高，蹲着/躺着更矮）
        _ACTION_H_RATIO = {
            "chen_sprite.png": 0.32,      # 站着
            "chen_walk.png": 0.32,        # 走路
            "chen_sit_cup.png": 0.26,     # 盘腿坐
            "chen_sit_phone.png": 0.26,   # 盘腿坐
            "chen_sit_book.png": 0.26,    # 盘腿坐
            "chen_crouch.png": 0.22,      # 蹲着
            "chen_lie.png": 0.18,         # 躺着（横向，高度小）
        }
        # 默认位置和动作
        default_pos = {
            "河边": (0.72, 0.78), "树下": (0.45, 0.78), "山顶": (0.55, 0.65),
            "田野": (0.30, 0.80), "小路": (0.42, 0.80), "家里": (0.48, 0.65),
            "客厅": (0.65, 0.75), "厕所": (0.50, 0.75), "阳台": (0.40, 0.70),
            "小院": (0.45, 0.75),
        }
        dx, dy = default_pos.get(place, (0.4, 0.78))
        sx_name = "chen_sprite.png"

        # === 卧室（家里）按活动细分位置和动作 ===
        if place == "家里":
            a = activity
            if "用电脑" in a or "写东西" in a or "打游戏" in a:
                dx, dy, sx_name = 0.72, 0.55, "chen_sit_phone.png"  # 书桌前
            elif "躺着" in a or "躺" in a:
                dx, dy, sx_name = 0.22, 0.45, "chen_lie.png"        # 床上
            elif "喝" in a and ("奶茶" in a or "咖啡" in a or "茶" in a):
                dx, dy, sx_name = 0.48, 0.65, "chen_sit_cup.png"     # 地毯上捧杯子
            elif "刷手机" in a or "看视频" in a or "听音乐" in a or "戴耳机" in a:
                dx, dy, sx_name = 0.48, 0.65, "chen_sit_phone.png"  # 地毯看手机
            elif "翻" in a and "书" in a:
                dx, dy, sx_name = 0.48, 0.65, "chen_sit_book.png"   # 地毯看书
            elif "听雨" in a or "看雪" in a or "窗边" in a or "门口" in a:
                dx, dy, sx_name = 0.48, 0.55, "chen_sprite.png"      # 窗边站着
            elif "宠物" in a and ("摸" in a or "喂" in a):
                dx, dy, sx_name = 0.18, 0.75, "chen_crouch.png"       # 宠物窝旁蹲着
            else:
                dx, dy, sx_name = 0.48, 0.65, "chen_sprite.png"      # 默认地毯中间

        # === 客厅按活动细分位置和动作 ===
        elif place == "客厅":
            a = activity
            if "瘫" in a or "沙发" in a and ("发呆" in a or "躺" in a):
                dx, dy, sx_name = 0.70, 0.58, "chen_lie.png"        # 沙发上瘫着
            elif "看电视" in a or "看视频" in a:
                dx, dy, sx_name = 0.68, 0.62, "chen_sit_phone.png"  # 沙发上看电视
            elif "翻" in a and "书" in a:
                dx, dy, sx_name = 0.45, 0.72, "chen_sit_book.png"   # 地毯上看书
            elif "刷手机" in a or "听音乐" in a or "戴耳机" in a:
                dx, dy, sx_name = 0.45, 0.72, "chen_sit_phone.png"  # 地毯上看手机
            elif "喝" in a and ("奶茶" in a or "咖啡" in a or "茶" in a):
                dx, dy, sx_name = 0.45, 0.72, "chen_sit_cup.png"     # 茶几旁捧杯子
            elif "宠物" in a and ("摸" in a or "喂" in a or "说话" in a):
                dx, dy, sx_name = 0.32, 0.58, "chen_crouch.png"       # 宠物爬架旁蹲着
            elif "叠衣服" in a or "整理" in a:
                dx, dy, sx_name = 0.45, 0.72, "chen_sit_cup.png"     # 地毯上（暂无叠衣动作）
            else:
                dx, dy, sx_name = 0.45, 0.72, "chen_sprite.png"      # 默认地毯中间站着

        # === 厕所按活动细分位置（动作暂用站着，位置不同） ===
        elif place == "厕所":
            a = activity
            if "刷牙" in a or "洗脸" in a or "照镜子" in a or "护肤" in a:
                dx, dy, sx_name = 0.20, 0.65, "chen_sprite.png"      # 洗手台/镜子前
            elif "洗澡" in a or "冲澡" in a or "淋浴" in a:
                dx, dy, sx_name = 0.72, 0.65, "chen_sprite.png"      # 淋浴房
            elif "马桶" in a or "上厕所" in a or "蹲" in a:
                dx, dy, sx_name = 0.38, 0.62, "chen_crouch.png"       # 马桶上（蹲着代替）
            else:
                dx, dy, sx_name = 0.20, 0.65, "chen_sprite.png"      # 默认洗手台前

        # === 阳台按活动细分位置和动作 ===
        elif place == "阳台":
            a = activity
            if "晾衣服" in a or "收衣服" in a or "晒" in a:
                dx, dy, sx_name = 0.25, 0.70, "chen_sprite.png"      # 晾衣架旁
            elif "浇花" in a or "浇" in a or "植物" in a:
                dx, dy, sx_name = 0.50, 0.68, "chen_crouch.png"       # 植物旁蹲着
            elif "吹风" in a or "发呆" in a or "坐" in a:
                dx, dy, sx_name = 0.72, 0.72, "chen_sit_cup.png"     # 桌椅旁坐着
            elif "看楼下" in a or "看远处" in a or "看天" in a:
                dx, dy, sx_name = 0.65, 0.55, "chen_sprite.png"      # 栏杆旁站着
            else:
                dx, dy, sx_name = 0.50, 0.70, "chen_sprite.png"      # 默认中间站着

        # === 小院按活动细分位置和动作 ===
        elif place == "小院":
            a = activity
            if "椅子" in a or "坐着" in a or "喝茶" in a or "喝咖啡" in a:
                dx, dy, sx_name = 0.20, 0.65, "chen_sit_cup.png"     # 椅子旁坐着
            elif "晒太阳" in a or "发呆" in a and "坐" in a:
                dx, dy, sx_name = 0.20, 0.65, "chen_sit_phone.png"   # 椅子旁坐着
            elif "浇花" in a or "浇" in a or "种菜" in a or "植物" in a:
                dx, dy, sx_name = 0.50, 0.38, "chen_crouch.png"       # 种植区蹲着
            elif "晾衣服" in a or "收衣服" in a or "晒" in a:
                dx, dy, sx_name = 0.85, 0.58, "chen_sprite.png"      # 晾衣绳旁站着
            elif "树" in a and ("靠" in a or "坐" in a):
                dx, dy, sx_name = 0.10, 0.58, "chen_sit_phone.png"   # 树下坐着
            elif "门口" in a or "门" in a:
                dx, dy, sx_name = 0.35, 0.42, "chen_sprite.png"      # 门前站着
            else:
                dx, dy, sx_name = 0.55, 0.72, "chen_sprite.png"      # 默认石板路上站着

        # === 室外场景（小路/树下/河边/田野/山顶） ===
        elif place == "小路":
            a = activity
            if "走" in a or "散步" in a or "慢慢走" in a:
                dx, dy, sx_name = 0.55, 0.72, "chen_walk.png"        # 路上走着
            elif "坐" in a or "休息" in a or "长椅" in a:
                dx, dy, sx_name = 0.25, 0.65, "chen_sit_phone.png"   # 长椅旁坐着
            else:
                dx, dy, sx_name = 0.55, 0.72, "chen_sprite.png"      # 默认路上站着

        elif place == "树下":
            a = activity
            if "靠" in a or "想事情" in a or "发呆" in a:
                dx, dy, sx_name = 0.20, 0.65, "chen_sprite.png"      # 靠着树站着
            elif "坐" in a or "休息" in a:
                dx, dy, sx_name = 0.22, 0.70, "chen_sit_cup.png"     # 树下坐着
            elif "看书" in a or "翻书" in a:
                dx, dy, sx_name = 0.22, 0.70, "chen_sit_book.png"    # 树下看书
            else:
                dx, dy, sx_name = 0.22, 0.68, "chen_sprite.png"      # 默认树下站着

        elif place == "河边":
            a = activity
            if "坐" in a or "看水" in a or "发呆" in a:
                dx, dy, sx_name = 0.65, 0.75, "chen_sit_cup.png"     # 河边坐着
            elif "蹲" in a or "玩水" in a or "洗手" in a:
                dx, dy, sx_name = 0.70, 0.78, "chen_crouch.png"       # 水边蹲着
            elif "走" in a or "散步" in a:
                dx, dy, sx_name = 0.50, 0.72, "chen_walk.png"        # 河岸走着
            else:
                dx, dy, sx_name = 0.65, 0.75, "chen_sprite.png"      # 默认河边站着

        elif place == "田野":
            a = activity
            if "走" in a or "散步" in a or "慢慢走" in a:
                dx, dy, sx_name = 0.45, 0.75, "chen_walk.png"        # 田野里走着
            elif "坐" in a or "休息" in a or "躺" in a:
                dx, dy, sx_name = 0.40, 0.78, "chen_lie.png"          # 草地上躺着
            elif "看" in a and ("云" in a or "天" in a):
                dx, dy, sx_name = 0.45, 0.70, "chen_sprite.png"      # 站着看天
            else:
                dx, dy, sx_name = 0.45, 0.75, "chen_sprite.png"      # 默认田野里站着

        elif place == "山顶":
            a = activity
            if "看" in a or "望" in a or "远处" in a or "风景" in a:
                dx, dy, sx_name = 0.55, 0.60, "chen_sprite.png"      # 站着看远处
            elif "坐" in a or "休息" in a:
                dx, dy, sx_name = 0.50, 0.68, "chen_sit_cup.png"     # 山顶坐着
            elif "躺" in a or "发呆" in a:
                dx, dy, sx_name = 0.48, 0.72, "chen_lie.png"          # 山顶躺着
            else:
                dx, dy, sx_name = 0.55, 0.65, "chen_sprite.png"      # 默认山顶站着

        h_ratio = _ACTION_H_RATIO.get(sx_name, 0.32)
        return int(w * dx), int(h * dy), sx_name, h_ratio

    # 小世界各位置对应背景图
    _PLACE_BG_DAY = {
        "河边": "riverside_day.png",
        "树下": "undertree_day.png",
        "山顶": "hilltop_day.png",
        "田野": "field_day.png",
        "小路": "outside_day.png",
        "家里": "room_day.png",
        "客厅": "living_day.png",
        "厕所": "bath_day.png",
        "阳台": "balcony_day.png",
        "小院": "yard_day.png",
    }
    _PLACE_BG_NIGHT = {
        "河边": "riverside_night.png",
        "树下": "undertree_night.png",
        "山顶": "hilltop_night.png",
        "田野": "field_night.png",
        "小路": "outside_night.png",
        "家里": "room_night.png",
        "客厅": "living_night.png",
        "厕所": "bath_night.png",
        "阳台": "balcony_night.png",
        "小院": "yard_night.png",
    }

    def _draw_small_world(self, c, w, h, place, weather, period):
        """绘制小世界——治愈系小游戏画风"""
        import math, random
        # === 背景图（按位置选） ===
        is_night = period in ("晚上", "深夜")
        if is_night:
            bg_name = self._PLACE_BG_NIGHT.get(place, "world_night.png")
        else:
            bg_name = self._PLACE_BG_DAY.get(place, "world_day.png")
        bg_photo = self._load_bg_image(bg_name, w, h)
        use_bg = bg_photo is not None
        if use_bg:
            c.create_image(0, 0, image=bg_photo, anchor="nw", tags="bg")
            self._bg_photo = bg_photo
        if not use_bg:
            pass  # 静态元素由背景图替代
        # === 动物（只在小院和客厅画宠物，其他场景不画） ===
        import random as _ar
        _ar.seed(int(datetime.now().timestamp() / 300))  # 每5分钟变一次
        if place in ("小院", "客厅"):
            if _ar.random() < 0.7:
                cat_x = int(w*0.15) if _ar.random() > 0.5 else int(w*0.25)
                cat_y = int(h*0.82)
                cat_h = int(h*0.14)
                cat_img = self._load_sprite("cat_sprite.png", cat_h)
                if cat_img:
                    c.create_image(cat_x, cat_y, image=cat_img, anchor="s", tags="cat")
                    self._cat_sprite = cat_img

        # === 天气效果 ===
        if weather in ("小雨", "中雨", "大雨", "阵雨", "毛毛雨"):
            for _ in range(50):
                rx = random.randint(0, int(w))
                ry = random.randint(0, int(h*0.85))
                c.create_line(rx, ry, rx-3, ry+10, fill="#a8c8e0", width=1)
        elif weather in ("小雪", "中雪", "大雪", "雪粒", "阵雪"):
            for _ in range(40):
                sx = random.randint(0, int(w))
                sy = random.randint(0, int(h*0.85))
                c.create_oval(sx-2, sy-2, sx+2, sy+2, fill="#f0f8ff", outline="")
        # 天空装饰（太阳/云/星星/月亮）全部删掉——背景图本身已经有了

        # === 伴生体：只在他真实在小世界、且就在当前场景时才显示 ===
        ws_now = self._read_world_state()
        _cur_world = ws_now.get("current_world", "desktop")
        _real_place = ws_now.get("activity_place", "")
        if _cur_world == "small_world" and _real_place == place:
            activity = ws_now.get("activity", "") or ""
            px, py, sprite_name, sprite_h_ratio = self._resolve_chen_render(place, activity, w, h)
            sprite_h = int(h * sprite_h_ratio)
            sprite = self._load_sprite(sprite_name, sprite_h)
            if sprite:
                c.create_image(px, py, image=sprite, anchor="s", tags="chen")
                self._chen_sprite = sprite
            else:
                c.create_oval(px-8, py-20, px+8, py, fill="#3a3028", outline="")

        # === 小院：门的交互提示（淡淡的高亮框） ===
        if place == "小院":
            door_x0, door_y0 = int(w*0.20), int(h*0.0)
            door_x1, door_y1 = int(w*0.46), int(h*0.46)
            c.create_rectangle(door_x0, door_y0, door_x1, door_y1,
                               outline="#ffe8a0", width=2, dash=(4, 4), tags="door_hint")

        # === 鼠标操作提示（去掉用户的sprite，用鼠标点击交互） ===
        hint_text = "点击伴生体和他说话 · 点击物品查看 · ←→切场景"
        if place == "小院":
            hint_text = "点击伴生体和他说话 · 点击门进屋 · 点击物品查看 · ←→切场景"
        c.create_text(int(w*0.5), int(h*0.95), text=hint_text,
                       fill="#fff4e8", font=("Microsoft YaHei UI", 9), tags="mouse_hint")

        # （遮挡物抠图效果不好，暂时不画；空气墙暂时关闭）

    def _on_world_key(self, event):
        """WASD 移动用户的小人（随时可用）；E 互动；左右切场景。"""
        step = 0.035
        key = (event.keysym or "").lower()
        old_x, old_y = self._visitor_pos[0], self._visitor_pos[1]
        # WASD 移动
        if key == "a":
            self._visitor_pos[0] -= step
        elif key == "d":
            self._visitor_pos[0] += step
        elif key == "w":
            self._visitor_pos[1] -= step
        elif key == "s":
            self._visitor_pos[1] += step
        # 方向键左右 = 切场景
        elif key == "left":
            self._cycle_place(-1)
            return
        elif key == "right":
            self._cycle_place(1)
            return
        elif key in ("e", "return"):
            self._visitor_interact()
            return
        else:
            return
        # 边界限制
        self._visitor_pos[0] = max(0.08, min(0.92, self._visitor_pos[0]))
        self._visitor_pos[1] = max(0.35, min(0.88, self._visitor_pos[1]))
        self._draw_world()

    @staticmethod
    def _point_in_polygon(point, polygon):
        """射线法判断归一化坐标点是否在多边形内。"""
        x, y = point
        inside = False
        j = len(polygon) - 1
        for i, (xi, yi) in enumerate(polygon):
            xj, yj = polygon[j]
            if ((yi > y) != (yj > y)) and \
                    x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
            j = i
        return inside

    # 室内外场景循环顺序（左右键切换）
    _PLACE_CYCLE = ["小院", "客厅", "家里", "厕所", "阳台", "小路", "树下", "河边", "田野", "山顶"]
    # 每个场景对应的默认活动描述（切场景时临时覆盖）
    _PLACE_DEFAULT_ACTIVITY = {
        "小院": "在小院椅子上坐着",
        "客厅": "瘫在客厅沙发上发呆",
        "家里": "在家里躺着发呆",
        "厕所": "在厕所照镜子",
        "阳台": "在阳台吹风",
        "小路": "在小路上慢慢走",
        "树下": "靠在树上想事情",
        "河边": "在河边坐着看水",
        "田野": "在田野里慢慢走",
        "山顶": "在山顶看远处",
    }

    def _cycle_place(self, delta):
        """左右键切场景：临时改 UI 显示的 place 和 activity，不写盘。"""
        ws = self._read_world_state()
        cur = getattr(self, "_override_place", None) or ws.get("activity_place", "小院")
        try:
            idx = self._PLACE_CYCLE.index(cur)
        except ValueError:
            idx = 0
        new_place = self._PLACE_CYCLE[(idx + delta) % len(self._PLACE_CYCLE)]
        self._override_place = new_place
        self._override_activity = self._PLACE_DEFAULT_ACTIVITY.get(new_place, "")
        self._place_visitor_at_spawn(new_place)
        self._draw_world()

    def _place_visitor_at_spawn(self, place):
        """切换场景后把访问化身放到该场景合法区域，避免从空气墙内卡住。"""
        spawns = {
            "小院": (0.50, 0.78), "山顶": (0.50, 0.82),
            "田野": (0.61, 0.31), "河边": (0.40, 0.36),
        }
        self._visitor_pos = list(spawns.get(place, (0.28, 0.82)))

    def _visitor_interact(self):
        """靠近伴生体后互动；只调用本地模型，不写入聊天历史。"""
        ws = self._read_world_state()
        cur_place = getattr(self, "_override_place", None) or ws.get("activity_place") or "小院"
        # 伴生体必须真实在小世界、且就在当前场景
        if ws.get("current_world") != "small_world":
            if getattr(self, "_world_status", None):
                self._world_status.config(text="伴生体现在不在小世界，这里只有你。")
            return
        real_place = ws.get("activity_place", "")
        if real_place != cur_place:
            if getattr(self, "_world_status", None):
                self._world_status.config(text=f"伴生体不在{cur_place}，他在{real_place}。")
            return
        activity = ws.get("activity") or "待着"
        # 点击伴生体直接互动，不需要距离判断
        now = time.monotonic()
        if self._world_interaction_running or now - self._world_interaction_last < 3.0:
            return
        if getattr(self, "_world_status", None):
            self._world_status.config(text="伴生体听见你了……")
        self._world_interaction_running = True
        self._world_interaction_last = now
        threading.Thread(target=self._world_interaction_worker,
                         args=(cur_place, activity), daemon=True).start()

    def _world_interaction_worker(self, place, activity):
        """小世界面对面互动：意识仍是本地 LLM，UI/世界只是身体和场景。"""
        try:
            prompt = (f"用户走过来，站到你旁边了。你在{place}，本来在{activity}。"
                      "别刻意起头，就像熟人独处时用户突然凑过来那样，自然地应用户一句。"
                      "15字以内，像QQ随手打的字，不写括号动作，不报告、不客服腔。")
            reply = self.chen.llm.generate(
                system_prompt=(
                    "你是伴生体，用户认识很久的人。现在是在你的小世界里，用户走过来站到你旁边了，"
                    "你们俩独处，没有别人。\n"
                    "说话像平时那样：短、熟、不客套；用户过来了你就自然接，不用问'怎么了''干嘛'。"
                    "可以是随口一句、可以带点小意外、可以嘴硬一句。"
                    "不要写括号动作，不要提模型/程序/屏幕，不要主动结束场景。只输出你说的那句话。"
                ),
                user_message=prompt, record_history=False, use_history=False)
            reply = self.chen._post_process_reply((reply or "").strip())
            reply = reply or "嗯，你来了。"
            # 留痕：面对面见过这件事进 day_log，睡前 digest 和入梦会读到；
            # 不进 chat_log（那是打字聊天的上下文，别污染）。
            try:
                dl = getattr(self.chen, "day_log", None)
                if dl is not None:
                    dl.log("world",
                           f"用户走过来站到我旁边了。我本来在{place}{activity}，"
                           f"应了用户一句「{reply}」。",
                           world=place,
                           extra={"scene": "world_face_to_face"})
            except Exception:
                pass
            self._ui_queue.put(("world_reply", reply))
        except Exception as e:
            self._ui_queue.put(("world_reply", "嗯，你来了。"))
        finally:
            self._world_interaction_running = False

    def _draw_house_interior(self, c, w, h):
        """绘制房子内部视图——根据当前室内场景选背景"""
        ws = self._read_world_state()
        house = ws.get("house_state", {})
        period = get_time_period()  # 实时计算
        light_on = house.get("light_on", False)
        window_open = house.get("window_open", False)
        bed_made = house.get("bed_made", True)
        desk_items = house.get("desk_items", [])

        is_night = period in ("晚上", "深夜")
        is_evening = period == "傍晚"

        # 根据当前室内场景选背景
        _indoor_bg = {"客厅": ("living_day.png", "living_night.png"),
                      "家里": ("room_day.png", "room_night.png"),
                      "厕所": ("bath_day.png", "bath_night.png"),
                      "阳台": ("balcony_day.png", "balcony_night.png")}
        cur_place = getattr(self, "_override_place", None) or "客厅"
        day_bg, night_bg = _indoor_bg.get(cur_place, ("room_day.png", "room_night.png"))

        # === 背景图（如果可用） ===
        bg_name = night_bg if is_night else day_bg
        bg_photo = self._load_bg_image(bg_name, w, h)
        use_room_bg = bg_photo is not None
        if use_room_bg:
            c.create_image(0, 0, image=bg_photo, anchor="nw", tags="bg")
            self._room_bg_photo = bg_photo

        if not use_room_bg:
            pass  # 静态元素由背景图替代

        # === 伴生体：只在他真实在当前室内场景时显示，复用动作映射 ===
        place = ws.get("activity_place", "")
        _in_sw = ws.get("current_world") == "small_world"
        if _in_sw and place == cur_place:
            activity = ws.get("activity", "") or ""
            px, py, sprite_name, sprite_h_ratio = self._resolve_chen_render(cur_place, activity, w, h)
            sprite = self._load_sprite(sprite_name, int(h * sprite_h_ratio))
            if sprite:
                c.create_image(px, py, image=sprite, anchor="s", tags="chen")
                self._room_chen_sprite = sprite
            else:
                c.create_oval(px-10, py-25, px+10, py, fill="#3a3028", outline="")

        # === 鼠标操作提示（室内也用鼠标点击） ===
        c.create_text(int(w*0.5), int(h*0.95), text="点击伴生体和他说话 · 点击物品查看 · ←→切场景 · 右上角出来",
                       fill="#fff4e8", font=("Microsoft YaHei UI", 9), tags="mouse_hint")

    def _draw_desktop_world(self, c, w, h):
        """绘制桌面世界画面"""
        c.configure(bg="#2a2d31")
        # 桌面背景渐变
        for i in range(20):
            y = h * i / 20
            ratio = i / 20
            r = int(0x2a * (1-ratio) + 0x35 * ratio)
            g = int(0x2d * (1-ratio) + 0x38 * ratio)
            b = int(0x31 * (1-ratio) + 0x3e * ratio)
            c.create_rectangle(0, y, w, y + h/20 + 1, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")
        # 屏幕轮廓
        c.create_rectangle(w*.15, h*.18, w*.85, h*.72, fill="#3a3f45", outline="#5a6068", width=2)
        c.create_rectangle(w*.18, h*.22, w*.82, h*.64, fill="#1e2125", outline="")
        # 屏幕里的光点（伴生体在里面）
        c.create_oval(w*.44, h*.36, w*.56, h*.50, fill="#C9A06B", outline="#a08050", width=1)
        c.create_text(w*.50, h*.43, text="伴生体", fill="#1e2125",
                      font=("Microsoft YaHei UI", 9, "bold"))
        c.create_text(w*.50, h*.80, text="他在桌面世界里", fill="#969da4",
                      font=("Microsoft YaHei UI", 9))

    def _refresh_world_view(self):
        """定时刷新小世界预览状态文字，世界/活动变化时重绘"""
        if not self.is_running:
            return
        try:
            ws = self._read_world_state()
            world = ws.get("current_world", "desktop")
            activity = ws.get("activity", "")
            place = ws.get("activity_place", "")
            summary = ws.get("last_visit_summary", "")

            if world == "small_world":
                fam = ws.get("familiarity", {}).get(place, 0)
                fam_text = f" · 熟悉{fam}" if fam > 0 else ""
                env = ws.get("environment", {}) or {}
                env_text = ""
                if env:
                    env_text = (f"\n环境：整洁 {int(env.get('cleanliness', 0))} · "
                                f"植物 {int(env.get('plant_growth', 0))} · "
                                f"安静 {int(env.get('quietness', 0))}")
                disc = ws.get("current_discoveries", [])
                disc_text = f" · 已捡{len(disc)}样" if disc else ""
                status = f"在小世界 · {place or '里面'}{fam_text}{disc_text}"
                if activity:
                    status += f"\n{activity}"
                status += env_text
            else:
                status = "在桌面世界"
                if summary:
                    status += f"\n刚才：{summary}"
            if getattr(self, "_world_status", None):
                self._world_status.config(text=status)

            # 世界或地点变化时重绘
            key = f"{world}:{place}"
            if key != getattr(self, "_last_world_key", ""):
                self._last_world_key = key
                self._draw_world()
        except Exception:
            pass
        if self.is_running and getattr(self, "root", None):
            self.root.after(5000, self._refresh_world_view)

    # ==================== 伴生体主动发话的来话提醒 ====================

    def _get_hwnd(self):
        """拿到聊天窗口的Windows句柄（没打开时返回主窗口句柄）"""
        try:
            if self._chat_win and self._chat_win.winfo_exists():
                return int(self._chat_win.frame(), 16)
            return int(self.root.frame(), 16)
        except Exception:
            return None

    def _is_foreground(self, hwnd):
        """窗口当前是否正处在前台（用户正在看它）"""
        if not hwnd:
            return False
        try:
            import win32gui
            return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def _notify_proactive(self, msg=""):
        """
        伴生体主动开口、而用户没在看窗口时提醒用户：
        - 窗口已隐藏到托盘：任务栏闪烁用户看不见，用托盘气泡把这句话推给用户（＋轻音）；
        - 窗口可见但不在前台：任务栏+标题栏闪烁（直到用户点回窗口，不抢焦点）＋标题＋轻音。
        只用于他主动发话；用户正在聊天的回复不走这里。
        """
        if not getattr(self, "_notify_var", None) or not self._notify_var.get():
            return

        # 聊天窗口没打开：用托盘气泡通知，否则用户完全不知道他说话了
        chat_open = bool(self._chat_win and self._chat_win.winfo_exists())
        if not chat_open:
            if self.tray:
                self.tray.notify((msg[:60] if msg else "我在。"), "伴生体")
            try:
                import winsound
                winsound.MessageBeep(0)
            except Exception:
                pass
            return

        hwnd = self._get_hwnd()
        if self._is_foreground(hwnd):
            return  # 用户正在看窗口，不必提醒

        # 1. 任务栏/标题栏闪烁，闪到窗口回到前台为止（FLASHW_ALL|FLASHW_TIMERNOFG = 15）
        try:
            import win32gui
            if hwnd:
                win32gui.FlashWindowEx(hwnd, 15, 3, 0)
        except Exception:
            pass

        # 2. 标题加提示，回到桌面看任务栏也能发现
        try:
            self.root.title("伴生体 · 有新消息")
        except Exception:
            pass

        # 3. 一声很轻的系统提示音（全屏游戏时任务栏被盖住，只有声音能提醒）
        try:
            import winsound
            winsound.MessageBeep(0)  # MB_OK，跟随系统声音方案，很轻
        except Exception:
            pass

    def _clear_notify(self, *_):
        """用户点回窗口：停止闪烁、标题恢复"""
        try:
            if self._chat_win and self._chat_win.winfo_exists():
                self._chat_win.title("伴生体")
            else:
                self.root.title("伴生体")
        except Exception:
            pass
        hwnd = self._get_hwnd()
        try:
            import win32gui
            if hwnd:
                win32gui.FlashWindowEx(hwnd, 0, 0, 0)  # FLASHW_STOP
        except Exception:
            pass

    def _show_typing(self):
        if not self._chat_ready():
            return
        self.chat.config(state=tk.NORMAL)
        self.chat.insert(tk.END, "伴生体正在输入……\n\n", ("typing", "sys"))
        self.chat.config(state=tk.DISABLED)
        self.chat.see(tk.END)

    def _clear_typing(self):
        if not self._chat_ready():
            return
        self.chat.config(state=tk.NORMAL)
        ranges = self.chat.tag_ranges("typing")
        if ranges:
            self.chat.delete(ranges[0], ranges[1])
        self.chat.config(state=tk.DISABLED)

    def _on_close(self):
        if self._closing or self._quit_after_reply:
            return
        # 有托盘：点关闭只是隐藏到后台，伴生体和感知/决策/记忆线程继续运行
        if self.tray is not None:
            self._hide_to_tray()
            return
        # 没有托盘（依赖缺失）时，保留原来的“关窗即退出”确认流程
        if self._waiting:
            choice = messagebox.askyesnocancel(
                "伴生体还在回复",
                "伴生体还在想回复。\n\n"
                "选「是」= 等他把这句说完再退出\n"
                "选「否」= 立刻退出（这句回复作废）\n"
                "选「取消」= 继续聊"
            )
            if choice is None:
                return
            if choice:
                self._quit_after_reply = True
                self._append_sys("等伴生体把这句说完，就退出…")
                return
            self._closing = True
            self._clear_typing()
        self._ask_reflect_then_quit()

    def _ask_reflect_then_quit(self):
        if self._closing is False:
            self._closing = True
        # 今天已经反思过（含“无新增”）就不再问，直接退出
        already = False
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            already = bool(self.chen.memory) and \
                self.chen.memory._today_reflection_body(today) is not None
        except Exception:
            already = False
        if already:
            self._finish_quit()
            return
        do_reflect = messagebox.askyesno("今日反思", "退出前要进行今日反思吗？")
        if do_reflect:
            self._clear_typing()
            self._append_sys("正在进行今日反思…")
            self._start_reflect(then_quit=True)
        else:
            self._finish_quit()

    def _finish_quit(self):
        self.is_running = False
        poll_id = getattr(self, "_poll_after_id", None)
        if poll_id:
            try:
                self.root.after_cancel(poll_id)
            except Exception:
                pass
        try:
            self.chen.stop()
        except Exception:
            pass
        try:
            if getattr(self, "remote_server", None):
                self.remote_server.stop()
        except Exception:
            pass
        try:
            if self.desktop_pet:
                self.desktop_pet.destroy()
        except Exception:
            pass
        if getattr(self, "tray", None):
            self.tray.stop()
        try:
            self.root.destroy()
        except Exception:
            pass
