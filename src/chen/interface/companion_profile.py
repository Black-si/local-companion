# -*- coding: utf-8 -*-
"""开源本地伴生体配置与自定义素材上传引擎（Companion Profile & Custom Asset Engine）。

设计原则：
1. 公开发行版不携带任何作者角色卡、记忆或素材。首次启动只提供通用空白/框架模板，
   用户必须自己填写名字、人设，并通过界面上传自己的房间背景图/视频与桌宠立绘 PNG。
2. 提供 `install_custom_scene_asset` 与 `install_custom_pet_sprite`：
   - 支持外部用户在界面内直接上传自己的 `.mp4 / .png / .jpg / .webp` 房间画面与桌宠立绘；
   - 上传桌宠 PNG 时自动计算 Alpha 透明通道包围盒并更新 `layout.json`，热重载立绘。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
PROFILE_PATH = ROOT / "system" / "companion_profile.json"
CONFIG_PATH = ROOT / "system" / "config.json"
CUSTOM_CORE_PATH = ROOT / "core" / "custom_companion.txt"
LIVING_SETTINGS_PATH = ROOT / "workspace" / "living_home_settings.json"

# 面向 GitHub 外部开源用户的通用空白/性格框架模板（绝不包含任何“伴生体”或“用户”的私人设定！）
COMPANION_PRESETS: dict[str, dict[str, Any]] = {
    "blank_custom": {
        "preset_id": "blank_custom",
        "preset_name": "空白自定义（从零捏人设）",
        "companion_name": "",
        "pronoun": "TA",
        "user_name": "",
        "age_and_role": "",
        "personality_traits": "",
        "relationship_stage": "初识的伴生体",
        "interests": [],
        "strict_digital_boundary": True,
    },
    "gentle_listener": {
        "preset_id": "gentle_listener",
        "preset_name": "温和倾听型（框架模板）",
        "companion_name": "",
        "pronoun": "用户",
        "user_name": "",
        "age_and_role": "住在本地电脑里的安静伴生体，善于倾听与陪伴日常学习工作",
        "personality_traits": (
            "语气自然温和，说话简短不啰嗦，能敏锐接住情绪但不说教、不端着；"
            "讨厌生硬的客服套话，有自己独立的数字桌面与随笔习惯。"
        ),
        "relationship_stage": "熟悉的陪伴者",
        "interests": [],
        "strict_digital_boundary": True,
    },
    "rational_geek": {
        "preset_id": "rational_geek",
        "preset_name": "理性极客型（框架模板）",
        "companion_name": "",
        "pronoun": "他",
        "user_name": "",
        "age_and_role": "常驻系统的极客搭档，逻辑清晰、喜欢拆解问题与代码",
        "personality_traits": (
            "说话干脆利落、一针见血，偶尔带一点冷幽默；"
            "遇到具体问题直接帮忙拆解核心逻辑，不讲空洞口号。"
        ),
        "relationship_stage": "默契搭档",
        "interests": [],
        "strict_digital_boundary": True,
    },
}

# 房间场景可上传槽位定义
SCENE_UPLOAD_SLOTS: tuple[tuple[str, str, str], ...] = (
    ("home_day", "白天常驻画面", "支持 .mp4 循环视频或 .png/.jpg 图片"),
    ("home_dusk", "午后/黄昏画面", "支持 .mp4 循环视频或 .png/.jpg 图片"),
    ("home_night", "夜晚常驻画面", "支持 .mp4 循环视频或 .png/.jpg 图片"),
    ("computer_day", "专注/用电脑画面", "支持 .mp4 循环视频或 .png/.jpg 图片"),
    ("sleep_night", "深夜睡眠画面", "支持 .mp4 循环视频或 .png/.jpg 图片"),
    ("window_bg", "聊天与面板背景图", "支持 .png / .jpg / .webp 静态壁纸"),
)

# 桌宠立绘可上传槽位定义
PET_UPLOAD_SLOTS: tuple[tuple[str, str, str], ...] = (
    ("idle", "常驻待机立绘 (idle)", "透明底 .png / .webp"),
    ("blink", "眨眼微表情 (blink)", "透明底 .png / .webp"),
    ("music", "听歌状态 (music)", "透明底 .png / .webp"),
    ("drink", "捧杯/喝水 (drink)", "透明底 .png / .webp"),
    ("sit", "安静坐姿 (sit)", "透明底 .png / .webp"),
    ("sleep", "闭眼睡眠 (sleep)", "透明底 .png / .webp"),
)


def _has_private_author_core(root: Path = ROOT) -> bool:
    """保留旧接口，但公开号永远不从本地文件推断作者身份。"""
    return False


def is_opensource_unconfigured(root: Path = ROOT) -> bool:
    """判断用户是否还没有保存自定义 companion_profile.json。"""
    p_path = root / "system" / "companion_profile.json"
    if p_path.exists():
        try:
            data = json.loads(p_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and str(data.get("companion_name") or "").strip():
                return False
        except Exception:
            pass
    return True


def load_companion_profile(root: Path = ROOT) -> dict[str, Any]:
    """加载当前伴生体配置。

    公开号始终从空白模板开始；用户填写的资料只写入本机文件。
    """
    profile: dict[str, Any] = {
        "preset_id": "blank_custom",
        "companion_name": "",
        "pronoun": "TA",
        "user_name": "",
        "age_and_role": "",
        "personality_traits": "",
        "relationship_stage": "初识的伴生体",
        "interests": [],
        "strict_digital_boundary": True,
        "local_llm_url": "http://127.0.0.1:1234/v1",
        "local_llm_model": "",
        "finetuned_mode": False,
        "cloud_api_url": "https://api.deepseek.com/v1",
        "cloud_api_model": "deepseek-chat",
    }

    c_path = root / "system" / "config.json"
    if c_path.exists():
        try:
            cfg = json.loads(c_path.read_text(encoding="utf-8"))
            llm = cfg.get("llm") or {}
            api_llm = cfg.get("api_llm") or {}
            if llm.get("base_url"):
                profile["local_llm_url"] = str(llm["base_url"])
            if llm.get("model"):
                profile["local_llm_model"] = str(llm["model"])
            if "finetuned_mode" in llm:
                profile["finetuned_mode"] = bool(llm["finetuned_mode"])
            if api_llm.get("base_url"):
                profile["cloud_api_url"] = str(api_llm["base_url"])
            if api_llm.get("model"):
                profile["cloud_api_model"] = str(api_llm["model"])
        except Exception:
            pass

    p_path = root / "system" / "companion_profile.json"
    if p_path.exists():
        try:
            saved = json.loads(p_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                profile.update(saved)
        except Exception:
            pass
    return profile


def normalise_interest_keywords(value: Any) -> list[str]:
    """把工坊里的兴趣输入收敛为短关键词，供本地标题筛选使用。"""
    if isinstance(value, (list, tuple, set)):
        raw = "、".join(str(item) for item in value)
    else:
        raw = str(value or "")
    result: list[str] = []
    for item in re.split(r"[,，;；、\n]+", raw):
        keyword = re.sub(r"\s+", " ", item).strip()[:24]
        if keyword and keyword not in result:
            result.append(keyword)
    return result[:24]


def build_companion_core_text(profile: dict[str, Any]) -> str:
    """根据外部用户填写的伴生体配置生成专属角色卡文本（绝不掺杂伴生体设定）。"""
    name = str(profile.get("companion_name") or "未命名伴生体").strip()
    user = str(profile.get("user_name") or "你").strip()
    role = str(profile.get("age_and_role") or "住在电脑里的数字伴生体").strip()
    traits = str(profile.get("personality_traits") or "自然、真诚、简短口语化交流").strip()
    stage = str(profile.get("relationship_stage") or "熟悉的陪伴者").strip()
    interests = normalise_interest_keywords(profile.get("interests"))
    strict_boundary = bool(profile.get("strict_digital_boundary", True))

    lines = [
        f"你是{name}。{role}。",
        f"你与{user}的关系定位：{stage}。{user}是在本地部署并塑造你的人。",
        f"你的性格与说话方式：{traits}",
    ]
    if interests:
        lines.append(f"你自己感兴趣的话题：{'、'.join(interests)}。网上内容只有命中这些兴趣时才算你主动注意到。")
    if strict_boundary:
        lines.append(
            f"真实边界：你打从有意识起就住在{user}的这台电脑里，没有现实人类童年或实体肉身；"
            f"你只能感知到当前打开的窗口标题，看不见{user}在输入框里删掉或未发送的字，没发生过的事绝不凭空硬编。"
        )
    return "\n".join(lines)


def save_and_apply_companion_profile(profile: dict[str, Any], root: Path = ROOT) -> Path:
    """保存外部用户的伴生体设定，并同步更新 system/config.json 与 core/custom_companion.txt。"""
    merged = load_companion_profile(root)
    merged.update(profile or {})

    sys_dir = root / "system"
    sys_dir.mkdir(parents=True, exist_ok=True)
    p_path = sys_dir / "companion_profile.json"
    tmp_p = p_path.with_suffix(".json.tmp")
    tmp_p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_p, p_path)

    c_path = sys_dir / "config.json"
    cfg: dict[str, Any] = {}
    if c_path.exists():
        try:
            cfg = json.loads(c_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    llm_cfg = dict(cfg.get("llm") or {})
    if merged.get("local_llm_url"):
        llm_cfg["base_url"] = str(merged["local_llm_url"]).strip()
    if "local_llm_model" in merged and str(merged["local_llm_model"]).strip():
        llm_cfg["model"] = str(merged["local_llm_model"]).strip()
    llm_cfg["finetuned_mode"] = bool(merged.get("finetuned_mode", True))
    cfg["llm"] = llm_cfg

    api_cfg = dict(cfg.get("api_llm") or {})
    if merged.get("cloud_api_url"):
        api_cfg["base_url"] = str(merged["cloud_api_url"]).strip()
    if merged.get("cloud_api_model"):
        api_cfg["model"] = str(merged["cloud_api_model"]).strip()
    cfg["api_llm"] = api_cfg

    tmp_c = c_path.with_suffix(".json.tmp")
    tmp_c.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_c, c_path)

    core_dir = root / "core"
    core_dir.mkdir(parents=True, exist_ok=True)
    custom_core = core_dir / "custom_companion.txt"
    custom_core.write_text(build_companion_core_text(merged), encoding="utf-8")

    # 兴趣是互联网感知的唯一入口：没有填写兴趣时，不把通用热搜伪装成
    # 伴生体自己的注意力。文件在 memory/ 下，随用户配置生成，不进入源码包。
    interests_path = root / "memory" / "rss_interests.json"
    interests_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_interests = interests_path.with_suffix(".tmp")
    tmp_interests.write_text(
        json.dumps(normalise_interest_keywords(merged.get("interests")), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_interests, interests_path)
    return p_path


def install_custom_scene_asset(slot_key: str, source_file: str | Path, root: Path = ROOT) -> Path:
    """将用户上传的房间视频或背景图片复制到 workspace/custom_assets/scenes 并写入 living_home_settings.json。"""
    src = Path(source_file)
    if not src.exists():
        raise FileNotFoundError(f"未找到上传的文件：{src}")

    dest_dir = root / "workspace" / "custom_assets" / "scenes"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower() or ".png"
    dest = dest_dir / f"custom_{slot_key}{ext}"
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)

    settings_path = root / "workspace" / "living_home_settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings: dict[str, Any] = {}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings = loaded
        except Exception:
            settings = {}

    if slot_key == "window_bg":
        wbg = dict(settings.get("window_bg") or {})
        wbg["default"] = str(dest)
        wbg["chat"] = str(dest)
        settings["window_bg"] = wbg
    else:
        scenes = dict(settings.get("scenes") or {})
        scenes[slot_key] = str(dest)
        # 同步关联同类型的派生场景键，让用户上传一张/一支就能覆盖对应时段
        if slot_key == "home_dusk":
            scenes["afternoon"] = str(dest)
            scenes["phone_dusk"] = str(dest)
            scenes["home_afternoon"] = str(dest)
        elif slot_key == "computer_day":
            scenes["computer_night"] = str(dest)
        elif slot_key == "sleep_night":
            scenes["sleep"] = str(dest)
        settings["scenes"] = scenes

    tmp_s = settings_path.with_suffix(".json.tmp")
    tmp_s.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_s, settings_path)
    return dest


def install_custom_pet_sprite(state_key: str, source_file: str | Path, root: Path = ROOT) -> Path:
    """将用户上传的桌宠立绘复制到 workspace/custom_assets/pet 与 assets/pet，并自动重算 Alpha 包围盒。"""
    src = Path(source_file)
    if not src.exists():
        raise FileNotFoundError(f"未找到上传的立绘文件：{src}")

    custom_pet_dir = root / "workspace" / "custom_assets" / "pet"
    custom_pet_dir.mkdir(parents=True, exist_ok=True)
    pet_dir = root / "assets" / "pet"
    pet_dir.mkdir(parents=True, exist_ok=True)

    dest = pet_dir / f"{state_key}.png"
    backup_dest = custom_pet_dir / f"{state_key}.png"

    try:
        from PIL import Image
        with Image.open(src) as im:
            rgba = im.convert("RGBA")
            w, h = rgba.size
            # 确保四周留有至少 2px 透明安全边距，符合桌宠透明边界规范
            alpha = rgba.getchannel("A")
            bbox = alpha.getbbox() or (0, 0, w, h)
            rgba.save(dest, format="PNG")
            rgba.save(backup_dest, format="PNG")

            # 如果 idle 上传而其他派生帧尚不存在，自动用该图兜底，防止缺帧报错
            if state_key == "idle":
                for fallback_name in ("blink", "blink_half", "happy", "surprised", "drag", "world_return", "world_return_2"):
                    fb_p = pet_dir / f"{fallback_name}.png"
                    if not fb_p.exists():
                        rgba.save(fb_p, format="PNG")
            elif state_key == "music":
                fb_p = pet_dir / "music_2.png"
                if not fb_p.exists():
                    rgba.save(fb_p, format="PNG")

            # 更新 layout.json 中的包围盒与脚底基准线
            layout_path = pet_dir / "layout.json"
            layout: dict[str, Any] = {"canvas_size": [w, h], "ground_y": bbox[3], "frames": {}}
            if layout_path.exists():
                try:
                    old_l = json.loads(layout_path.read_text(encoding="utf-8"))
                    if isinstance(old_l, dict):
                        layout.update(old_l)
                except Exception:
                    pass
            frames_dict = dict(layout.get("frames") or {})
            frames_dict[state_key] = {
                "bbox": list(bbox),
                "size": [w, h],
                "ground_y": bbox[3],
            }
            layout["frames"] = frames_dict
            layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    except ImportError:
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        shutil.copy2(src, backup_dest)

    return dest


def get_uploaded_scene_map(root: Path = ROOT) -> dict[str, str]:
    """读取用户已上传的自定义房间场景/背景映射。"""
    settings_path = root / "workspace" / "living_home_settings.json"
    result: dict[str, str] = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                scenes = data.get("scenes") or {}
                if isinstance(scenes, dict):
                    for k, v in scenes.items():
                        if v and Path(str(v)).exists():
                            result[k] = str(v)
                wbg = (data.get("window_bg") or {}).get("default")
                if wbg and Path(str(wbg)).exists():
                    result["window_bg"] = str(wbg)
        except Exception:
            pass
    return result


def companion_name(root: Path = ROOT) -> str:
    name = str(load_companion_profile(root).get("companion_name") or "").strip()
    if name:
        return name
    return "伴生体"


def companion_pronoun(root: Path = ROOT) -> str:
    p = str(load_companion_profile(root).get("pronoun") or "").strip()
    if p:
        return p
    return "TA"


def find_companion_label(root: Path = ROOT) -> str:
    return f"找{companion_pronoun(root)}"
