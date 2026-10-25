"""后台记忆维护与每日反思协调。"""

import json
import os
import threading
from datetime import datetime


class MemoryMaintenance:
    """协调记忆层的低频维护任务，不改变记忆算法本身。"""

    def __init__(self, chen):
        self.chen = chen

    def maybe_daily_maintain(self):
        """每天一次的记忆轻量整理（合并相似、清理碎片、衰减临时记忆，不调LLM）。"""
        if not self.chen.memory:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        # 启动整理和心跳整理共享一个日期标记，避免同一天重复跑一遍
        # memory.daily_maintain。保留对旧实例的回退，方便独立测试和旧调用方。
        last_file = getattr(self.chen, "last_maintain_file", None)
        if last_file is None:
            last_file = self.chen.base_dir / "memory" / "last_maintain.json"
        try:
            if last_file.exists():
                rec = json.loads(last_file.read_text(encoding="utf-8"))
                if rec.get("date") == today:
                    return  # 今天已经整理过了
            stats = self.chen.memory.daily_maintain()
            if stats:
                print(f"[记忆整理] 合并{stats.get('merged', 0)}条，清理{stats.get('pruned', 0)}条")
            last_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = last_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps({"date": today}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_file, last_file)
        except Exception as e:
            print(f"每日记忆整理异常：{e}")

    def maybe_weekly_consolidate(self):
        """
        每周一次的记忆深度整理（用一次LLM把稳定事实归纳进长期记忆）。
        距上次成功满7天才跑；模型没就绪导致失败时，次日才再试，不反复重试。
        在独立后台线程执行，不阻塞决策循环；任何异常都不影响主程序。
        """
        if not (self.chen.memory and self.chen.llm_api) or getattr(self.chen, "_weekly_running", False):
            return
        f = self.chen.base_dir / "memory" / "last_weekly_consolidate.json"
        today = datetime.now().date()
        old_ok = None
        try:
            if f.exists():
                rec = json.loads(f.read_text(encoding="utf-8"))
                old_ok = rec.get("date")
                last_try = rec.get("attempt") or old_ok
                if old_ok and (today - datetime.strptime(old_ok, "%Y-%m-%d").date()).days < 7:
                    return
                if last_try and (today - datetime.strptime(last_try, "%Y-%m-%d").date()).days < 1:
                    return
        except Exception:
            pass

        self.chen._weekly_running = True

        def _write(rec):
            try:
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, f)
            except Exception:
                pass

        def _job():
            today_s = datetime.now().strftime("%Y-%m-%d")
            try:
                print("🧠 距上次深度整理已满7天，开始把稳定记忆归纳进长期记忆...")
                stats = self.chen.memory.weekly_consolidation(self.chen.llm_api)
                if stats is None:
                    _write({"date": old_ok, "attempt": today_s})
                    print("   本周记忆整理未完成（模型可能未就绪），明天再试")
                else:
                    print(f"   沉淀长期记忆 {stats['promoted']} 条、合并 {stats['merged']} 条、归档 {stats['discarded']} 条")
                    _write({"date": today_s, "attempt": today_s})
            except Exception as e:
                _write({"date": old_ok, "attempt": today_s})
                print(f"   [警告] 每周记忆整理出错（不影响使用）：{e}")
            finally:
                self.chen._weekly_running = False

        threading.Thread(target=_job, daemon=True).start()

    def maybe_daily_reflection(self):
        """
        每晚自动做一次当天反思（21点后、今天还没反思、模型就绪，后台线程跑）。
        今天已反思（含“无新增”）就跳过；模型没就绪失败则下轮重试，无需手动反复点。
        """
        if not (self.chen.memory and self.chen.llm_api) or getattr(self.chen, "_reflect_running", False):
            return
        now = datetime.now()
        if not (21 <= now.hour <= 23):
            return
        today = now.strftime("%Y-%m-%d")
        try:
            if self.chen.memory._today_reflection_body(today) is not None:
                return
        except Exception:
            pass

        self.chen._reflect_running = True

        def _job():
            try:
                print("🌙 到晚上了，自动整理今天的反思…")
                result = self.chen.daily_reflection(force=False)
                if result and result != "already":
                    print("   今日反思已自动保存")
                elif result is None:
                    print("   今天没有新的了解，不生成反思")
            except Exception as e:
                print(f"   [警告] 自动反思失败（稍后再试）：{e}")
            finally:
                self.chen._reflect_running = False

        threading.Thread(target=_job, daemon=True).start()

    def daily_reflection(self, force=False):
        """触发每日反思（记忆系统第二层），包含当天活动汇总。
        force=False 时今天已反思过会直接返回 'already'，不重复生成。"""
        if self.chen.memory and self.chen.llm_api:
            # 获取当天活动摘要（感知层）
            activity_summary = ""
            if self.chen.perception:
                activity_summary, _ = self.chen.perception.get_daily_summary()
                # 附上今天在B站看的视频标题，帮助沉淀“用户最近在补哪科”这类理解
                bili = self.chen.perception.get_bilibili_watch_summary()
                if bili:
                    activity_summary = (activity_summary + "\n" + bili).strip()
            return self.chen.memory.daily_reflection(
                self.chen.llm_api, activity_summary=activity_summary, force=force
            )
        return None

