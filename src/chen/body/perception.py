"""
感知模块 - 伴生体的"眼睛"
监控用户的活动窗口，记录活动日志，并生成时间与状态事件。

隐私边界：
- 只感知：窗口标题、活跃时间
- 不感知：键盘输入、密码、屏幕截图、剪贴板、浏览器具体内容
- 包含"密码""登录""支付"等关键词的窗口标题自动排除
"""

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from chen.paths import PROJECT_ROOT

try:
    import win32gui
    import win32api
    import win32process
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class Perception:
    """感知层 - 监控用户活动，计算持续时间并生成感知事件"""

    def __init__(self, log_dir="memory/activity", interval=60):
        self.base_dir = PROJECT_ROOT
        self.log_dir = self.base_dir / log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.interval = interval  # 采样间隔（秒），默认60秒
        self.is_monitoring = False
        self._monitor_thread = None
        self._stop_event = threading.Event()

        # ---------- 持续时间与事件追踪状态 ----------
        self.current_app = None
        self.current_app_start_time = None
        self.current_app_duration_minutes = 0
        self.current_app_detail = None  # 当前程序的细节（如B站正在看的视频标题），其他程序为None
        self._reported_thresholds = set()  # 记录当前应用已经触发过哪些时长报警（如 60, 120），避免每分钟重复触发
        
        # 待消费事件队列（供 decision.py 取出并判断）
        self.pending_events = []
        self._events_lock = threading.Lock()

        # ---------- 在场状态追踪（用户是否在电脑前）----------
        self.presence_state = "ACTIVE"     # ACTIVE=在电脑前 / AWAY=离开
        self.away_since = None             # 最后一次键鼠操作的时间（离开的起点）
        self.away_threshold = 300          # 键鼠空闲达到5分钟判定为"离开"
        self.back_idle_limit = 60          # 离开状态下，空闲降到1分钟内判定为"回来了"

        # 隐私排除关键词
        self.exclude_keywords = [
            "密码", "登录", "支付", "银行", "验证码", "转账",
            "password", "login", "pay", "bank", "otp",
        ]

        # 程序名映射
        self.app_aliases = {
            # 游戏
            "崩坏：星穹铁道": "星穹铁道",
            "崩坏星穹铁道": "星穹铁道",
            "starrail": "星穹铁道",
            "明日方舟": "明日方舟",
            "arknights": "明日方舟",
            "终末地": "明日方舟：终末地",
            "endfield": "明日方舟：终末地",
            "泰拉瑞亚": "泰拉瑞亚",
            "terraria": "泰拉瑞亚",
            "星露谷": "星露谷物语",
            "stardew": "星露谷物语",
            "minecraft": "我的世界",
            "我的世界": "我的世界",
            "steam": "Steam",
            "left 4 dead": "求生之路2",
            "left4dead": "求生之路2",
            "求生之路": "求生之路2",
            # 社交
            "微信": "微信",
            "wechat": "微信",
            "qq": "QQ",
            "tim": "QQ",
            "钉钉": "钉钉",
            "dingtalk": "钉钉",
            # 浏览器
            "chrome": "Chrome",
            "msedge": "Edge",
            "edge": "Edge",
            "firefox": "Firefox",
            # 开发工具
            "visual studio code": "VS Code",
            "vs code": "VS Code",
            "cursor": "Cursor",
            "pycharm": "PyCharm",
            "jupyter": "Jupyter",
            # 办公
            "word": "Word",
            "excel": "Excel",
            "powerpoint": "PowerPoint",
            "ppt": "PowerPoint",
            "wps": "WPS",
            "pdf": "PDF阅读器",
            # 系统
            "文件资源管理器": "文件管理器",
            "explorer": "文件管理器",
            "设置": "系统设置",
            "任务管理器": "任务管理器",
            # 视频/音乐
            "bilibili": "B站",
            "哔哩哔哩": "B站",
            "网易云音乐": "网易云音乐",
            "qq音乐": "QQ音乐",
            "youtube": "YouTube",
            "spotify": "Spotify",
            # AI工具
            "豆包": "豆包",
            "doubao": "豆包",
            "chatgpt": "ChatGPT",
            "lm studio": "LM Studio",
            "gemini": "Gemini",
            "claude": "Claude",
            # 系统界面（简化或排除）
            "windows 默认锁屏": "锁屏",
            "windows 默认锁屏界面": "锁屏",
            "lockingwindow": "锁屏",
            "锁屏界面": "锁屏",
            "桌面": "桌面",
            "任务栏": "任务栏",
            "开始菜单": "开始菜单",
            # RPG制作
            "rpg maker": "RPG Maker",
            # 其他常见
            "记事本": "记事本",
            "notepad": "记事本",
            "计算器": "计算器",
            "画图": "画图",
            "截图": "截图工具",
            "snip": "截图工具",
        }

        # 浏览器网站识别（从窗口标题中提取网站名）
        self.website_patterns = [
            # 学习/知识
            ("zhihu.com", "知乎"), ("知乎", "知乎"),
            ("baidu.com", "百度"), ("百度", "百度"),
            ("wenku.baidu", "百度文库"), ("百度文库", "百度文库"),
            ("bilibili.com", "B站"), ("哔哩哔哩", "B站"),
            ("xuexi", "学习平台"), ("国家中小学", "国家中小学智慧教育"),
            ("zxxk", "学科网"), ("zuoyebang", "作业帮"), ("yuanfudao", "猿辅导"),
            ("coursera", "Coursera"), ("icourse163", "中国大学MOOC"),
            ("wikipedia", "维基百科"), ("维基百科", "维基百科"),
            ("csdn", "CSDN"), ("github", "GitHub"), ("stackoverflow", "StackOverflow"),
            # 娱乐/视频
            ("youtube.com", "YouTube"), ("youtube", "YouTube"),
            ("iqiyi", "爱奇艺"), ("youku", "优酷"), ("tencent.com", "腾讯视频"),
            ("mgtv", "芒果TV"), ("acfun", "A站"),
            ("douyin", "抖音"), ("抖音", "抖音"),
            ("kuaishou", "快手"),
            # 社交
            ("weibo.com", "微博"), ("微博", "微博"),
            ("xiaohongshu", "小红书"), ("小红书", "小红书"),
            ("tieba.baidu", "百度贴吧"), ("贴吧", "百度贴吧"),
            ("zhihu.com", "知乎"),
            # 购物
            ("taobao.com", "淘宝"), ("淘宝", "淘宝"),
            ("tmall.com", "天宠物"), ("天宠物", "天宠物"),
            ("jd.com", "京东"), ("京东", "京东"),
            ("pinduoduo", "拼多多"),
            # 音乐
            ("music.163", "网易云音乐"), ("网易云音乐", "网易云音乐"),
            ("y.qq.com", "QQ音乐"),
            # 工具/其他
            ("google.com", "Google"), ("google", "Google"),
            ("bing.com", "必应"), ("必应", "必应"),
            ("weather", "天气"),
        ]

        # 应用分类标签（让伴生体知道你在做什么类型的事）
        self.app_categories = {
            # 学习
            "知乎": "学习", "百度": "学习", "百度文库": "学习", "B站": "学习/娱乐",
            "国家中小学智慧教育": "学习", "学科网": "学习", "作业帮": "学习",
            "Coursera": "学习", "中国大学MOOC": "学习", "维基百科": "学习",
            "CSDN": "学习", "GitHub": "学习/工作", "StackOverflow": "学习",
            "Word": "学习/工作", "Excel": "学习/工作", "PowerPoint": "学习/工作",
            "WPS": "学习/工作", "PDF阅读器": "学习", "记事本": "学习/工作",
            "VS Code": "工作", "Cursor": "工作", "PyCharm": "工作", "Jupyter": "工作",
            "RPG Maker": "工作",
            # 娱乐
            "YouTube": "娱乐", "爱奇艺": "娱乐", "优酷": "娱乐", "腾讯视频": "娱乐",
            "芒果TV": "娱乐", "A站": "娱乐", "抖音": "娱乐", "快手": "娱乐",
            "星穹铁道": "娱乐", "明日方舟": "娱乐", "明日方舟：终末地": "娱乐",
            "泰拉瑞亚": "娱乐", "星露谷物语": "娱乐", "我的世界": "娱乐",
            "Steam": "娱乐", "求生之路2": "娱乐",
            "网易云音乐": "娱乐", "QQ音乐": "娱乐", "Spotify": "娱乐",
            "微博": "娱乐", "小红书": "娱乐", "百度贴吧": "娱乐",
            "淘宝": "娱乐", "天宠物": "娱乐", "京东": "娱乐", "拼多多": "娱乐",
            # 社交
            "微信": "社交", "QQ": "社交", "钉钉": "社交",
            # 系统/其他
            "文件管理器": "系统", "系统设置": "系统", "任务管理器": "系统",
            "命令提示符": "系统", "系统对话框": "系统", "系统界面": "系统",
            "桌面": "系统", "锁屏": "系统", "开始菜单": "系统", "任务栏": "系统",
            "计算器": "系统", "画图": "系统", "截图工具": "系统",
            "伴生体聊天窗": "和伴生体聊天",
            "豆包": "学习/工作", "ChatGPT": "学习/工作", "LM Studio": "工作",
            "Gemini": "学习/工作", "Claude": "学习/工作",
            "Chrome": "浏览", "Edge": "浏览", "Firefox": "浏览",
        }

    # ========== 窗口获取与隐私过滤 ==========

    def _get_process_name(self, hwnd):
        """通过窗口句柄拿到所属进程的exe名（小写），失败返回空串"""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            full_path = win32process.GetModuleFileNameEx(handle, 0)
            return full_path.split("\\")[-1].lower()
        except Exception:
            return ""

    def get_active_window(self):
        """获取当前活动窗口标题和进程名，返回(title, process_name)；失败返回(None, "")"""
        if not HAS_WIN32:
            return None, ""
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            title = title.strip() if title else None
            return title, self._get_process_name(hwnd)
        except Exception:
            return None, ""

    def get_idle_seconds(self):
        """返回距上一次键鼠操作的秒数（无法获取时返回0，表示一直在用）"""
        if not HAS_WIN32:
            return 0
        try:
            # GetTickCount：系统启动至今毫秒；GetLastInputInfo：最后一次输入时的tick
            return max(0, (win32api.GetTickCount() - win32api.GetLastInputInfo()) / 1000.0)
        except Exception:
            return 0

    def get_presence_status(self):
        """返回(状态, 已持续秒数)：状态为 ACTIVE（在电脑前）或 AWAY（离开）"""
        if self.presence_state == "AWAY" and self.away_since:
            away_sec = int((datetime.now() - self.away_since).total_seconds())
            return "AWAY", max(0, away_sec)
        return "ACTIVE", 0

    def prompt_state(self):
        """返回可供 system prompt 使用的授权感知摘要。"""
        presence, away_sec = self.get_presence_status()
        app, duration, detail = "", 0, ""
        lines = []
        if presence == "AWAY":
            away_min = away_sec // 60
            away_text = (
                f"约{away_min // 60}小时{away_min % 60}分钟"
                if away_min >= 60 else f"约{away_min}分钟"
            )
            lines.append(
                f"用户现在不在电脑前（键鼠已经{away_text}没有操作），"
                "可能离开了或在忙别的，不用假设用户正盯着屏幕。"
            )
        else:
            app, duration, detail = self.get_current_app_status()
            if app:
                if app == "B站" and detail:
                    lines.append(
                        f"感知层检测到用户正在B站看视频《{detail}》。"
                        "你只能看到程序名和视频标题，看不到弹幕、评论或具体画面。"
                    )
                else:
                    lines.append(
                        f"感知层当前检测到用户正在使用：{app}（已持续约{duration}分钟）。"
                        "你只能看到程序名，看不到屏幕内容、键盘输入或具体操作。"
                    )

        today_summary, app_counts = self.get_daily_summary()
        if app_counts and len(app_counts) >= 2:
            lines.append(f"用户今天的电脑活动：{today_summary}")
        return {
            "presence": presence,
            "away_sec": away_sec,
            "app": app,
            "duration": duration,
            "detail": detail,
            "today_summary": today_summary,
            "app_counts": app_counts,
            "lines": lines,
        }

    def _simplify_title(self, title, process_name=""):
        """把窗口标题简化成程序名；优先用进程名识别（解决客户端播放页标题不带站名的问题）"""
        if not title:
            return "未知"
        # 1. 先按进程名识别（最可靠）
        pn = (process_name or "").lower()
        process_map = {
            # 浏览器/视频/开发
            "哔哩哔哩": "B站", "bilibili": "B站",
            "msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox",
            "code.exe": "VS Code",
            # 系统壳与终端：窗口标题常是路径或零散对话框，按进程归一最准
            "explorer.exe": "文件管理器",
            "cmd.exe": "命令提示符",
            "powershell.exe": "命令提示符",
            "windowsterminal": "命令提示符",
            "sndvol": "系统界面",
            "control.exe": "系统设置",
            # 音乐（日文歌等窗口标题不带站名，靠进程名兜底）
            "cloudmusic": "网易云音乐",
            "qqmusic": "QQ音乐",
            # 游戏（窗口标题千变万化，按进程名兜底）
            "steam": "Steam",
            "left4dead": "求生之路2",
            "starrail": "星穹铁道",
            "minecraft": "我的世界",
        }
        for key, alias in process_map.items():
            if key in pn:
                return alias
        # 1.5 伴生体自己的聊天窗（Tk 标题“伴生体”/“伴生体 · 有新消息”）：当成一个程序，
        #     不与他本人的自我指涉混淆。放在进程名之后，避免误判 VS Code 里的同名文件。
        if title == "伴生体" or title.startswith("伴生体 ·") or title.startswith("伴生体·"):
            return "伴生体聊天窗"
        # 通用文件/系统对话框（资源管理器进程已在上面归一，这里兜其他程序弹的模态框）
        for _pref in ("选择", "另存为", "保存", "打开文件", "浏览文件夹"):
            if title.startswith(_pref) and len(title) <= 12:
                return "系统对话框"
        # 2. 再按窗口标题关键词识别
        title_lower = title.lower()
        for keyword, alias in self.app_aliases.items():
            if keyword.lower() in title_lower:
                return alias
        return title[:20] if len(title) > 20 else title

    def _extract_app_detail(self, raw_title, app_name):
        """
        提取窗口里值得让伴生体知道的"细节"。
        - B站：提取正在看的视频标题
        - 浏览器：识别正在访问的网站名
        其他程序一律返回None（不额外读取，守住隐私边界）。
        """
        if not raw_title:
            return None

        # B站：提取视频标题
        if app_name == "B站":
            if "干杯" in raw_title:
                return None
            detail = raw_title
            detail = re.sub(r"bilibili", "", detail, flags=re.IGNORECASE)
            detail = detail.replace("哔哩哔哩", "").replace("哔哩", "")
            detail = re.sub(r"^[\s_\-|–—:：.、]+", "", detail)
            detail = re.sub(r"[\s_\-|–—:：.、]+$", "", detail)
            detail = detail.strip()
            if len(detail) < 4:
                return None
            if len(detail) > 40:
                detail = detail[:40]
            return detail

        # 浏览器：识别网站名
        if app_name in ("Chrome", "Edge", "Firefox", "浏览"):
            title_lower = raw_title.lower()
            for pattern, site_name in self.website_patterns:
                if pattern.lower() in title_lower:
                    return site_name
            # 识别不到具体网站时，返回"浏览网页"
            return "浏览网页"

        return None

    def get_app_category(self, app_name):
        """获取应用的分类标签（学习/娱乐/工作/社交/系统/和伴生体聊天）"""
        return self.app_categories.get(app_name, "其他")

    def _should_exclude(self, title):
        """检查窗口标题是否包含隐私排除关键词"""
        if not title:
            return True
        title_lower = title.lower()
        for keyword in self.exclude_keywords:
            if keyword.lower() in title_lower:
                return True
        return False

    # ========== 日志存储 ==========

    def _get_log_file(self, date_str=None):
        """获取当天的日志文件路径"""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"{date_str}.jsonl"

    # ========== 核心：单次采样与持续时间事件计算 ==========

    def _looks_really_away(self):
        """
        判断前台窗口是否已经“看不出来还在用电脑”——锁屏、桌面、系统界面等
        意味着人可能真离开了；仍停留在某个普通应用（哪怕键鼠停了）则视为“还在电脑前，只是看手机/发呆/走神”。
        只看窗口归属，不读任何屏幕内容，守住隐私边界。
        """
        try:
            title, process_name = self.get_active_window()
            if not title:
                # 拿不到前台窗口（可能已在锁屏/休眠边缘），保守交给空闲判定本身
                return None
            app = self._simplify_title(title, process_name)
            # 明确的“人不在/只剩系统”信号才认为真的离开
            if app in ("锁屏", "桌面", "系统界面", "开始菜单", "任务栏", "Windows 默认锁屏", "未知"):
                return True
            # 还在正常应用里，只是键鼠空档 → 大概率还在电脑前
            return False
        except Exception:
            return None

    def _update_presence(self):
        """
        根据“键鼠空闲 + 前台窗口是否还在正常使用”追踪用户是在电脑前还是离开了。
        只有键鼠长时间停 AND 前台也从普通应用退到锁屏/桌面等“离开信号”时，才判定离开，
        避免“看手机/发呆/走神导致键鼠短暂静止”被误判成离开（那会触发一串“回来了”）。
        """
        idle_sec = self.get_idle_seconds()
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        with self._events_lock:
            if self.presence_state == "ACTIVE" and idle_sec >= self.away_threshold:
                # 键鼠停够久了，进一步确认前台是否已退到“离开”窗口
                really_away = self._looks_really_away()
                # None=拿不到窗口，回退到原逻辑（只按空闲判离开）；False=还在用普通应用，不算离开
                if really_away is False:
                    return
                # 在电脑前 -> 离开：起点按最后一次操作时间算（反推idle_sec）
                self.presence_state = "AWAY"
                self.away_since = now - timedelta(seconds=idle_sec)
                self.pending_events.append({
                    "type": "USER_AWAY",
                    "idle_minutes": int(idle_sec // 60),
                    "timestamp": now_str,
                })
            elif self.presence_state == "AWAY" and idle_sec < self.back_idle_limit:
                # 离开 -> 回来：键鼠重新活动了
                away_minutes = 0
                if self.away_since:
                    away_minutes = int((now - self.away_since).total_seconds() // 60)
                self.presence_state = "ACTIVE"
                self.away_since = None
                self.pending_events.append({
                    "type": "USER_BACK",
                    "away_minutes": away_minutes,
                    "timestamp": now_str,
                })

    def _record_once(self):
        """记录一次当前活动窗口并更新持续时间状态"""
        # 人离开后不再记录活动窗口（避免日志里堆满"锁屏/无操作"）
        if self.presence_state == "AWAY":
            return

        title, process_name = self.get_active_window()
        if not title or self._should_exclude(title):
            return

        app_name = self._simplify_title(title, process_name)
        # 锁屏窗口不计活跃（人其实已离开，只是空闲满5分钟判 AWAY 前的一小段空档）
        if app_name == "锁屏":
            return
        app_detail = self._extract_app_detail(title, app_name)
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 1. 基础写盘日志（detail只在有细节时写入，如B站视频标题）
        record = {
            "timestamp": now_str,
            "app": app_name,
            "title": title[:50],
        }
        if app_detail:
            record["detail"] = app_detail
        log_file = self._get_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 2. 状态持续时间与事件判定
        with self._events_lock:
            if self.current_app is None:
                # 初始启动
                self.current_app = app_name
                self.current_app_start_time = now
                self.current_app_duration_minutes = 0
                self.current_app_detail = app_detail
                self._reported_thresholds.clear()

            elif self.current_app != app_name:
                # 应用发生切换：生成“切换事件”
                duration = int((now - self.current_app_start_time).total_seconds() // 60)
                if duration >= 5:  # 只有使用超过5分钟的切换才值得记录为一个有意义事件
                    self.pending_events.append({
                        "type": "APP_SWITCH",
                        "from_app": self.current_app,
                        "to_app": app_name,
                        "duration_minutes": duration,
                        "timestamp": now_str,
                    })

                # 切换到了一个新软件：产生“焦点事件”，供决策层结合记忆自主决定要不要开口
                self.pending_events.append({
                    "type": "APP_FOCUS",
                    "app": app_name,
                    "from_app": self.current_app,
                    "detail": app_detail,
                    "timestamp": now_str,
                })

                # 重置当前应用跟踪
                self.current_app = app_name
                self.current_app_start_time = now
                self.current_app_duration_minutes = 0
                self.current_app_detail = app_detail
                self._reported_thresholds.clear()

            else:
                # 同一个应用持续运行：累加时长；细节（如换了个B站视频）也跟着更新
                self.current_app_detail = app_detail
                duration = int((now - self.current_app_start_time).total_seconds() // 60)
                self.current_app_duration_minutes = duration

                # 阈值判定：达到 60分钟、120分钟、180分钟 等显著时长时，生成持续事件
                # 这样不会每分钟发，只在每个里程碑生成一次事件
                for threshold in [45, 60, 90, 120, 180, 240]:
                    if duration >= threshold and threshold not in self._reported_thresholds:
                        self._reported_thresholds.add(threshold)
                        self.pending_events.append({
                            "type": "LONG_SESSION",
                            "app": app_name,
                            "duration_minutes": duration,
                            "timestamp": now_str,
                        })

    # ========== 决策层调用接口 ==========

    def pop_events(self):
        """取出并清空当前积攒的事件列表，供决策层（decision.py）读取消费"""
        with self._events_lock:
            events = list(self.pending_events)
            self.pending_events.clear()
            return events

    def get_current_app_status(self):
        """获取当前活跃应用、已连续持续的时长（分钟）、细节（如B站视频标题）"""
        if not self.current_app or not self.current_app_start_time:
            return None, 0, None
        duration = int((datetime.now() - self.current_app_start_time).total_seconds() // 60)
        return self.current_app, duration, self.current_app_detail

    def get_bilibili_watch_summary(self, date_str=None, max_titles=15):
        """
        汇总某天在B站看过的视频标题（去重、按出现顺序），供每日反思沉淀学科用。
        从当天日志读，程序中途重启也不丢。没有则返回空字符串。
        """
        records = self.load_daily_activity(date_str)
        seen = []
        seen_set = set()
        for r in records:
            if r.get("app") != "B站":
                continue
            detail = (r.get("detail") or "").strip()
            if detail and detail not in seen_set:
                seen_set.add(detail)
                seen.append(detail)
        if not seen:
            return ""
        return "用户今天在B站看了这些视频：" + "；".join(seen[:max_titles])

    # ========== 后台监控线程 ==========

    def _monitor_loop(self):
        """后台监控循环"""
        while not self._stop_event.is_set():
            try:
                self._update_presence()  # 先更新在/离状态，再记录窗口
                self._record_once()
            except Exception as e:
                print(f"  ⚠️ 感知记录失败：{e}")
            self._stop_event.wait(self.interval)

    def start_monitoring(self):
        """启动后台监控"""
        if self.is_monitoring:
            return
        if not HAS_WIN32:
            print("  ⚠️ 未安装pywin32，感知监控不可用")
            return
        self.is_monitoring = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        print(f"  👁️ 感知监控已启动（每{self.interval}秒记录一次活动窗口，并追踪持续时长）")

    def stop_monitoring(self):
        """停止后台监控"""
        if not self.is_monitoring:
            return
        self.is_monitoring = False
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        print("  👁️ 感知监控已停止")

    # ========== 历史活动汇总（保持原样） ==========

    def load_daily_activity(self, date_str=None):
        """加载某天的活动记录"""
        log_file = self._get_log_file(date_str)
        if not log_file.exists():
            return []
        records = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def get_daily_summary(self, date_str=None):
        """汇总某天的活动，生成自然语言摘要"""
        records = self.load_daily_activity(date_str)
        if not records:
            return "今天没有活动记录。", {}

        app_counts = {}
        for r in records:
            app = r.get("app", "未知")
            app_counts[app] = app_counts.get(app, 0) + 1

        sorted_apps = sorted(app_counts.items(), key=lambda x: x[1], reverse=True)
        total = len(records)
        parts = []
        for app, count in sorted_apps[:5]:
            minutes = count
            if minutes >= 60:
                time_str = f"{minutes // 60}小时{minutes % 60}分"
            else:
                time_str = f"{minutes}分钟"
            parts.append(f"{app}（约{time_str}）")

        summary = f"今天用户主要用了：{'、'.join(parts)}。总共活跃约{total}分钟。"
        return summary, app_counts

    def get_recent_activity_summary(self, minutes=30):
        """获取最近的活动摘要"""
        records = self.load_daily_activity()
        if not records:
            return "暂无活动记录"
        recent = records[-5:]
        apps = [r.get("app", "未知") for r in recent]
        seen = set()
        unique_apps = []
        for app in apps:
            if app not in seen:
                seen.add(app)
                unique_apps.append(app)
        return f"用户最近在使用：{'、'.join(unique_apps)}"

    def get_activity_since(self, since_dt, max_apps=3, max_bili=3):
        """汇总 since_dt 到现在（可跨天）的电脑活动，用于隔一段时间再聊时让伴生体知道
        “这段时间用户在电脑上忙过什么”。返回 (文本, 是否有内容)；用户没用电脑则返回空。"""
        if since_dt is None:
            return "", False
        from datetime import timedelta
        records = []
        day = since_dt.date()
        today = datetime.now().date()
        guard = 0
        while day <= today and guard < 8:  # 最多回溯8天，防止异常死循环
            records.extend(self.load_daily_activity(day.strftime("%Y-%m-%d")))
            day += timedelta(days=1)
            guard += 1

        new_records = []
        for r in records:
            ts = r.get("timestamp")
            if not ts:
                continue
            try:
                rec_t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if rec_t >= since_dt:
                new_records.append(r)
        if not new_records:
            return "", False

        app_minutes = {}
        bili, bili_seen = [], set()
        for r in new_records:
            app = r.get("app", "未知")
            app_minutes[app] = app_minutes.get(app, 0) + 1
            if app == "B站":
                d = (r.get("detail") or "").strip()
                if d and d not in bili_seen:
                    bili_seen.add(d)
                    bili.append(d)

        parts = []
        for app, mins in sorted(app_minutes.items(), key=lambda x: -x[1])[:max_apps]:
            if mins < 5:  # 不足5分钟不提，避免噪声
                continue
            if mins >= 60:
                span = f"{mins // 60}小时{mins % 60}分"
            else:
                span = f"{mins}分钟"
            parts.append(f"{app}约{span}")
        text = ""
        if parts:
            text = "这段时间用户在电脑上用过：" + "、".join(parts) + "。"
        if bili:
            text += "B站看了：" + "、".join(f"《{b}》" for b in bili[:max_bili]) + "。"
        return text, bool(text)

    def archive_old_logs(self, keep_days=30):
        """
        自动整理活动日志：
        - 最近 keep_days 天的日明细保留在 activity/ 下
        - 更早的按月份合并进 activity/archive/YYYY-MM.jsonl，然后删掉日文件
        返回归档的日文件数量。
        """
        import re as _re
        from datetime import timedelta
        archive_dir = self.log_dir / "archive"
        cutoff = datetime.now() - timedelta(days=keep_days)
        day_pattern = _re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")

        archived = 0
        for f in self.log_dir.glob("*.jsonl"):
            m = day_pattern.match(f.name)
            if not m:
                continue
            try:
                file_date = datetime.strptime(m.group(1), "%Y-%m-%d")
            except ValueError:
                continue
            if file_date >= cutoff:
                continue  # 最近的保留

            # 归入对应月份
            month_key = file_date.strftime("%Y-%m")
            archive_dir.mkdir(parents=True, exist_ok=True)
            month_file = archive_dir / f"{month_key}.jsonl"
            content = f.read_text(encoding="utf-8")
            if content.strip():
                with open(month_file, "a", encoding="utf-8") as out:
                    out.write(content)
            f.unlink()  # 删掉已归档的日文件
            archived += 1

        return archived
