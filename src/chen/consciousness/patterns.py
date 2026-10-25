# -*- coding: utf-8 -*-
"""
事件频率统计（认知层 · 偏好/脾气内化的地基）

设计原则（第一版）：
  - 只做确定性统计，只落盘 patterns.json；不改伴生体任何行为、不进 system prompt。
  - 反复出现的事先被“数清楚”，跑一段时间确认统计准确后，第二版才挑稳定的缓慢沉淀成
    他的偏好/判断。没有重复就没有成长，且成长必须显式、缓慢、可查。
  - 数据来源：memory/activity/YYYY-MM-DD.jsonl（约每分钟一条 app 记录，B站带 detail 标题）。
  - patterns.json 是“当前近 N 天”的滚动快照，每次整体覆写，不会无限增长。

趋势口径：用“最近3个完整天”对比“更早3个完整天”，刻意排除数据还不完整的今天，
        避免统计运行的时刻不同（早上 vs 晚上）把趋势误判成下降。
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path


WINDOW_DAYS = 7  # 默认滚动窗口

# B站标题 -> 学科（命中任一关键词即归类，一个标题可命中多个学科）
SUBJECT_KEYWORDS = {
    "数学": ["数学", "函数", "几用户", "代数", "导数", "方程", "三角", "数列", "向量",
            "概率", "立体", "不等式", "集合", "圆锥曲线", "排列", "组合"],
    "英语": ["英语", "单词", "语法", "听力", "完形", "时态", "从句", "雅思", "托福", "词汇"],
    "语文": ["语文", "作文", "古文", "古诗", "文言文", "诗歌", "现代文", "默写", "红楼梦"],
    "物理": ["物理", "力学", "运动学", "电学", "电路", "牛顿", "磁场", "电场", "光学",
            "热学", "受力", "动能", "万有引力"],
    "化学": ["化学", "反应", "方程式", "元素", "有机", "无机", "离子", "氧化", "还原",
            "物质的量", "化学实验"],
    "生物": ["生物", "细胞", "遗传", "基因", "生态", "光合", "呼吸作用", "免疫", "激素", "进化"],
    "政治": ["政治", "哲学", "唯物", "辩证", "时政", "经济与社会"],
    "历史": ["历史", "朝代", "近代史", "古代史", "战争史", "改革", "革命", "史料"],
    "地理": ["地理", "气候", "地形", "经纬", "洋流", "板块", "人口", "区域地理", "大气环流"],
}

# 非学科、但值得知道的长期兴趣/项目主题
TOPIC_KEYWORDS = {
    "编程/AI": ["python", "编程", "代码", "程序", "人工智能", "模型", "算法", "开发",
              "github", "机器学习", "深度学习", "神经网络", "教程"],
    "游戏": ["游戏", "攻略", "实况", "星穹", "方舟", "原神", "我的世界", "mc", "通关"],
    "音乐": ["音乐", "歌曲", "翻唱", "吉他", "钢琴", "乐队"],
}


class PatternStats:
    def __init__(self, activity_dir, out_file):
        self.activity_dir = Path(activity_dir)
        self.out_file = Path(out_file)

    # ---------- 读取 ----------

    def _load_day(self, day):
        """读某一天的活动记录，day 为 date；文件不存在或损坏返回 []。"""
        f = self.activity_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"
        if not f.exists():
            return []
        records = []
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            return []
        return records

    @staticmethod
    def _period(hour):
        if 5 <= hour < 11:
            return "上午"
        if 11 <= hour < 14:
            return "午间"
        if 14 <= hour < 18:
            return "下午"
        if 18 <= hour < 23:
            return "晚上"
        return "深夜"

    # ---------- 主流程 ----------

    def update(self, days=WINDOW_DAYS):
        today = datetime.now().date()
        # 窗口内每一天的记录（含今天）
        per_day = {}
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            per_day[d.strftime("%Y-%m-%d")] = self._load_day(d)

        app_window = {}       # 整个窗口各 app 的分钟数（记录条数≈分钟）
        app_today = {}        # 今天各 app 分钟数
        daily_app = {}        # 日期 -> {app: 分钟}
        period_minutes = {"上午": 0, "午间": 0, "下午": 0, "晚上": 0, "深夜": 0}
        subj_minutes = {}     # 学科 -> 观看记录条数（≈分钟，带时长权重）
        topic_minutes = {}
        bili_titles, bili_seen = [], set()
        active_days = set()
        today_str = today.strftime("%Y-%m-%d")

        for ds, records in per_day.items():
            if records:
                active_days.add(ds)
            day_app = {}
            for r in records:
                app = r.get("app", "未知")
                app_window[app] = app_window.get(app, 0) + 1
                day_app[app] = day_app.get(app, 0) + 1
                if ds == today_str:
                    app_today[app] = app_today.get(app, 0) + 1
                hour = 12
                ts = r.get("timestamp", "")
                try:
                    hour = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").hour
                except (ValueError, TypeError):
                    pass
                period_minutes[self._period(hour)] = period_minutes.get(self._period(hour), 0) + 1

                if app == "B站":
                    title = (r.get("detail") or "").strip()
                    low = title.lower()
                    if title and title not in bili_seen:
                        bili_seen.add(title)
                        bili_titles.append(title)
                    for subj, kws in SUBJECT_KEYWORDS.items():
                        if any(k.lower() in low for k in kws):
                            subj_minutes[subj] = subj_minutes.get(subj, 0) + 1
                    for topic, kws in TOPIC_KEYWORDS.items():
                        if any(k.lower() in low for k in kws):
                            topic_minutes[topic] = topic_minutes.get(topic, 0) + 1
            daily_app[ds] = day_app

        # 趋势：最近3个完整天（昨天起往前3天）vs 更早3个完整天，排除今天
        def _day_total(d, app):
            return daily_app.get(d.strftime("%Y-%m-%d"), {}).get(app, 0)

        recent_days = [today - timedelta(days=i) for i in (1, 2, 3)]
        older_days = [today - timedelta(days=i) for i in (4, 5, 6)]

        def _trend(app):
            recent_avg = sum(_day_total(d, app) for d in recent_days) / 3.0
            older_avg = sum(_day_total(d, app) for d in older_days) / 3.0
            if older_avg < 1 and recent_avg >= 3:
                return "new"
            diff = recent_avg - older_avg
            if older_avg > 0 and recent_avg > older_avg * 1.3 and diff >= 5:
                return "up"
            if older_avg > 0 and recent_avg < older_avg * 0.7 and diff <= -5:
                return "down"
            return "stable"

        apps = {}
        for app, mins in sorted(app_window.items(), key=lambda x: -x[1]):
            apps[app] = {
                "today_min": app_today.get(app, 0),
                "window_minutes": mins,
                "daily_avg": round(mins / max(len(active_days), 1), 1),
                "recent3_avg": round(sum(_day_total(d, app) for d in recent_days) / 3.0, 1),
                "trend": _trend(app),
            }

        data = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "window_days": days,
            "active_days": len(active_days),
            "total_active_minutes": sum(app_window.values()),
            "apps": apps,
            "active_periods": period_minutes,
            "bili": {
                # 数值是命中的观看记录条数（约等于在该主题上停留的分钟数），不是视频个数
                "subject_minutes": dict(sorted(subj_minutes.items(), key=lambda x: -x[1])),
                "topic_minutes": dict(sorted(topic_minutes.items(), key=lambda x: -x[1])),
                "recent_titles": bili_titles[-30:],
            },
            "_note": (
                "仅为近" + str(days) + "天频率统计快照，每次整体覆写，不影响伴生体行为、不进对话；"
                "确认统计稳定准确前，不作为他的判断依据。subject_minutes 约等于在该学科内容上停留的分钟数。"
            ),
        }

        self.out_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.out_file.with_suffix(self.out_file.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.out_file)
        return data