# -*- coding: utf-8 -*-
"""
热搜/热点获取模块 —— 轻量级互联网接入。
只获取标题，不获取内容，避免被污染。
支持多源：抖音热搜（主）、B站热门（备）、百度热搜（备）。
获取后存到文件，由决策层判断伴生体是否注意到、是否在聊天中提到。
"""

import json
import os
import random
import urllib.request
from datetime import datetime
from pathlib import Path


# 各源API
SOURCES = {
    "douyin": [
        "https://api.vvhan.com/api/hotlist/douyin",
        "https://api.oioweb.cn/api/common/douyinHot",
    ],
    "bilibili": [
        "https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1",
    ],
    "baidu": [
        "https://top.baidu.com/api/board?platform=wise&tab=realtime",
    ],
}

# 缓存文件
CACHE_FILE = "hot_trends.json"
# 缓存有效期（分钟）
CACHE_TTL_MINUTES = 60
MAX_RESPONSE_BYTES = 1024 * 1024


class HotTrends:
    """热搜获取和管理。"""

    def __init__(self, memory_dir):
        self.cache_path = Path(memory_dir) / CACHE_FILE
        self.data = self._load_cache()
        # 伴生体的兴趣标签（会从自我状态/记忆中读取）
        self.interests = ["游戏", "音乐", "科技", "动漫", "电影", "搞笑", "美食", "宠物"]

    def _load_cache(self):
        try:
            if self.cache_path.exists():
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                return data
        except Exception:
            pass
        return {"items": [], "source": "", "updated_at": ""}

    def _save_cache(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.cache_path)
        except Exception:
            pass

    def _is_cache_fresh(self):
        if not self.data.get("updated_at"):
            return False
        try:
            updated = datetime.strptime(self.data["updated_at"], "%Y-%m-%d %H:%M")
            return (datetime.now() - updated).total_seconds() < CACHE_TTL_MINUTES * 60
        except Exception:
            return False

    def _fetch_url(self, url, timeout=10):
        """请求URL，返回解析后的JSON或None。"""
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw_bytes) > MAX_RESPONSE_BYTES:
                    return None
                raw = raw_bytes.decode("utf-8")
                return json.loads(raw)
        except Exception as e:
            print(f"[热搜] 请求失败 {url}: {e}")
            return None

    def _parse_douyin(self, result):
        """解析抖音热搜。"""
        titles = []
        if isinstance(result, dict):
            data = result.get("data", result)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        title = item.get("title") or item.get("name") or item.get("word")
                        if title:
                            titles.append(str(title)[:50])
            elif isinstance(data, dict):
                for item in data.get("list", []):
                    if isinstance(item, dict):
                        title = item.get("title") or item.get("name") or item.get("word")
                        if title:
                            titles.append(str(title)[:50])
        return titles

    def _parse_bilibili(self, result):
        """解析B站热门。"""
        titles = []
        if isinstance(result, dict) and result.get("code") == 0:
            data = result.get("data", {})
            for item in data.get("list", []):
                title = item.get("title")
                if title:
                    titles.append(str(title)[:50])
        return titles

    def _parse_baidu(self, result):
        """解析百度热搜。"""
        titles = []
        if isinstance(result, dict):
            data = result.get("data", {})
            for card in data.get("cards", []):
                for item in card.get("content", []):
                    title = item.get("word") or item.get("query")
                    if title:
                        titles.append(str(title)[:50])
        return titles

    def fetch(self, force=False):
        """获取热搜（多源容错）。
        优先抖音，失败用B站，再失败用百度。
        返回热搜标题列表。
        """
        if not force and self._is_cache_fresh():
            return self.data.get("items", [])

        # 1. 尝试抖音
        for url in SOURCES["douyin"]:
            result = self._fetch_url(url)
            if result:
                titles = self._parse_douyin(result)
                if titles:
                    self._save(titles, "抖音")
                    return titles

        # 2. 尝试B站
        for url in SOURCES["bilibili"]:
            result = self._fetch_url(url)
            if result:
                titles = self._parse_bilibili(result)
                if titles:
                    self._save(titles, "B站")
                    return titles

        # 3. 尝试百度
        for url in SOURCES["baidu"]:
            result = self._fetch_url(url)
            if result:
                titles = self._parse_baidu(result)
                if titles:
                    self._save(titles, "百度")
                    return titles

        print("[热搜] 所有源都失败，使用缓存")
        return self.data.get("items", [])

    def _save(self, titles, source):
        """保存到缓存。"""
        seen = set()
        unique = []
        for t in titles:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        self.data["items"] = unique[:20]
        self.data["source"] = source
        self.data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save_cache()
        print(f"[热搜] 获取{source}热搜 {len(unique)} 条")

    def get_relevant(self, interests=None, limit=3):
        """根据兴趣筛选相关热搜。
        interests: 兴趣关键词列表；省略时使用当前伴生体配置，空列表表示不注入通用热搜
        limit: 返回条数
        返回 (相关热搜列表, 来源)
        """
        all_hot = self.data.get("items", [])
        if not all_hot:
            return [], self.data.get("source", "")

        interests = self.interests if interests is None else interests

        # 按兴趣关键词匹配
        relevant = []
        for title in all_hot:
            for interest in interests:
                if interest in title:
                    relevant.append(title)
                    break

        return relevant[:limit], self.data.get("source", "")

    def get_random(self, limit=2):
        """随机取几条热搜（他随便刷到的）。"""
        all_hot = self.data.get("items", [])
        if not all_hot:
            return [], self.data.get("source", "")
        selected = random.sample(all_hot, min(limit, len(all_hot)))
        return selected, self.data.get("source", "")

    def get_all(self, limit=10):
        """获取所有热搜。"""
        return self.data.get("items", [])[:limit], self.data.get("source", "")
