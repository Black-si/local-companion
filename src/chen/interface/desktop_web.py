# -*- coding: utf-8 -*-
"""虚拟桌面的受控互联网入口。

这里做的是“搜索摘要层”，不是把任意网页内容塞进伴生体的上下文：

* 搜索只读取 DuckDuckGo HTML 结果页中的标题、摘要和链接；
* 结果会清理控制字符并截断，避免网页文本直接变成 UI/提示词指令；
* DeepSeek 只整理已经得到的结果，不自动打开网页、不写入聊天历史；
* 用户点击后才调用系统默认浏览器打开链接。
"""
from __future__ import annotations

import html
import re
import threading
import urllib.parse
import urllib.request
from html.parser import HTMLParser


MAX_RESPONSE_BYTES = 768 * 1024
MAX_QUERY_CHARS = 80
MAX_RESULTS = 8
TITLE_MAX = 100
SNIPPET_MAX = 220


def _clean(value, limit):
    text = html.unescape(str(value or ""))
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class _DuckParser(HTMLParser):
    """只解析搜索结果的有限字段，不执行或保留网页脚本。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._current = None
        self._capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            href = attrs.get("href") or ""
            self._current = {"url": href, "title": "", "snippet": "", "source": "DuckDuckGo"}
            self._capture = "title"
        elif tag in ("a", "div") and "result__snippet" in classes and self._current:
            self._capture = "snippet"

    def handle_data(self, data):
        if self._current and self._capture:
            self._current[self._capture] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._current and self._capture == "title":
            self._current["title"] = _clean(self._current.get("title"), TITLE_MAX)
            self._capture = None
        elif self._current and self._capture == "snippet" and tag in ("a", "div"):
            self._current["snippet"] = _clean(self._current.get("snippet"), SNIPPET_MAX)
            self._finish()

    def _finish(self):
        if not self._current:
            return
        item = self._current
        self._current = None
        self._capture = None
        url = str(item.get("url") or "").strip()
        title = _clean(item.get("title"), TITLE_MAX)
        if not title or not url:
            return
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith(("http://", "https://")):
            return
        item["url"] = url[:600]
        item["title"] = title
        item["snippet"] = _clean(item.get("snippet"), SNIPPET_MAX)
        self.results.append(item)


class DesktopWebService:
    """桌面主动搜索和 DeepSeek 整理服务。"""

    def __init__(self, chen=None):
        self.chen = chen

    def search(self, query, *, limit=MAX_RESULTS):
        query = _clean(query, MAX_QUERY_CHARS)
        if len(query) < 2:
            return []
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ChenChenDesktop/0.3 (+local desktop search)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("搜索结果过大，已停止读取")
        parser = _DuckParser()
        parser.feed(raw.decode("utf-8", errors="replace"))
        output = []
        seen = set()
        for item in parser.results:
            key = item.get("url")
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
            if len(output) >= max(1, min(int(limit), MAX_RESULTS)):
                break
        return output

    def summarize(self, query, results, *, mode="browser"):
        api = getattr(self.chen, "llm_api", None)
        local = getattr(self.chen, "llm", None)
        if not api or api is local or not getattr(api, "api_key", None):
            return "DeepSeek 暂未配置，先查看下面的搜索结果。"
        rows = []
        for index, item in enumerate(list(results or [])[:MAX_RESULTS], 1):
            rows.append(
                f"{index}. {item.get('title', '')}\n"
                f"摘要：{item.get('snippet', '')}\n"
                f"链接：{item.get('url', '')}"
            )
        if not rows:
            return "没有可整理的搜索结果。"
        if mode == "music":
            task = "从结果中整理适合继续试听或查找的音乐/歌手/专辑线索，避免编造不存在的歌曲。"
        else:
            task = "从结果中整理主题、可信的共同点和适合继续查看的方向，明确说出不确定之处。"
        prompt = (
            f"用户在伴生体自己的虚拟桌面搜索：{_clean(query, MAX_QUERY_CHARS)}\n"
            f"{task}\n"
            "只依据提供的标题和摘要，不要声称已经打开或验证网页。"
            "用简短中文分成‘看到的’和‘可以继续’两段，控制在180字以内。\n\n"
            + "\n\n".join(rows)
        )
        return str(api.generate(
            "你是伴生体虚拟桌面的互联网整理助手。只做摘要，不代替用户点击网页。",
            prompt,
            record_history=False,
            use_history=False,
            max_tokens=260,
        ) or "").strip() or "这次没有整理出摘要。"


def run_search(service, query, on_done, on_error=None, *, limit=MAX_RESULTS):
    """后台执行搜索，避免网络请求阻塞 Qt 主线程。"""
    def worker():
        try:
            on_done(service.search(query, limit=limit))
        except Exception as exc:
            if on_error:
                on_error(str(exc)[:180])

    threading.Thread(target=worker, daemon=True).start()


def run_summary(service, query, results, on_done, on_error=None, *, mode="browser"):
    """后台调用 DeepSeek，只传搜索摘要和链接。"""
    def worker():
        try:
            on_done(service.summarize(query, results, mode=mode))
        except Exception as exc:
            if on_error:
                on_error(str(exc)[:180])

    threading.Thread(target=worker, daemon=True).start()

