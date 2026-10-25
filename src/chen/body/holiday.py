# -*- coding: utf-8 -*-
"""
伴生体的节假日感知
================
他住在用户电脑里，能看到周几，但不知道法定假、调休补班、寒暑假。
这个模块把 system/holidays.json 翻译成"今天用户要不要上学"。

优先级：寒暑假 > 法定假 > 调休补班 > 周末 > 普通上学日
纯本地查表，零 API、零网络。每年底国务院发下一年安排后改 json 即可。
"""
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from chen.paths import PROJECT_ROOT


class Holiday:
    def __init__(self, data_dir):
        self.file = Path(data_dir) / "holidays.json"
        self.data = self._load()

    def _load(self):
        try:
            if self.file.exists():
                return json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"holidays": {}, "makeup_workdays": {}, "school_breaks": {}}

    # ---------- 判定 ----------

    def _in_break(self, d):
        """d 是 date；在寒暑假区间里返回 'winter'/'summer'，否则 None。"""
        breaks = self.data.get("school_breaks") or {}
        for name, span in breaks.items():
            try:
                start = datetime.strptime(span[0], "%Y-%m-%d").date()
                end = datetime.strptime(span[1], "%Y-%m-%d").date()
                if start <= d <= end:
                    return name
            except Exception:
                continue
        return None

    def day_info(self, now=None):
        """返回今天的节假日/上学状态。
        {
          type: 'winter_break'|'summer_break'|'holiday'|'makeup_workday'|'weekend'|'workday',
          label: 给人看的一句话（'国庆假期第4天'、'今天周日但要补国庆的课'），
          holiday_name: 节日名（没有则空），
          is_school_day: bool（今天用户该不该上学），
          days_into_holiday: 长假第几天（从1开始，否则0），
          days_left: 长假还剩几天（否则0），
        }
        """
        now = now or datetime.now()
        d = now.date()
        key = d.strftime("%Y-%m-%d")
        wday = d.weekday()  # 0=周一
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][wday]

        # 1) 寒暑假
        br = self._in_break(d)
        if br:
            name = "寒假" if br == "winter" else "暑假"
            return {
                "type": br,
                "label": f"正在{name}",
                "holiday_name": name,
                "is_school_day": False,
                "days_into_holiday": 0,
                "days_left": 0,
            }

        # 2) 法定节假日
        hname = (self.data.get("holidays") or {}).get(key)
        if hname:
            # 算这是长假第几天 / 还剩几天
            days_into, days_left = self._holiday_span(d, hname)
            return {
                "type": "holiday",
                "label": f"{hname}假" + (f"第{days_into}天" if days_into else ""),
                "holiday_name": hname,
                "is_school_day": False,
                "days_into_holiday": days_into,
                "days_left": days_left,
            }

        # 3) 调休补班（周末要上学）
        makeup = (self.data.get("makeup_workdays") or {}).get(key)
        if makeup:
            return {
                "type": "makeup_workday",
                "label": f"今天{weekday_cn}，但要{makeup}",
                "holiday_name": "",
                "is_school_day": True,
                "days_into_holiday": 0,
                "days_left": 0,
            }

        # 4) 普通周末
        if wday >= 5:
            return {
                "type": "weekend",
                "label": f"今天{weekday_cn}，周末",
                "holiday_name": "",
                "is_school_day": False,
                "days_into_holiday": 0,
                "days_left": 0,
            }

        # 5) 普通上学日
        return {
            "type": "workday",
            "label": f"今天{weekday_cn}，上学日",
            "holiday_name": "",
            "is_school_day": True,
            "days_into_holiday": 0,
            "days_left": 0,
        }

    def _holiday_span(self, d, hname):
        """d 在名为 hname 的假期里，是第几天、还剩几天。"""
        days = sorted(
            datetime.strptime(k, "%Y-%m-%d").date()
            for k, v in (self.data.get("holidays") or {}).items()
            if v == hname
        )
        if not days:
            return 0, 0
        start, end = days[0], days[-1]
        into = (d - start).days + 1
        left = (end - d).days
        return max(into, 0), max(left, 0)

    # ---------- 给 prompt 的一行 ----------

    def prompt_line(self, now=None):
        """注入到聊天 prompt 的一行。没特别情况就不占 token。"""
        info = self.day_info(now)
        t = info["type"]
        if t == "holiday":
            h = info["holiday_name"]
            left = info["days_left"]
            line = f"今天是{h}假期"
            if info["days_into_holiday"]:
                line += f"第{info['days_into_holiday']}天"
            if left == 0:
                line += "，今天最后一天，明天收假"
            elif left == 1:
                line += "，明天收假"
            return line + "。"
        if t == "makeup_workday":
            return f"今天虽是周末，但要{info['holiday_name'] or '补班'}，用户得上学。"
        if t in ("winter_break", "summer_break"):
            return f"现在是{info['holiday_name']}，用户不用上学。"
        # 周末和普通上学日不啰嗦——他自己知道周几
        return ""


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    h = Holiday(PROJECT_ROOT / "system")
    # 今天
    print("今天:", json.dumps(h.day_info(), ensure_ascii=False))
    print("prompt 行:", h.prompt_line())
    print()
    # 几个关键日期
    for s in ["2026-10-04", "2026-10-07", "2026-10-10", "2026-10-12",
              "2026-09-20", "2026-02-15", "2026-07-20", "2026-01-15"]:
        d = datetime.strptime(s, "%Y-%m-%d")
        info = h.day_info(d)
        print(f"{s} ({['周一','周二','周三','周四','周五','周六','周日'][d.weekday()]}): "
              f"{info['label']} | 上学={info['is_school_day']} | prompt: {h.prompt_line(d)}")
