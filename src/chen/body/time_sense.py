# -*- coding: utf-8 -*-
"""
伴生体时间感模块 —— 让时间成为他稳定的认知变量，而不只是一个时钟读数。

三层时间：
  现实时间：周几 / 时段（已由 _get_time_context 提供）。
  经历时间：节律与预期——“周X这个时段用户通常在不在电脑前、常用什么”，
            从而能发现异常（周一傍晚本该在学校，今天却在家 → 自然的“你怎么这么早回来了”）。
  内部时间：“最近”的变化——patterns.json 已统计趋势，这里只挑有把握的轻量带入
            （最近用户用某软件比前阵子多/少），让他对“近来”有感觉；
            用户不在时也明确“这段时间是你自己的，时间照常走”。

性能：render_block() 每轮调用，只做文件读取 + 确定性聚合，绝不调 LLM。
节律从近 30 天 activity 日志聚合；“最近”读 patterns.json（已由维护任务定期生成）。
"""

import json
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path


# 时段桶 -> (起始小时, 结束小时, 跨度分钟)；结束小时可超过24表示跨到次日凌晨
PERIODS = OrderedDict([
    ("早上", (5, 9, 240)),
    ("上午", (9, 11, 120)),
    ("中午", (11, 13, 120)),
    ("下午", (13, 17, 240)),
    ("傍晚", (17, 19, 120)),
    ("晚上", (19, 23, 240)),
    ("深夜", (23, 29, 360)),
])

LOOKBACK_DAYS = 30      # 节律学习回看天数
MIN_WEEKDAY_SAMPLES = 2  # 某个周几至少出现几次才敢下预期
USUALLY_FRAC = 0.5      # 活跃占比高于此 → 通常在
RARELY_FRAC = 0.18      # 低于此 → 通常不在
PATTERN_FRESH_HOURS = 50  # patterns.json 多久内算新鲜
LATELY_LIMIT = 2
LATELY_MIN_AVG = 8      # up/down 趋势需达到的近期日均，避免微小波动
NEW_MIN_AVG = 8

# 系统/通用/识别不出的 app 不作为“最近变化”，避免“最近开始常用图片和视频”这类无意义内容
SYSTEM_APP_DENY = (
    "图片和视频", "照片", "文件管理器", "资源管理器", "任务管理器", "系统设置",
    "设置", "任务栏", "开始菜单", "桌面", "未知", "截图工具", "截图", "计算器",
    "锁屏", "windows", "运行", "搜索", "登录",
)


class TimeSense:
    """节律/预期聚合、近期趋势读取与注入。"""

    def __init__(self, activity_dir, patterns_file):
        self.activity_dir = Path(activity_dir)
        self.patterns_file = Path(patterns_file)
        self._rhythm_cache = None   # (聚合时刻, (weekday_occ, active, app_counts))

    # ---------- 时段工具 ----------

    @staticmethod
    def period_of_hour(hour):
        for name, (lo, hi, _span) in PERIODS.items():
            if lo <= hour < hi:
                return name
        return "深夜"

    @staticmethod
    def context_text(now=None):
        """返回客观时间文本，不附带提醒或关怀暗示。"""
        now = now or datetime.now()
        weekday = now.weekday()
        day_type = "周末" if weekday >= 5 else "上学日"
        period = TimeSense.period_of_hour(now.hour)
        weekday_name = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][weekday]
        return f"今天是{weekday_name}（{day_type}），现在是{period}。"

    # 月份 -> (季节, 月内细称, 你的城市本地物候感)
    # 不堆知识，只给他一句能自然带出来的体感。
    _SEASON_BY_MONTH = {
        1:  ("冬", "隆冬",   "出太阳就暖，阴天冷"),
        2:  ("冬", "残冬",   "快开春了，太阳好的时候已经暖"),
        3:  ("春", "初春",   "圆通山的樱花开了"),
        4:  ("春", "仲春",   "干燥，风大"),
        5:  ("春", "暮春",   "教场中路的蓝花楹开了"),
        6:  ("夏", "初夏",   "雨季开始，天天下"),
        7:  ("夏", "盛夏",   "最热的月份其实不热，雨下得最勤"),
        8:  ("夏", "暮夏",   "雨还多，早晚凉下来了"),
        9:  ("秋", "初秋",   "雨停了，天很高"),
        10: ("秋", "仲秋",   "最舒服的时候，不冷不热"),
        11: ("秋", "深秋",   "银杏黄了，早晚凉"),
        12: ("冬", "初冬",   "出太阳就舒服，阴天冷"),
    }

    @classmethod
    def season_of(cls, now=None):
        """此刻的季节感。返回 dict：{name, label, note}。纯本地、零成本。"""
        now = now or datetime.now()
        name, label, note = cls._SEASON_BY_MONTH.get(now.month, ("?", "?", ""))
        return {"name": name, "label": label, "note": note}

    # ---------- 节律聚合（确定性） ----------

    def _load_rhythm(self, now):
        """节律聚合（带 TTL 缓存）：10分钟内复用，避免每轮重新解析近30个日志文件。"""
        RHYTHM_TTL = 600
        if self._rhythm_cache is not None:
            _ts, _val = self._rhythm_cache
            if (now - _ts).total_seconds() < RHYTHM_TTL:
                return _val
        _val = self._build_rhythm(now)
        self._rhythm_cache = (now, _val)
        return _val

    def _build_rhythm(self, now):
        """
        聚合近 LOOKBACK_DAYS 天活动日志：
        返回 (weekday_occ, active[(weekday,period)], app_counts[(weekday,period)][app])。
        凌晨(0-5)记录归到前一天的“深夜”，符合“这一晚”的归属。
        """
        weekday_occ = {}                 # weekday -> 出现的天数
        active = {}                      # (weekday,period) -> 活跃分钟
        app_counts = {}                  # (weekday,period) -> {app: 分钟}

        for back in range(LOOKBACK_DAYS):
            day = (now - timedelta(days=back)).date()
            f = self.activity_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"
            weekday = day.weekday()
            weekday_occ[weekday] = weekday_occ.get(weekday, 0) + 1
            if not f.exists():
                continue
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("timestamp", "")
                try:
                    rdt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    continue
                hour = rdt.hour
                rec_weekday = weekday
                if 0 <= hour < 5:
                    # 凌晨归到前一晚
                    rec_weekday = (weekday - 1) % 7
                period = self.period_of_hour(hour)
                key = (rec_weekday, period)
                active[key] = active.get(key, 0) + 1
                app = rec.get("app", "未知")
                app_counts.setdefault(key, {})
                app_counts[key][app] = app_counts[key].get(app, 0) + 1

        return weekday_occ, active, app_counts

    def _expectation(self, now, presence):
        """对‘此刻(周几,时段)’的预期；返回一句文本，无把握/无异常返回空串。"""
        weekday_occ, active, app_counts = self._load_rhythm(now)
        samples = weekday_occ.get(now.weekday(), 0)
        if samples < MIN_WEEKDAY_SAMPLES:
            return ""
        period = self.period_of_hour(now.hour)
        span = PERIODS[period][2]
        key = (now.weekday(), period)
        frac = active.get(key, 0) / float(samples * span)

        weekday_cn = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][now.weekday()]
        # 该时段最常用的 app
        modal_app = ""
        if app_counts.get(key):
            modal_app = max(app_counts[key].items(), key=lambda x: x[1])[0]

        # 通常不在，此刻却在 → 异常（最有反应价值）
        if frac < RARELY_FRAC and presence == "ACTIVE":
            base = f"{weekday_cn}{period}用户通常不在电脑前，这会儿在"
            if modal_app and frac > 0:
                return base + "。"
            return base + "。"
        # 通常在，此刻却不在 → 异常
        if frac > USUALLY_FRAC and presence == "AWAY":
            return f"{weekday_cn}{period}用户通常在电脑前，这会儿不在。"
        # 与预期一致：不占 token，保持安静
        return ""

    # ---------- “最近”趋势（读 patterns.json） ----------

    def _lately_lines(self):
        """从 patterns.json 挑有把握的近期变化，最多 LATELY_LIMIT 条，中性陈述。"""
        try:
            if not self.patterns_file.exists():
                return []
            data = json.loads(self.patterns_file.read_text(encoding="utf-8"))
            gen = datetime.strptime(data.get("generated_at"), "%Y-%m-%d %H:%M:%S")
            if datetime.now() - gen > timedelta(hours=PATTERN_FRESH_HOURS):
                return []
        except Exception:
            return []

        candidates = []
        apps = (data.get("apps") or {})
        for app, info in apps.items():
            if not app or any(s in app.lower() for s in SYSTEM_APP_DENY):
                continue
            trend = info.get("trend")
            recent = float(info.get("recent3_avg", 0))
            daily = float(info.get("daily_avg", 0))
            if trend == "new" and recent >= NEW_MIN_AVG:
                candidates.append((recent, f"最近用户开始常用{app}"))
            elif trend == "up" and recent >= LATELY_MIN_AVG:
                candidates.append((recent - daily, f"最近用户用{app}比前阵子多"))
            elif trend == "down" and daily >= LATELY_MIN_AVG:
                candidates.append((daily - recent, f"最近用户用{app}比前阵子少"))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [text for _mag, text in candidates[:LATELY_LIMIT]]

    # ---------- 每轮注入 ----------

    def render_block(self, *, presence="ACTIVE", away_sec=0, app="",
                     gap_minutes=None, now=None):
        """生成时间感注入文本（节律异常 / 最近变化 / 用户不在时的自我时间）。可能为空。"""
        now = now or datetime.now()
        lines = []

        # 节律预期 / 异常
        try:
            exp = self._expectation(now, presence)
        except Exception:
            exp = ""
        if exp:
            lines.append(exp)

        # 近期变化
        try:
            lines.extend(self._lately_lines())
        except Exception:
            pass

        # 用户不在：明确这段时间是“他自己的”，内部时间照常流动
        if presence == "AWAY" and away_sec and away_sec >= 30 * 60:
            away_min = int(away_sec // 60)
            if away_min >= 60:
                dur = f"{away_min // 60}小时{away_min % 60}分钟"
            else:
                dur = f"{away_min}分钟"
            lines.append(f"用户已经离开约{dur}，这段时间是你自己的，时间照常走，不是停在原地等用户。")

        if not lines:
            return ""
        header = [
            "====时间感====",
            "（你对‘现在、节律、最近’的感觉，帮你自然反应，不要逐条复述或念出来）",
        ]
        return "\n".join(header + ["- " + x for x in lines])
