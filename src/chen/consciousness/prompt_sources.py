"""System prompt 所需的静态意识资料读取器。"""

import re
from pathlib import Path


class PromptSources:
    """读取角色卡、语气样本、世界书和最近反思，并按文件修改时间缓存。"""

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self._file_cache = {}
        self._struct_cache = {}

    def _read_cached(self, path):
        try:
            path = Path(path)
            stat = path.stat()
            key = str(path)
            hit = self._file_cache.get(key)
            if hit and hit[0] == stat.st_mtime:
                return hit[1]
            text = path.read_text(encoding="utf-8")
            self._file_cache[key] = (stat.st_mtime, text)
            return text
        except Exception:
            return Path(path).read_text(encoding="utf-8")

    def load_personality(self, finetuned_mode=False):
        """加载核心人格文件。若外部开源用户在伴生体工坊配置了自定义人设，优先注入自定义角色卡。"""
        core_dir = self.base_dir / "core"
        try:
            import json as _json
            prof_p = self.base_dir / "system" / "companion_profile.json"
            custom_p = core_dir / "custom_companion.txt"
            if prof_p.exists() and custom_p.exists():
                prof = _json.loads(prof_p.read_text(encoding="utf-8"))
                if isinstance(prof, dict) and (
                    prof.get("preset_id", "chen_default") != "chen_default"
                    or prof.get("companion_name", "伴生体") != "伴生体"
                    or prof.get("user_name", "用户") != "用户"
                ):
                    return self._read_cached(custom_p).strip()
        except Exception:
            pass
        if finetuned_mode:
            ft_path = core_dir / "finetuned_core.txt"
            if ft_path.exists():
                return self._read_cached(ft_path).strip()
        parts = []
        for filename in [
            "identity.txt",
            "personality.txt",
            "personality_layer.txt",
            "relationship.txt",
            "speech.txt",
            "self.txt",
        ]:
            file_path = core_dir / filename
            if file_path.exists():
                parts.append(self._read_cached(file_path))
        return "\n\n".join(parts)

    def load_conversation_examples(self, user_message=""):
        """按关键词筛选单轮示例，并保留多轮节奏示例。"""
        examples_path = self.base_dir / "core" / "conversation_examples.txt"
        if not examples_path.exists():
            return ""

        text = self._read_cached(examples_path)
        try:
            examples_mtime = examples_path.stat().st_mtime
        except Exception:
            examples_mtime = None
        cache_key = ("examples", examples_mtime)
        if cache_key in self._struct_cache:
            example_map, multi_text = self._struct_cache[cache_key]
        else:
            parts = text.split("====多轮对话示例")
            single_text = parts[0]
            multi_text = "====多轮对话示例" + parts[1] if len(parts) > 1 else ""
            blocks = re.split(r'(?=^示例\d+：)', single_text, flags=re.MULTILINE)
            example_map = {}
            for block in blocks:
                block = block.strip()
                match = re.match(r'^示例(\d+)：', block)
                if match:
                    example_map[match.group(1)] = block
            self._struct_cache[cache_key] = (example_map, multi_text)

        chosen = ["1"]
        rules = [
            (["好不爽", "不爽", "气死", "有病", "神经病", "脑残", "崩溃", "吐血", "炸了", "受不了了"], ["19", "20"]),
            (["哭", "难受", "想哭", "烦死", "好烦", "累", "难过", "委屈"], ["2", "3"]),
            (["作业", "考试", "成绩", "数学", "化学", "英语", "物理", "上课", "老师", "班主任", "学校"], ["4", "5"]),
            (["你最近", "你在干嘛", "你在做什么", "你都看到", "你今天", "你平时", "你干嘛", "你干啥", "你都在"], ["6"]),
            (["刚刚", "刚才", "前面", "上面", "我说了", "跟你说过", "重复", "详细", "具体"], ["12"]),
            (["不知道", "你知道吗", "你认识", "你了解"], ["8"]),
            (["附近", "哪里", "去哪", "怎么走", "推荐", "哪家"], ["9"]),
            (["搞砸", "弄坏", "我完了", "犯错", "做错", "完蛋", "闯祸", "摔碎", "弄丢"], ["7"]),
            (["你错了", "说错了", "不是这样", "你记错", "记错了", "不对", "你烦", "不想理你", "别烦我"], ["11"]),
            (["谢谢", "感谢", "谢了"], ["10"]),
            (["考了", "进步", "开心", "高兴", "太好了", "终于"], ["14"]),
            (["我妈", "我爸", "我家", "骂我", "我家人"], ["13"]),
            (["你猜", "猜猜", "猜一下"], ["15"]),
            (["你看到", "看到我", "我在干啥", "我在干嘛", "看我在"], ["16"]),
            (["伴生体!", "伴生体！", "！！", "!!", "喂", "在吗"], ["17"]),
            (["成功了", "申请成功", "我跟你说", "好消息", "告诉你", "终于成"], ["18", "14"]),
        ]
        for keywords, names in rules:
            if any(keyword in user_message for keyword in keywords):
                for name in names:
                    if name not in chosen:
                        chosen.append(name)

        picked = [example_map[name] for name in chosen if name in example_map][:3]
        result_parts = []
        if picked:
            result_parts.append("====单轮对话示例（参考语气和节奏）====\n" + "\n\n---\n\n".join(picked))
        if multi_text:
            multi_lines = multi_text.strip().split("\n")
            multi_clean = "\n".join(line for line in multi_lines if not line.startswith("（这段对话展示"))
            result_parts.append(multi_clean.strip())
        return "\n\n".join(result_parts) if result_parts else ""

    def load_lorebook(self, user_message=""):
        """按关键词加载世界书条目。"""
        lorebook_path = self.base_dir / "core" / "lorebook.txt"
        if not lorebook_path.exists():
            return ""
        text = self._read_cached(lorebook_path)
        entries = re.split(r'(?=^===条目\d+：)', text, flags=re.MULTILINE)
        matched = []
        for entry in entries:
            entry = entry.strip()
            if not entry.startswith("===条目"):
                continue
            keywords_match = re.search(r'关键词：(.+)', entry)
            if not keywords_match:
                continue
            keywords = [keyword.strip() for keyword in keywords_match.group(1).split(',')]
            if any(keyword in user_message for keyword in keywords):
                content_match = re.search(r'内容：\n(.+)', entry, re.DOTALL)
                if content_match:
                    content = content_match.group(1).strip()
                    title_match = re.match(r'===条目\d+：(.+?)===', entry)
                    title = title_match.group(1) if title_match else "相关设定"
                    matched.append(f"【{title}】\n{content}")
        return "====世界书（当前话题相关设定）====\n" + "\n\n---\n\n".join(matched) if matched else ""

    def load_latest_reflection(self):
        """读取 daily_summary.txt 的最后一条反思。"""
        summary_path = self.base_dir / "memory" / "daily_summary.txt"
        if not summary_path.exists():
            return ""
        summary_content = self._read_cached(summary_path)
        blocks = re.split(r'\n(\d{4}-\d{2}-\d{2})：\n', summary_content)
        if len(blocks) >= 3:
            last_date = blocks[-2]
            last_reflection = blocks[-1].strip()
            if last_reflection and "暂无记录" not in last_reflection and "今天没有新的了解" not in last_reflection:
                return f"====最近的反思====\n[{last_date}] {last_reflection}"
        return ""
