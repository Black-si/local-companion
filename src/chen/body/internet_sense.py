# -*- coding: utf-8 -*-
"""
InternetSense —— 伴生体的互联网感知 V1（白名单 RSS → 本地兴趣筛选 → 感知事件）
==============================================================================
分工原则："Python 先得到信息，本地筛选，绝大多数到不了伴生体"。

V1 全程无 LLM：
- 只抓白名单 RSS 源（memory/rss_feeds.json，可增删），不浏览、不抓正文、不执行任何网页内容
- 本地按兴趣关键词筛选（memory/rss_interests.json）：命中的才是"他注意到的"
- 已见链接去重（memory/rss_seen.json，封顶 500 条）
- 命中的事件写进按天经历日志（kind="web"），晚上被每日消化一起酿成念头
- 每 2 小时检查一次；失败 15 分钟后重试，不刷屏

安全：网页标题可能带恶意文本，入库前剥掉换行/控制字符并截断。
"他要不要在聊天里提到"由以后的注意力门决定（V2），V1 只感知不搭话。
"""
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

CHECK_INTERVAL_MIN = 120   # 检查间隔
RETRY_MINUTES = 15         # 失败后重试
SEEN_MAX = 500             # 已见链接上限
TITLE_MAX = 80             # 标题截断（也防注入超长）
MAX_FEED_BYTES = 1024 * 1024

# 默认白名单（保守起步，用户可在 memory/rss_feeds.json 增删）
DEFAULT_FEEDS = [
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage"},
    {"name": "少数派", "url": "https://sspai.com/feed"},
    {"name": "V2EX", "url": "https://www.v2ex.com/index.xml"},
]

# 默认兴趣关键词（他会"注意到"什么；可在 memory/rss_interests.json 增删）
DEFAULT_INTERESTS = ["AI", "人工智能", "大模型", "游戏", "Steam", "编程",
                     "Python", "音乐", "心理学", "桌游"]


class InternetSense:
    """白名单 RSS 感知器。maybe_run() 到点才抓，返回本次新注意到的事件列表。"""

    def __init__(self, memory_dir):
        d = Path(memory_dir)
        self.feeds_file = d / "rss_feeds.json"
        self.interests_file = d / "rss_interests.json"
        self.seen_file = d / "rss_seen.json"
        self.state_file = d / "internet_sense.json"
        self.state = {"last_check": "", "last_try": ""}
        self._load_jsons()

    # ---------- 配置与状态 ----------

    def _read_json(self, path, default):
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return data
        except Exception:
            pass
        return default

    def _write_json(self, path, data):
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            pass

    def _load_jsons(self):
        self.feeds = self._read_json(self.feeds_file, None) or DEFAULT_FEEDS
        self.interests = self._read_json(self.interests_file, None) or DEFAULT_INTERESTS
        self.seen = self._read_json(self.seen_file, []) or []
        st = self._read_json(self.state_file, {})
        if isinstance(st, dict):
            self.state.update(st)

    def _mark_seen(self, links):
        self.seen.extend(links)
        del self.seen[:-SEEN_MAX]
        self._write_json(self.seen_file, self.seen)

    # ---------- 主入口 ----------

    def maybe_run(self, now=None):
        """到点且冷却完 → 抓全部白名单源并筛选。返回新事件列表 [{title,link,source}]；
        没到点/失败返回 None（失败不更新 last_check，冷却后重试）。"""
        now = now or datetime.now()
        try:
            last = datetime.fromisoformat(self.state.get("last_check", ""))
            if (now - last).total_seconds() < CHECK_INTERVAL_MIN * 60:
                return None
        except Exception:
            pass
        try:
            last_try = datetime.fromisoformat(self.state.get("last_try", ""))
            if (now - last_try).total_seconds() < RETRY_MINUTES * 60:
                return None
        except Exception:
            pass

        self.state["last_try"] = now.isoformat(timespec="seconds")
        self._write_json(self.state_file, self.state)

        items = self._fetch_all()
        if items is None:
            return None  # 抓取失败，下轮重试

        events = []
        new_seen = []
        for it in items:
            if it["link"] and it["link"] in self.seen:
                continue
            if it["link"]:
                new_seen.append(it["link"])
            if self._is_notable(it["title"]):
                events.append(it)
        self._mark_seen(new_seen)
        self.state["last_check"] = now.isoformat(timespec="seconds")
        self._write_json(self.state_file, self.state)
        return events

    # ---------- 抓取与解析 ----------

    def _fetch_all(self):
        """抓所有白名单源，合并条目。全部失败返回 None；部分失败忽略。"""
        all_items = []
        any_ok = False
        for feed in self.feeds:
            url = (feed or {}).get("url", "")
            name = (feed or {}).get("name", "")
            if not url:
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ChenChen/0.4"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    raw = resp.read(MAX_FEED_BYTES + 1)
                if len(raw) > MAX_FEED_BYTES:
                    continue
                items = self._parse(raw)
                for it in items:
                    it["source"] = name
                all_items.extend(items)
                any_ok = True
            except Exception:
                continue  # 单个源失败不影响其他源
        return all_items if any_ok else None

    @staticmethod
    def _parse(raw):
        """极简 RSS/Atom 解析，只取 title + link。"""
        items = []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return items
        # RSS 2.0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title:
                items.append({"title": InternetSense._sanitize(title), "link": link})
        if items:
            return items
        # Atom
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("a:title", namespaces=ns) or "").strip()
            link = ""
            for le in entry.iter("{http://www.w3.org/2005/Atom}link"):
                if le.get("href"):
                    link = le.get("href")
                    break
            if title:
                items.append({"title": InternetSense._sanitize(title), "link": link})
        return items

    @staticmethod
    def _sanitize(title):
        """剥控制字符/换行并截断——网页标题可能带恶意文本，入库前清洗。"""
        t = re.sub(r"[\x00-\x1f]", " ", str(title))
        t = re.sub(r"\s+", " ", t).strip()
        return t[:TITLE_MAX]

    # ---------- 本地筛选 ----------

    def _is_notable(self, title):
        """命中任一兴趣关键词才算"他注意到的"。V1 纯本地，无 LLM。"""
        t = title or ""
        for kw in self.interests:
            if kw and kw in t:
                return True
        return False


if __name__ == "__main__":
    import tempfile
    from pathlib import Path as P

    tmp = P(tempfile.mkdtemp())
    # 造两个假 RSS 源
    feed_a = tmp / "a.xml"
    feed_a.write_text("""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>A</title>
    <item><title>某 AI 大模型发布新版本</title><link>http://a/1</link></item>
    <item><title>明星八卦新闻</title><link>http://a/2</link></item>
    <item><title>带\n换行和控制符的 AI 标题</title><link>http://a/3</link></item>
    </channel></rss>""", encoding="utf-8")
    feed_b = tmp / "b.xml"
    feed_b.write_text("""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><title>B</title>
    <entry><title>Python 新版本发布</title><link href="http://b/1"/></entry>
    <entry><title>无关紧要的事</title><link href="http://b/2"/></entry>
    </feed>""", encoding="utf-8")

    sense = InternetSense(tmp)
    sense.feeds = [{"name": "源A", "url": feed_a.as_uri()},
                   {"name": "源B", "url": feed_b.as_uri()}]

    from datetime import datetime as dt
    events = sense.maybe_run(now=dt(2026, 10, 1, 20, 0))
    print("本次注意到:", [e["title"] for e in (events or [])])
    assert events and len(events) == 3, "应命中3条兴趣（AI x2 + Python）"
    # 换行清洗
    assert all("\n" not in e["title"] for e in events)
    # 去重：同时间再跑不该重复（但间隔未到会返回 None，模拟 3 小时后）
    events2 = sense.maybe_run(now=dt(2026, 10, 1, 23, 0))
    assert events2 == [], f"重复了: {events2}"
    print("去重 OK")
    # 关键词未命中不进来
    assert not any("八卦" in e["title"] or "无关紧要" in e["title"] for e in events)
    print("筛选 OK")
    print("全部通过")
