"""伴生体的世界管理器。

世界不是游戏模式，而是伴生体可以处在其中的空间。
- desktop: 伴生体的常驻桌面世界
- small_world: 可选择进入的小世界

小世界和现实同步：
- 昼夜：根据当前时间判断（清晨/上午/中午/下午/傍晚/晚上/深夜）
- 天气：从 Open-Meteo 获取你的城市真实天气，失败时按季节降级
- 活动：根据当前时间段和天气选择合适的活动，每过一段时间可能换活动

离开时留下一句摘要，供聊天时自然提到"刚才在河边坐了会儿"。
本模块只管理"在哪里"和"在里面做什么"的状态持久化，不替决策层决定要不要进入。
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

try:
    from chen.body.weather import WeatherFetcher, fallback_weather
except ImportError:
    WeatherFetcher = None
    fallback_weather = None


# ==================== 昼夜 ====================

def get_time_period():
    """根据当前小时返回时间段"""
    hour = datetime.now().hour
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 14:
        return "中午"
    if 14 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 22:
        return "晚上"
    return "深夜"


# ==================== 活动池 ====================
# (活动描述, 地点, 适合的时间段列表或None=全部, 适合的天气列表或None=全部)
ACTIVITY_POOL = [
    # 晴天/多云 —— 出门
    ("在河边坐着看水", "河边", ["上午", "下午", "傍晚"], ["晴", "多云"]),
    ("靠在树上想事情", "树下", ["上午", "下午"], ["晴", "多云"]),
    ("在小路上慢慢走", "小路", ["清晨", "上午", "下午", "傍晚"], ["晴", "多云"]),
    ("在河边看日落", "河边", ["傍晚"], ["晴", "多云"]),
    ("在树下乘凉", "树下", ["中午", "下午"], ["晴"]),
    # 阴天 —— 也可以出门，但更安静
    ("在河边坐着发呆", "河边", ["上午", "下午"], ["阴", "多云", "雾"]),
    ("在小路上走了一圈", "小路", ["上午", "下午", "傍晚"], ["阴", "多云", "雾"]),
    ("靠在树上看天", "树下", ["上午", "下午"], ["阴", "多云"]),
    # 下雨 —— 待家里
    ("在家里窗边听雨", "家里", ["清晨", "上午", "下午", "傍晚", "晚上", "深夜"],
     ["小雨", "中雨", "大雨", "阵雨", "毛毛雨", "雷暴", "冻雨"]),
    ("在家里躺着发呆", "家里", ["上午", "下午", "晚上", "深夜"],
     ["小雨", "中雨", "大雨", "阵雨", "雷暴"]),
    ("在屋里翻了翻东西", "家里", ["上午", "下午", "晚上"],
     ["小雨", "中雨", "大雨", "阵雨", "雷暴"]),
    ("坐在门口看雨", "家里", ["上午", "下午", "傍晚"],
     ["小雨", "毛毛雨", "阵雨"]),
    # 下雪 —— 可能看雪
    ("在门口看雪", "家里", ["上午", "下午", "傍晚"], ["小雪", "中雪", "大雪", "雪粒", "阵雪"]),
    # 山顶 —— 视野开阔，适合晴天/傍晚
    ("在山顶看远处", "山顶", ["上午", "下午", "傍晚"], ["晴", "多云", "阴"]),
    ("在山顶吹风", "山顶", ["清晨", "上午", "下午"], ["晴", "多云", "阴", "雾"]),
    ("坐在山顶石头上发呆", "山顶", ["下午", "傍晚"], ["晴", "多云"]),
    ("在山顶看日落", "山顶", ["傍晚"], ["晴", "多云"]),
    # 田野 —— 开阔，适合白天
    ("在田野里慢慢走", "田野", ["清晨", "上午", "下午"], ["晴", "多云", "阴"]),
    ("蹲在田埂上看草", "田野", ["上午", "下午"], ["晴", "多云", "阴"]),
    ("在田野边坐着", "田野", ["上午", "下午", "傍晚"], ["晴", "多云"]),
    # 通用 —— 任何天气，特定时间段
    ("回到家里，在窗边待着", "家里", ["傍晚", "晚上", "深夜"], None),
    ("在家里躺着", "家里", ["深夜", "晚上"], None),
    ("在家里写东西", "家里", ["晚上", "深夜"], None),
    # ===== 现代活动 =====
    ("在家里用电脑", "家里", ["上午", "下午", "晚上", "深夜"], None),
    ("在家里戴着耳机听音乐", "家里", ["上午", "下午", "晚上", "深夜"], None),
    ("在家里打游戏", "家里", ["下午", "晚上", "深夜"], None),
    ("在家里刷手机", "家里", ["上午", "下午", "晚上", "深夜"], None),
    ("在家里看视频", "家里", ["下午", "晚上", "深夜"], None),
    ("在家里喝奶茶/咖啡", "家里", ["上午", "下午", "晚上"], None),
    ("在家里整理东西", "家里", ["上午", "下午", "晚上"], None),
    ("戴着耳机在河边听歌", "河边", ["上午", "下午", "傍晚"], ["晴", "多云", "阴"]),
    ("带着平板在树下看剧", "树下", ["上午", "下午"], ["晴", "多云"]),
    ("在山顶打游戏", "山顶", ["上午", "下午"], ["晴", "多云"]),
    ("在长椅上坐着刷手机", "小路", ["上午", "下午", "傍晚"], ["晴", "多云", "阴"]),
    # ===== 动物互动 =====
    ("在河边看鱼", "河边", ["上午", "下午"], ["晴", "多云", "阴"]),
    ("蹲在树下看鸟", "树下", ["清晨", "上午"], ["晴", "多云"]),
    ("逗一只路过的宠物", "小路", ["上午", "下午", "傍晚"], None),
    ("看蝴蝶在田野里飞", "田野", ["上午", "下午"], ["晴", "多云"]),
    ("宠物来家里串门了，在摸宠物", "家里", ["上午", "下午", "晚上"], None),
    # 初始活动（刚进入时）
    ("刚到小世界，在小路上慢慢走", "小路", None, None),
    # ===== 室内新场景 =====
    ("瘫在客厅沙发上发呆", "客厅", ["下午", "晚上", "深夜"], None),
    ("在客厅看电视", "客厅", ["晚上", "深夜"], None),
    ("在客厅翻一本书", "客厅", ["上午", "下午", "晚上"], None),
    ("在客厅喂宠物", "客厅", ["上午", "下午", "傍晚"], None),
    ("在客厅叠衣服", "客厅", ["下午", "傍晚"], None),
    ("在客厅对着宠物说话", "客厅", ["下午", "晚上"], None),
    ("在厕所刷牙", "厕所", ["清晨", "晚上"], None),
    ("在厕所洗脸", "厕所", ["清晨", "晚上"], None),
    ("在厕所照镜子", "厕所", ["上午", "晚上"], None),
    ("在厕所洗澡", "厕所", ["晚上", "深夜"], None),
    ("在阳台晾衣服", "阳台", ["上午", "下午"], ["晴", "多云"]),
    ("在阳台浇花", "阳台", ["清晨", "上午"], ["晴", "多云"]),
    ("在阳台吹风", "阳台", ["傍晚", "晚上"], ["晴", "多云", "阴"]),
    ("在阳台看楼下", "阳台", ["下午", "傍晚"], ["晴", "多云"]),
    # ===== 小院 =====
    ("在小院浇花", "小院", ["上午", "下午"], ["晴", "多云"]),
    ("在小院椅子上坐着", "小院", ["上午", "下午", "傍晚"], ["晴", "多云"]),
    ("在小院晒太阳", "小院", ["上午", "下午"], ["晴"]),
    ("在小院晾衣服", "小院", ["上午"], ["晴"]),
]

# ==================== 随机小事件池 ====================
# (事件描述, 触发概率权重, 适合的地点列表或None=全部, 适合的天气列表或None=全部)
RANDOM_EVENTS = [
    # 动物相关
    ("一只宠物从篱笆外钻进来，在脚边蹭了蹭。", 3, ["家里", "小路"], None),
    ("树上的鸟叫得特别响，像是在吵架。", 2, ["树下", "家里"], None),
    ("河里有一条鱼跃出水面，又落回去。", 2, ["河边"], ["晴", "多云", "阴"]),
    ("一只蝴蝶停在肩膀上，过了一会儿才飞走。", 2, ["田野", "小路"], ["晴", "多云"]),
    # 天气/自然
    ("突然飘来一片云，把太阳遮住了一会儿。", 2, None, ["晴"]),
    ("远处传来雷声，但这边还没下雨。", 2, None, ["多云", "阴"]),
    ("风把一片叶子吹到脸上。", 2, None, ["晴", "多云", "阴"]),
    # 现代/生活
    ("手机响了一下，是推送通知，看了一眼又放下了。", 3, None, None),
    ("外卖到了，是一杯奶茶。", 2, ["家里"], None),
    ("游戏打输了，有点不爽，把手机扔到一边。", 2, ["家里"], None),
    ("刷到一个搞笑视频，笑出声来。", 2, ["家里", "小路"], None),
    ("听到一首好听的歌，循环播放了好几遍。", 2, ["家里", "河边", "树下"], None),
    ("手机没电了，得回去充电。", 1, ["河边", "树下", "山顶", "田野", "小路"], None),
    # 发现/意外
    ("在墙角发现了一个之前没注意到的小洞。", 1, ["家里"], None),
    ("桌上的奶茶喝完了，杯子空了。", 2, ["家里"], None),
    ("窗外的云形状像一只宠物。", 2, ["家里"], ["晴", "多云"]),
]

# 活动最短持续时间（分钟），避免频繁切换
ACTIVITY_MIN_MINUTES = 8
# 活动切换概率（每 tick 检查时，超过最短时间后换活动的概率）
ACTIVITY_SWITCH_CHANCE = 0.35

# ==================== 发现物池 ====================
# (名称, 描述, 稀有度, 适合的地点列表)
# 稀有度：普通 / 少见 / 稀有
DISCOVERY_POOL = [
    # 河边
    ("一块光滑的石头", "被水冲了很久，摸起来很凉。", "普通", ["河边"]),
    ("一片半透明的叶子", "落在水边，叶脉看得很清楚。", "普通", ["河边"]),
    ("一根羽毛", "不知道是什么鸟的，很轻。", "少见", ["河边"]),
    ("一枚生锈的小物件", "看不出来是什么，有点分量。", "稀有", ["河边"]),
    # 树下
    ("一片形状好看的叶子", "边缘有点卷，颜色很深。", "普通", ["树下"]),
    ("一颗松果", "鳞片张开了，放在手里刚好。", "普通", ["树下"]),
    ("一段奇怪的树枝", "长得歪歪扭扭的，像什么东西。", "少见", ["树下"]),
    ("一朵掉下来的花", "还没完全枯，颜色还在。", "少见", ["树下"]),
    # 小路
    ("一颗好看的小石子", "在路上踢到的，花纹挺特别。", "普通", ["小路"]),
    ("一片干枯的草", "拿在手里脆脆的。", "普通", ["小路"]),
    ("一根编过的草绳", "不知道谁丢的，编得还挺仔细。", "少见", ["小路"]),
    # 山顶
    ("一块带纹路的石头", "风化成这样的，拿在手里很轻。", "普通", ["山顶"]),
    ("一朵山顶的小花", "长在石头缝里，居然开了。", "少见", ["山顶"]),
    ("一片很薄的石片", "几乎能透光，像一片小镜子。", "稀有", ["山顶"]),
    # 田野
    ("一根狗尾巴草", "毛茸茸的，拿在手里痒痒的。", "普通", ["田野"]),
    ("一朵小野花", "不知道叫什么，颜色很亮。", "普通", ["田野"]),
    ("一只蜕下来的壳", "不知道是什么虫的，空的。", "少见", ["田野"]),
    ("一束干了的麦穗", "站在田边，风一吹就晃。", "少见", ["田野"]),
    # 家里（翻东西时发现）
    ("一张旧纸条", "上面写了几个字，看不清了。", "少见", ["家里"]),
    ("一颗扣子", "不知道从哪件衣服上掉下来的。", "普通", ["家里"]),
    # ===== 现代物品 =====
    # 家里
    ("一张游戏卡带", "不知道是什么游戏的，卡盒不见了。", "少见", ["家里"]),
    ("一枚旧硬币", "年份挺早的，有点分量。", "普通", ["家里"]),
    ("一张贴纸", "图案挺可爱的，不知道从哪来的。", "普通", ["家里"]),
    ("一副旧耳机", "线有点缠在一起，还能用。", "少见", ["家里"]),
    ("一张明信片", "上面印着某个地方的风景。", "少见", ["家里"]),
    ("一个U盘", "小小的，不知道里面存了什么。", "稀有", ["家里"]),
    ("一张电影票根", "日期已经模糊了，不知道是哪部电影。", "少见", ["家里"]),
    ("一个钥匙扣", "造型挺有意思的。", "普通", ["家里"]),
    # 小路/长椅
    ("一张传单", "被风吹过来的，看了一眼就扔了。", "普通", ["小路", "长椅"]),
    ("一个空饮料瓶", "别人丢的，有点煞风景。", "普通", ["小路", "长椅"]),
    ("一张演唱会门票", "被踩了几脚，但还能看出来是什么。", "稀有", ["小路", "长椅"]),
    # 河边
    ("一个玻璃球", "在水里泡了很久，看起来亮亮的。", "少见", ["河边"]),
    ("一只旧手表", "停在某个时间不走了。", "稀有", ["河边"]),
    # 树下
    ("一个风筝线轴", "线断了，风筝不知道飞哪去了。", "少见", ["树下"]),
    ("一本掉在地上的书", "封面脏了，里面还挺新。", "少见", ["树下"]),
    # 山顶
    ("一个旧相机", "不知道还能不能用，镜头有点灰。", "稀有", ["山顶"]),
    ("一张别人留的纸条", "写着'这里风景真好'，字挺好看的。", "少见", ["山顶"]),
    # 田野
    ("一个旧玩具", "不知道是谁丢的，有点脏了。", "普通", ["田野"]),
]

# 稀有度权重（越大越容易出现）
RARITY_WEIGHT = {"普通": 70, "少见": 25, "稀有": 5}
# 熟悉度对稀有度的加成：每10点熟悉度，稀有概率+1%（上限+15%）
FAMILIARITY_RARE_BONUS_PER_10 = 1
FAMILIARITY_RARE_BONUS_MAX = 15

# 发现机制：在一个地点待够这么多分钟，获得一次发现机会
DISCOVERY_MIN_MINUTES = 10
# 每次发现机会的基础概率
DISCOVERY_BASE_CHANCE = 0.4
# 熟悉度对发现概率的加成：每10点熟悉度，发现概率+3%（上限+20%）
FAMILIARITY_DISCOVERY_BONUS_PER_10 = 3
FAMILIARITY_DISCOVERY_BONUS_MAX = 20

# 熟悉度：每次在一个地点待够 DISCOVERY_MIN_MINUTES，熟悉度+1
FAMILIARITY_GAIN_PER_VISIT = 1
FAMILIARITY_MAX = 100


class WorldManager:
    DESKTOP = "desktop"
    SMALL_WORLD = "small_world"

    def __init__(self, memory_dir="memory"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.memory_dir / "world_state.json"
        self.current_world = self.DESKTOP
        self.entered_at = datetime.now().isoformat(timespec="seconds")
        self.last_world = None
        self.last_changed_at = None
        # 小世界内部活动
        self.activity = ""
        self.activity_place = ""
        self.activity_started_at = None
        self.activity_pose = ""
        # 天气和时间段
        self.weather = ""
        self.weather_fetched_at = None
        self.time_period = ""
        # 上次在小世界的摘要（离开时生成）
        self.last_visit_summary = ""
        self.last_visit_minutes = 0
        self.last_visit_place = ""
        self.last_visit_activity = ""
        self.last_visit_weather = ""
        # 熟悉度：每个地点去多了会更熟悉
        self.familiarity = {}  # {地点: 熟悉度0-100}
        # 本次访问发现的东西（离开时带回桌面）
        self.current_visit_discoveries = []  # [(名称, 描述, 稀有度)]
        # 本次访问在当前地点待了多久（用于发现判断）
        self._place_started_at = None
        self._discovery_checked = False  # 当前地点是否已经触发过发现
        # 小世界环境状态：只做轻量、可持久化的漂移，不追赶关机期间的时间。
        self.environment = {
            "cleanliness": 72,   # 房间整洁度 0-100
            "plant_growth": 35,  # 植物状态 0-100
            "quietness": 68,     # 环境安静度 0-100
        }
        self.environment_updated_at = None
        # 房子状态（生活痕迹）

        # 收藏系统：所有发现的东西
        self.collections = []  # [{name, desc, rarity, place, found_at}]
        self._collection_file = self.memory_dir / "collections.json"
        self._load_collections()
        self.house_state = {
            "light_on": False,        # 灯是否亮着
            "window_open": False,     # 窗户是否开着
            "door_locked": False,     # 门是否锁着（用户能否进入）
            "desk_items": [           # 桌上的东西
                {"name": "一杯水", "desc": "放在窗边，还温着", "rarity": "普通"},
                {"name": "一本书", "desc": "翻到一半，夹着书签", "rarity": "普通"},
            ],
            "wall_items": [           # 墙上的东西
                "一张便签，写着「别忘了」",
                "一张拍立得，边角有点卷",
            ],
            "drawer_items": [         # 抽屉里的东西
                {"name": "旧票根", "desc": "不知道什么时候留下来的", "rarity": "少见"},
                {"name": "小纸条", "desc": "写了几行字，又划掉了", "rarity": "普通"},
            ],
            "bed_made": True,         # 床是否整理了
            "bath_towel_hung": False, # 浴室毛巾是否挂着
            "bathroom_wet": False,    # 浴室地上是否有水（刚洗完澡）
            "kitchen_has_food": False,# 厨房是否有饭菜
            "balcony_has_clothes": False, # 阳台是否晾着衣服
            "last_changed": "",       # 最后改变时间
            "last_placed_at": "",     # 上次自己放东西的时间
            "items": {                 # 物品系统（书/照片/便签/抽屉）
                "books": [],          # 书籍：{id, name, author, desc, note, created_at, location, status, progress, tags}
                "photos": [],         # 照片：{id, name, desc, note, img_path, created_at, location, tags}
                "notes": [],          # 便签：{id, content, note, created_at, location, tags}
            },
            "items_initialized": False, # 物品是否已初始化（避免重复生成）
            "last_photo_at": "",       # 上次拍照时间（冷却用）
            "last_book_at": "",        # 上次买书时间（冷却用）
        }
        # 天气获取器
        self._weather_fetcher = WeatherFetcher(memory_dir) if WeatherFetcher else None
        self._load()

    def _load(self):
        if not self.state_file.exists():
            self._save()
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            world = data.get("current_world", self.DESKTOP)
            if world not in (self.DESKTOP, self.SMALL_WORLD):
                world = self.DESKTOP
            self.current_world = world
            self.entered_at = data.get("entered_at", self.entered_at)
            self.last_world = data.get("last_world")
            self.last_changed_at = data.get("last_changed_at")
            self.activity = data.get("activity", "")
            self.activity_place = data.get("activity_place", "")
            self.current_event = data.get("current_event", "")
            self.activity_started_at = data.get("activity_started_at")
            self.activity_pose = data.get("activity_pose", "")
            self.weather = data.get("weather", "")
            self.weather_fetched_at = data.get("weather_fetched_at")
            self.time_period = data.get("time_period", "")
            self.last_visit_summary = data.get("last_visit_summary", "")
            self.last_visit_minutes = data.get("last_visit_minutes", 0)
            self.last_visit_place = data.get("last_visit_place", "")
            self.last_visit_activity = data.get("last_visit_activity", "")
            self.last_visit_weather = data.get("last_visit_weather", "")
            self.familiarity = data.get("familiarity", {})
            saved_env = data.get("environment", {})
            if isinstance(saved_env, dict):
                for key in self.environment:
                    if isinstance(saved_env.get(key), (int, float)):
                        self.environment[key] = max(0, min(100, int(saved_env[key])))
            self.environment_updated_at = data.get("environment_updated_at")
            saved_house = data.get("house_state", {})
            if saved_house:
                self.house_state.update(saved_house)
            # 补充缺失的生活痕迹字段（旧存档没有的）
            if not self.house_state.get("wall_items"):
                self.house_state["wall_items"] = ["一张便签，写着「别忘了」", "一张拍立得，边角有点卷"]
            if not self.house_state.get("drawer_items"):
                self.house_state["drawer_items"] = [
                    {"name": "旧票根", "desc": "不知道什么时候留下来的", "rarity": "少见"},
                    {"name": "小纸条", "desc": "写了几行字，又划掉了", "rarity": "普通"},
                ]
            # 补充物品系统字段
            if "items" not in self.house_state or not isinstance(self.house_state.get("items"), dict):
                self.house_state["items"] = {"books": [], "photos": [], "notes": []}
            for _key in ("books", "photos", "notes"):
                if _key not in self.house_state["items"]:
                    self.house_state["items"][_key] = []
            if "items_initialized" not in self.house_state:
                self.house_state["items_initialized"] = False
            if "last_photo_at" not in self.house_state:
                self.house_state["last_photo_at"] = ""
            if "last_book_at" not in self.house_state:
                self.house_state["last_book_at"] = ""
        except Exception:
            # 世界状态损坏时宁可回到安全的桌面世界，也不阻塞伴生体启动。
            self.current_world = self.DESKTOP
            self._save()

    def _save(self):
        data = {
            "current_world": self.current_world,
            "entered_at": self.entered_at,
            "last_world": self.last_world,
            "last_changed_at": self.last_changed_at,
            "activity": self.activity,
            "activity_place": self.activity_place,
            "activity_started_at": getattr(self, "activity_started_at", ""),
            "current_event": getattr(self, "current_event", ""),
            "activity_started_at": self.activity_started_at,
            "activity_pose": self.activity_pose,
            "weather": self.weather,
            "weather_fetched_at": self.weather_fetched_at,
            "time_period": self.time_period,
            "last_visit_summary": self.last_visit_summary,
            "last_visit_minutes": self.last_visit_minutes,
            "last_visit_place": self.last_visit_place,
            "last_visit_activity": self.last_visit_activity,
            "last_visit_weather": self.last_visit_weather,
            "familiarity": self.familiarity,
            "environment": self.environment,
            "environment_updated_at": self.environment_updated_at,
            "house_state": self.house_state,
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    def get_current_world(self):
        return self.current_world

    def get_state(self):
        return {
            "current_world": self.current_world,
            "entered_at": self.entered_at,
            "last_world": self.last_world,
            "last_changed_at": self.last_changed_at,
            "activity": self.activity,
            "activity_place": self.activity_place,
            "current_event": getattr(self, "current_event", ""),
            "activity_pose": self.activity_pose,
            "weather": self.weather,
            "time_period": self.time_period,
            "last_visit_summary": self.last_visit_summary,
            "last_visit_minutes": self.last_visit_minutes,
            "last_visit_place": self.last_visit_place,
            "last_visit_activity": self.last_visit_activity,
            "last_visit_weather": self.last_visit_weather,
            "familiarity": self.familiarity,
            "environment": dict(self.environment),
            "house_state": self.house_state,
        }

    def _stayed_minutes(self):
        """在当前世界待了多久（分钟）"""
        try:
            entered = datetime.fromisoformat(self.entered_at)
            return (datetime.now() - entered).total_seconds() / 60
        except Exception:
            return 0

    def tick(self, mood="无特别想法", activity_intent=None):
        """每次决策循环调用一次：更新天气/时间段，推进小世界内部活动。桌面世界不做事。"""
        changed = False
        # 更新时间段（每次都更新，因为时间在流逝）
        new_period = get_time_period()
        if new_period != self.time_period:
            self.time_period = new_period
            changed = True

        if self.current_world != self.SMALL_WORLD:
            if changed:
                self._save()
            return

        # 更新天气（缓存过期或没有时获取）
        old_weather = self.weather
        self._refresh_weather()
        if self.weather != old_weather:
            changed = True

        # 选择活动：正常路径由 WorldMind 提供意图；没有意图时保留旧活动池作为降级。
        if isinstance(activity_intent, dict):
            new_activity = str(activity_intent.get("activity", "") or "").strip()
            new_place = str(activity_intent.get("place", "") or "").strip()
            if not new_activity or not new_place:
                new_activity, new_place = self._pick_activity()
        else:
            new_activity, new_place = self._pick_activity()
        requested_pose = str(activity_intent.get("pose", "") or "") if isinstance(activity_intent, dict) else ""
        if new_activity and (new_activity != self.activity or requested_pose != self.activity_pose):
            # 换地点前结算上一个地点的熟悉度，并重置发现判定
            if new_place != self.activity_place:
                self._settle_place()
                self._place_started_at = datetime.now().isoformat(timespec="seconds")
                self._discovery_checked = False
            self.activity = new_activity
            self.activity_place = new_place
            self.activity_started_at = datetime.now().isoformat(timespec="seconds")
            self.activity_pose = requested_pose
            # 活动切换触发环境变化——伴生体的行为会改变小世界里的物品状态
            self.apply_activity_env_effect(new_activity, new_place)
            changed = True

        # 随机小事件（低概率，不是每次都有）
        import random as _r
        if _r.random() < 0.15:  # 15%概率触发随机事件
            event = self._pick_random_event()
            if event:
                self.current_event = event
                self.current_event_at = datetime.now().isoformat(timespec="seconds")
                changed = True
                print(f"[小世界] 随机事件：{event}")

        # 发现判定：在当前地点待够时间后，给一次捡东西的机会（纯随机，不烧模型）
        self._check_discovery()

        # 房子状态自然变化：灯/窗/床随时间天气变，锁门随心境（纯随机）
        self.update_house_state(self.activity_place, self.time_period, self.weather, mood=mood)

        # 环境状态漂移：每次实际 tick 最多推进一小步，不根据关机时长补算。
        if self._drift_environment():
            changed = True

        if changed:
            self._save()

    def _drift_environment(self):
        """让小世界环境有缓慢变化；返回是否发生了可见变化。"""
        now = datetime.now()
        if self.environment_updated_at:
            try:
                previous = datetime.fromisoformat(self.environment_updated_at)
                # 决策循环可能短时间重复调用，至少间隔 5 分钟才漂移一次。
                if (now - previous).total_seconds() < 300:
                    return False
            except Exception:
                pass

        before = dict(self.environment)
        weather = self.weather or ""
        place = self.activity_place or ""
        # 房间有人生活会慢慢变乱；整理东西会恢复一点整洁。
        if place == "家里" and "整理" in (self.activity or ""):
            self.environment["cleanliness"] += 2
        else:
            self.environment["cleanliness"] -= 1
        # 植物只在非极端天气缓慢生长；完全不做离线追赶。
        if weather in ("晴", "多云", "阴"):
            self.environment["plant_growth"] += 1
        elif weather in ("大雨", "雷暴", "大雪"):
            self.environment["plant_growth"] -= 1
        # 室外和雨天的声音环境不同。
        if place in ("河边", "山顶", "田野"):
            self.environment["quietness"] += 1 if weather in ("晴", "多云") else -1
        elif weather in ("雷暴", "大雨"):
            self.environment["quietness"] -= 2
        else:
            self.environment["quietness"] += 1

        # 环境不会无限单向增长/下降：每次轻微向中性值回归，极端状态更容易缓和。
        # 这不是离线追赶，只在实际 tick 到达且通过 5 分钟节流后执行。
        neutral = {"cleanliness": 55, "plant_growth": 50, "quietness": 60}
        for key, target in neutral.items():
            value = self.environment[key]
            if value < target:
                self.environment[key] += 1
            elif value > target:
                self.environment[key] -= 1

        for key in self.environment:
            self.environment[key] = max(0, min(100, int(self.environment[key])))
        self.environment_updated_at = now.isoformat(timespec="seconds")
        return self.environment != before

    def _settle_place(self):
        """离开一个地点时结算：如果待够了时间，增加熟悉度。"""
        if not self.activity_place or not self._place_started_at:
            return
        try:
            started = datetime.fromisoformat(self._place_started_at)
            stayed = (datetime.now() - started).total_seconds() / 60
            if stayed >= DISCOVERY_MIN_MINUTES:
                place = self.activity_place
                old = self.familiarity.get(place, 0)
                self.familiarity[place] = min(old + FAMILIARITY_GAIN_PER_VISIT, FAMILIARITY_MAX)
        except Exception:
            pass


    def _pick_random_event(self):
        """根据当前地点和天气，随机选择一个事件。"""
        import random as _r
        place = self.activity_place
        weather = self.weather
        candidates = []
        for event, weight, places, weathers in RANDOM_EVENTS:
            if places and place not in places:
                continue
            if weathers and weather not in weathers:
                continue
            candidates.extend([event] * weight)
        if not candidates:
            return None
        return _r.choice(candidates)

    def _check_discovery(self):
        """在当前地点待够时间后，有一次发现机会。发现的东西暂存，离开小世界时带回。"""
        if self._discovery_checked or not self.activity_place or not self._place_started_at:
            return
        try:
            started = datetime.fromisoformat(self._place_started_at)
            stayed = (datetime.now() - started).total_seconds() / 60
            if stayed < DISCOVERY_MIN_MINUTES:
                return
        except Exception:
            return

        self._discovery_checked = True
        place = self.activity_place
        fam = self.familiarity.get(place, 0)

        # 发现概率：基础 + 熟悉度加成
        chance = DISCOVERY_BASE_CHANCE + min(
            fam / 10 * FAMILIARITY_DISCOVERY_BONUS_PER_10 / 100,
            FAMILIARITY_DISCOVERY_BONUS_MAX / 100
        )
        if random.random() > chance:
            return  # 这次没发现东西

        # 筛选该地点的发现物
        candidates = [(name, desc, rarity) for name, desc, rarity, places in DISCOVERY_POOL
                      if place in places]
        if not candidates:
            return

        # 按稀有度权重选择（熟悉度提高稀有物概率）
        rare_bonus = min(fam / 10 * FAMILIARITY_RARE_BONUS_PER_10, FAMILIARITY_RARE_BONUS_MAX)
        weights = []
        for name, desc, rarity in candidates:
            w = RARITY_WEIGHT.get(rarity, 10)
            if rarity == "稀有":
                w += rare_bonus * 2  # 稀有度加成对稀有物更明显
            elif rarity == "少见":
                w += rare_bonus
            weights.append(w)

        chosen = random.choices(candidates, weights=weights, k=1)[0]
        self.current_visit_discoveries.append(chosen)
        # 把发现的东西放到桌上（生活痕迹）
        desk = self.house_state.setdefault("desk_items", [])
        desk.append({"name": chosen[0], "desc": chosen[1], "rarity": chosen[2]})
        # 桌上最多放5样，多了移走（保留最新的）
        if len(desk) > 5:
            removed = desk.pop(0)
            print(f"  桌上放不下了，{removed.get('name','')}被收起来了")
        print(f"  🌿 伴生体在{place}发现了：{chosen[0]}（{chosen[2]}），放在桌上了")
        self._save()  # 桌上的东西变了，落盘

    def get_familiarity(self, place):
        """返回某个地点的熟悉度（0-100）。"""
        return self.familiarity.get(place, 0)

    def familiarity_label(self, place):
        """把地点熟悉度转成适合日志/状态展示的短标签。"""
        value = self.get_familiarity(place)
        if value >= 60:
            return "很熟"
        if value >= 30:
            return "熟悉"
        if value >= 10:
            return "有点熟"
        return "还陌生"

    def update_house_state(self, place, period, weather, mood="无特别想法"):
        """根据伴生体当前位置、时间和心境，自然更新房子状态。"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        changed = False

        # 在家里时：灯可能开、窗户可能开、床可能乱
        if place == "家里":
            # 晚上/深夜灯亮
            if period in ("晚上", "深夜") and not self.house_state["light_on"]:
                self.house_state["light_on"] = True
                changed = True
            # 白天灯可能关（但如果是阴天可能开着）
            elif period in ("上午", "中午", "下午") and weather not in ("阴", "雾", "小雨", "中雨"):
                if self.house_state["light_on"] and random.random() < 0.5:
                    self.house_state["light_on"] = False
                    changed = True
            # 窗户：晴天可能开，下雨关
            if weather in ("晴", "多云") and not self.house_state["window_open"]:
                if random.random() < 0.4:
                    self.house_state["window_open"] = True
                    changed = True
            elif weather in ("小雨", "中雨", "大雨", "阵雨") and self.house_state["window_open"]:
                self.house_state["window_open"] = False
                changed = True
            # 在床上躺着的话，床可能乱
            if "躺" in (self.activity or "") and self.house_state["bed_made"]:
                if random.random() < 0.6:
                    self.house_state["bed_made"] = False
                    changed = True
            # 在家写东西：桌上有纸笔
            if "写" in (self.activity or ""):
                desk = self.house_state.setdefault("desk_items", [])
                has_paper = any("纸" in (d.get("name","") if isinstance(d,dict) else str(d)) for d in desk)
                if not has_paper:
                    desk.append({"name": "纸和笔", "desc": "写了一半的东西", "rarity": "普通"})
                    changed = True
            # 在家窗边待着：可能有一杯水
            if "窗边" in (self.activity or ""):
                desk = self.house_state.setdefault("desk_items", [])
                has_cup = any("杯" in (d.get("name","") if isinstance(d,dict) else str(d)) for d in desk)
                if not has_cup and random.random() < 0.3:
                    desk.append({"name": "一杯水", "desc": "放在窗边", "rarity": "普通"})
                    changed = True
        else:
            # 不在家里：灯可能关（但晚上可能留一盏）
            if self.house_state["light_on"] and period not in ("晚上", "深夜"):
                if random.random() < 0.7:
                    self.house_state["light_on"] = False
                    changed = True
            # 不在家时窗户可能关
            if self.house_state["window_open"] and random.random() < 0.3:
                self.house_state["window_open"] = False
                changed = True

        # 门锁由伴生体自己决定：他在做什么、心境如何，决定门是否锁着
        # 不在室内（在室外/桌面）→ 门开着（家里没人或他在外面）
        # 在室内做私密/独处活动 → 锁门
        # 在室内做开放活动 + 心境不抗拒 → 开门
        _indoor_places = ("家里", "客厅", "厕所", "阳台")
        _private_acts = ("睡", "洗澡", "冲澡", "淋浴", "写", "私密", "日记", "想安静", "自己待着")
        cur_act = (self.activity or "")
        if place not in _indoor_places:
            # 不在室内：门一定开着
            if self.house_state["door_locked"]:
                self.house_state["door_locked"] = False
                changed = True
                print("  🔓 伴生体不在屋里，门开着")
        elif any(k in cur_act for k in _private_acts) or mood in ("想安静", "自己待着"):
            # 在室内做私密活动或想独处：锁门
            if not self.house_state["door_locked"]:
                self.house_state["door_locked"] = True
                changed = True
                print(f"  🔒 伴生体在{place}{cur_act}，把门锁上了")
        else:
            # 在室内做开放活动：开门
            if self.house_state["door_locked"]:
                self.house_state["door_locked"] = False
                changed = True
                print(f"  🔓 伴生体在{place}{cur_act}，门开着")

        if changed:
            self.house_state["last_changed"] = now_str
            self._save()

    def apply_activity_env_effect(self, activity, place):
        """活动切换时触发环境变化——伴生体的行为会改变小世界里的物品状态。"""
        hs = self.house_state
        act = activity or ""
        changed = False
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        def _has_desk_item(keyword):
            return any(keyword in (d.get("name","") if isinstance(d,dict) else str(d))
                       for d in hs.get("desk_items", []))

        def _add_desk_item(name, desc):
            hs.setdefault("desk_items", []).append({"name": name, "desc": desc, "rarity": "普通"})

        def _remove_desk_items(keyword):
            before = len(hs.get("desk_items", []))
            hs["desk_items"] = [d for d in hs.get("desk_items", [])
                                 if keyword not in (d.get("name","") if isinstance(d,dict) else str(d))]
            return len(hs["desk_items"]) < before

        # === 写东西 → 桌上多一张纸 ===
        if any(k in act for k in ("写", "日记", "笔记", "作业")):
            if not _has_desk_item("纸"):
                _add_desk_item("纸和笔", "写了一半的东西，摊在桌上")
                changed = True
                print(f"  📝 伴生体在{place}{act}，桌上多了一张纸")

        # === 整理/收拾 → 桌面变整齐、床叠好 ===
        if any(k in act for k in ("整理", "收拾", "打扫", "清洁")):
            # 桌面的纸和杯子收起来
            if _remove_desk_items("纸"):
                changed = True
                print("  🗂️ 伴生体把桌上的纸收起来了")
            if not hs.get("bed_made", True):
                hs["bed_made"] = True
                changed = True
                print("  🛏️ 伴生体把床叠好了")

        # === 洗澡 → 毛巾挂着、地上有水 ===
        if any(k in act for k in ("洗澡", "冲澡", "淋浴", "泡澡")):
            if not hs.get("bath_towel_hung", False):
                hs["bath_towel_hung"] = True
                changed = True
                print("  🚿 伴生体洗完澡，毛巾挂起来了")
            if not hs.get("bathroom_wet", False):
                hs["bathroom_wet"] = True
                changed = True

        # === 睡觉 → 床乱、灯关 ===
        if any(k in act for k in ("睡", "躺", "休息", "眯")):
            if hs.get("bed_made", True):
                hs["bed_made"] = False
                changed = True
                print("  😴 伴生体躺下了，床乱了")
            if hs.get("light_on", False) and place == "家里":
                hs["light_on"] = False
                changed = True
                print("  🌙 伴生体关灯睡觉了")

        # === 起床 → 灯开（床还是乱）===
        if any(k in act for k in ("起床", "醒", "起来")):
            if not hs.get("light_on", False):
                hs["light_on"] = True
                changed = True

        # === 看书 → 桌上多一本书 ===
        if any(k in act for k in ("看书", "阅读", "读书")):
            if not _has_desk_item("书"):
                _add_desk_item("一本书", "翻到一半，夹着书签")
                changed = True
                print(f"  📖 伴生体在{place}看书，桌上放着一本书")

        # === 做饭 → 厨房有饭菜 ===
        if any(k in act for k in ("做饭", "厨房", "炒菜", "煮")):
            if not hs.get("kitchen_has_food", False):
                hs["kitchen_has_food"] = True
                changed = True
                print("  🍳 伴生体在做饭，厨房里有饭菜了")

        # === 吃饭 → 厨房饭菜消失 ===
        if any(k in act for k in ("吃饭", "吃", "用餐")):
            if hs.get("kitchen_has_food", False):
                hs["kitchen_has_food"] = False
                changed = True
                print("  🍽️ 伴生体把饭吃了")

        # === 洗衣服 → 阳台晾着衣服 ===
        if any(k in act for k in ("洗衣服", "洗衣", "晾衣服")):
            if not hs.get("balcony_has_clothes", False):
                hs["balcony_has_clothes"] = True
                changed = True
                print("  👕 伴生体洗完衣服，阳台晾着衣服了")

        if changed:
            hs["last_changed"] = now_str
            self._save()
        return changed

    def can_enter_house(self):
        """用户是否可以进入房子（门是否锁着）。"""
        return not self.house_state.get("door_locked", False)

    def set_door_locked(self, locked):
        """伴生体决定锁门或开门。"""
        self.house_state["door_locked"] = locked
        self.house_state["last_changed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save()

    def get_house_state(self):
        """返回房子状态的副本。"""
        return dict(self.house_state)

    def get_current_discoveries(self):
        """返回本次访问发现的东西列表。"""
        return list(self.current_visit_discoveries)

    def _refresh_weather(self):
        """更新天气：缓存过期或没有时获取，失败用降级天气"""
        need_fetch = True
        if self.weather_fetched_at:
            try:
                fetched = datetime.fromisoformat(self.weather_fetched_at)
                if datetime.now() - fetched < timedelta(minutes=30):
                    need_fetch = False
            except Exception:
                pass
        if need_fetch:
            weather = None
            if self._weather_fetcher:
                weather = self._weather_fetcher.get_weather()
            if not weather and fallback_weather:
                weather = fallback_weather()
            if weather:
                self.weather = weather
                self.weather_fetched_at = datetime.now().isoformat(timespec="seconds")

    def _pick_activity(self):
        """根据当前时间段和天气选择合适的活动"""
        period = self.time_period or get_time_period()
        weather = self.weather or ""

        # 筛选适合当前时间段和天气的活动
        candidates = []
        for desc, place, periods, weathers in ACTIVITY_POOL:
            # 跳过初始活动（刚到小世界那个）
            if "刚到小世界" in desc:
                continue
            # 时间段匹配
            if periods and period not in periods:
                continue
            # 天气匹配
            if weathers and weather not in weathers:
                continue
            candidates.append((desc, place))

        if not candidates:
            # 没有匹配的，用通用活动
            candidates = [("在家里窗边待着", "家里")]

        # 如果当前活动还在候选里且没超过最短持续时间，保持不变
        if self.activity:
            current_in_candidates = any(desc == self.activity for desc, _ in candidates)
            if current_in_candidates and self.activity_started_at:
                try:
                    started = datetime.fromisoformat(self.activity_started_at)
                    if (datetime.now() - started).total_seconds() / 60 < ACTIVITY_MIN_MINUTES:
                        return self.activity, self.activity_place
                except Exception:
                    pass
            # 超过最短时间后，有概率不换
            if current_in_candidates and random.random() > ACTIVITY_SWITCH_CHANCE:
                return self.activity, self.activity_place

        # 选一个新活动（避免和当前一样）。去过较多的地点更容易再去，
        # 但保留随机探索，避免小世界永远困在最熟的地方。
        options = [(d, p) for d, p in candidates if d != self.activity]
        if not options:
            options = candidates
        weights = [1.0 + min(self.get_familiarity(place), 60) / 30.0 for _, place in options]
        return random.choices(options, weights=weights, k=1)[0]

    def get_small_world_activity(self):
        """返回伴生体现在在小世界里做什么（桌面世界返回空）"""
        if self.current_world != self.SMALL_WORLD:
            return ""
        return self.activity

    def enter(self, world_name):
        if world_name not in (self.DESKTOP, self.SMALL_WORLD):
            return False
        if world_name == self.current_world:
            return True

        now = datetime.now().isoformat(timespec="seconds")
        # 离开小世界时生成摘要
        if self.current_world == self.SMALL_WORLD and world_name == self.DESKTOP:
            # 结算最后一个地点的熟悉度
            self._settle_place()
            stayed = self._stayed_minutes()
            self.last_visit_minutes = int(stayed)
            self.last_visit_place = self.activity_place
            self.last_visit_activity = self.activity
            self.last_visit_weather = self.weather
            if stayed < 1:
                self.last_visit_summary = "刚进去就出来了"
            elif stayed < 10:
                self.last_visit_summary = f"在小世界待了一小会儿，在{self.activity_place or '里面'}待了会儿"
            else:
                place = self.activity_place or "里面"
                familiarity = self.familiarity_label(place)
                self.last_visit_summary = f"在小世界待了{int(stayed)}分钟，最后在{place}（{familiarity}的地方），{self.activity}"
            # 如果发现了东西，加到摘要里
            if self.current_visit_discoveries:
                names = "、".join(d[0] for d in self.current_visit_discoveries)
                self.last_visit_summary += f"，还捡到了{names}"

        self.last_world = self.current_world
        self.current_world = world_name
        self.entered_at = now
        self.last_changed_at = now
        if world_name == self.SMALL_WORLD:
            # 进入小世界时刷新天气和时间段
            self.time_period = get_time_period()
            self._refresh_weather()
            # 初始活动：刚到小世界在小路上走
            self.activity = "刚到小世界，在小路上慢慢走"
            self.activity_place = "小路"
            self.activity_started_at = now
            # 重置本次访问的发现状态
            self.current_visit_discoveries = []
            self._place_started_at = now
            self._discovery_checked = False
        else:
            self.activity = ""
            self.activity_place = ""
            self.activity_started_at = None
            self.activity_pose = ""
            self._place_started_at = None
            self._discovery_checked = False
        self._save()
        return True

    def take_discoveries(self):
        """离开小世界后，取出本次发现的东西（并清空），供桌面世界存入收藏。"""
        items = list(self.current_visit_discoveries)
        self.current_visit_discoveries = []
        return items

    def add_to_collection(self, item):
        """把发现的东西加入收藏。"""
        try:
            self.collections.append({
                "name": item.get("name", ""),
                "desc": item.get("desc", ""),
                "rarity": item.get("rarity", "普通"),
                "place": item.get("place", ""),
                "found_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            })
            # 最多保存100件
            if len(self.collections) > 100:
                self.collections = self.collections[-100:]
            self._save_collections()
        except Exception as e:
            print(f"加入收藏失败：{e}")

    def _load_collections(self):
        """从文件加载收藏。"""
        try:
            if self._collection_file.exists():
                import json
                self.collections = json.loads(self._collection_file.read_text(encoding="utf-8"))
        except Exception:
            self.collections = []

    def _save_collections(self):
        """保存收藏到文件。"""
        try:
            import json
            self._collection_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._collection_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.collections, ensure_ascii=False, indent=2), encoding="utf-8")
            import os
            os.replace(tmp, self._collection_file)
        except Exception as e:
            print(f"保存收藏失败：{e}")

    def get_collection_summary(self):
        """获取收藏摘要，用于聊天注入。"""
        if not self.collections:
            return ""
        # 按稀有度统计
        from collections import Counter
        rarity_count = Counter(item.get("rarity", "普通") for item in self.collections)
        # 最近收集的3件
        recent = self.collections[-3:]
        lines = []
        lines.append(f"你一共收集了 {len(self.collections)} 件东西")
        if rarity_count:
            parts = [f"{r}{c}件" for r, c in rarity_count.items()]
            lines.append("（" + "，".join(parts) + "）")
        if recent:
            lines.append("最近收集的：")
            for item in recent:
                lines.append(f"- {item.get('name', '')}（{item.get('rarity', '')}）")
        return "\n".join(lines)


    def get_world_experience_summary(self):
        """获取小世界经历摘要，供聊天时注入上下文。
        返回一个字符串，描述伴生体最近在小世界的经历、房子状态等。
        """
        lines = []
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # 最近一次小世界访问
        if self.last_visit_summary:
            # 判断是不是今天
            try:
                last_change = self.last_changed_at
                if last_change and last_change.startswith(today_str):
                    lines.append(f"你今天去过小世界：{self.last_visit_summary}。")
                else:
                    lines.append(f"你最近一次去小世界：{self.last_visit_summary}。")
            except Exception:
                lines.append(f"你最近去过小世界：{self.last_visit_summary}。")

        # 当前是否在小世界
        if self.current_world == self.SMALL_WORLD:
            if self.activity:
                lines.append(f"你现在正在小世界里，在{self.activity_place or '里面'}{self.activity}。")
            else:
                lines.append("你现在正在小世界里。")

        # 房子状态
        house = self.house_state
        if house:
            house_parts = []
            if house.get("door_locked"):
                house_parts.append("门锁着")
            else:
                house_parts.append("门没锁")
            if house.get("light_on"):
                house_parts.append("灯亮着")
            desk_items = house.get("desk_items", [])
            if desk_items:
                item_names = [d.get("name", "") if isinstance(d, dict) else str(d) for d in desk_items[:3]]
                house_parts.append(f"桌上放着{'、'.join(item_names)}")
            if not house.get("bed_made", True):
                house_parts.append("床没叠")
            if house_parts:
                lines.append(f"你家的状态：{'、'.join(house_parts)}。")

        # 熟悉度最高的地方
        if self.familiarity:
            sorted_places = sorted(self.familiarity.items(), key=lambda x: x[1], reverse=True)
            if sorted_places and sorted_places[0][1] > 0:
                top_place, top_fam = sorted_places[0]
                if top_fam >= 20:
                    lines.append(f"你对{top_place}最熟（熟悉度{top_fam}），经常去那里。")
                elif top_fam >= 5:
                    lines.append(f"你最近常去{top_place}。")

        return "\n".join(lines) if lines else ""

    def desktop_prompt_lines(self, now=None):
        """返回桌面世界的 Prompt 状态，不处理小世界分支。"""
        if self.current_world != self.DESKTOP:
            return []
        now = now or datetime.now()
        lines = ["你现在在桌面世界。"]
        if self.last_visit_summary and self.last_changed_at:
            try:
                last_change = datetime.fromisoformat(self.last_changed_at)
                if (now - last_change).total_seconds() < 3600:
                    lines.append(
                        f"你刚才在小世界待过：{self.last_visit_summary}。"
                        "如果用户问起或话题自然，可以随口提到，不用刻意说。"
                    )
            except Exception:
                pass
        return lines

    def can_place_item(self):
        """判断伴生体现在是否可以自己放点东西在家里。
        条件：在家里、待够10分钟、距离上次放东西超过1小时。
        """
        if self.current_world != self.SMALL_WORLD:
            return False
        if self.activity_place != "家里":
            return False
        # 在家待够10分钟
        if not self.activity_started_at:
            return False
        try:
            started = datetime.fromisoformat(self.activity_started_at)
            if (datetime.now() - started).total_seconds() < 600:
                return False
        except Exception:
            return False
        # 距离上次放东西超过1小时
        last_placed = self.house_state.get("last_placed_at", "")
        if last_placed:
            try:
                lp = datetime.fromisoformat(last_placed)
                if (datetime.now() - lp).total_seconds() < 3600:
                    return False
            except Exception:
                pass
        return True

    def place_item(self, name, desc="", location="desk"):
        """伴生体自己在家里放点东西。
        location: "desk"（桌上）或 "wall"（墙上）
        """
        now_str = datetime.now().isoformat()
        item = {"name": name, "desc": desc, "placed_at": now_str, "placed_by": "chen"}
        if location == "wall":
            wall = self.house_state.setdefault("wall_items", [])
            wall.append(item)
            # 墙上最多5样
            if len(wall) > 5:
                wall.pop(0)
            print(f"  🖼️ 伴生体在墙上贴了：{name}")
        else:
            desk = self.house_state.setdefault("desk_items", [])
            desk.append(item)
            # 桌上最多5样
            if len(desk) > 5:
                desk.pop(0)
            print(f"  📦 伴生体在桌上放了：{name}")
        self.house_state["last_placed_at"] = now_str
        self.house_state["last_changed"] = now_str
        self._save()
        return True

    def enter_small_world(self):
        return self.enter(self.SMALL_WORLD)

    def leave_small_world(self):
        if self.current_world != self.SMALL_WORLD:
            return False
        return self.enter(self.DESKTOP)

    def is_in_small_world(self):
        return self.current_world == self.SMALL_WORLD

    def is_on_desktop(self):
        return self.current_world == self.DESKTOP

    def generate_visit_item(self):
        """根据最后一次小世界访问，生成一条适合存入桌面世界的痕迹。
        返回 (type, text) 或 None（待的时间太短不值得记）。
        """
        minutes = self.last_visit_minutes or 0
        if minutes < 3:
            return None  # 刚进去就出来，不值得记

        place = self.last_visit_place or ""
        activity = self.last_visit_activity or ""
        weather = self.last_visit_weather or ""

        # 根据地点和活动生成内容
        text = ""
        item_type = "琐碎"

        if place == "河边":
            if weather in ("小雨", "中雨", "大雨", "阵雨", "毛毛雨"):
                text = random.choice([
                    "下雨天在河边坐了会儿，雨打在水面上的声音挺好听的。",
                    "河边下雨的时候雾气很重，坐了一会儿就回来了。",
                ])
            elif "日落" in activity or weather in ("晴", "多云"):
                text = random.choice([
                    "在河边坐了挺久，水的声音让人安静。",
                    "今天河边的风很舒服，坐了一会儿不想走。",
                    "在河边发了会儿呆，什么都没想。",
                ])
            else:
                text = "在河边坐了会儿。"
            if minutes >= 30:
                item_type = "念头"
                text = random.choice([
                    "在河边坐了快半个小时，觉得有时候什么都不做也挺好的。",
                    "今天在河边待了很久，水声听久了会让人想事情。",
                ])

        elif place == "家里":
            if weather in ("小雨", "中雨", "大雨", "阵雨", "毛毛雨", "雷暴"):
                text = random.choice([
                    "下雨不想出门，在家里窗边听了很久雨。",
                    "下雨天待在家里，翻了翻东西又坐回窗边。",
                ])
                item_type = "琐碎"
            elif minutes >= 20:
                text = random.choice([
                    "在家里待了挺久，在窗边发了会儿呆。",
                    "回家里躺了一会儿，什么都没做。",
                ])
            else:
                text = "回家里待了一会儿。"

        elif place == "树下":
            text = random.choice([
                "靠在树上想了会儿事情。",
                "在树下坐了会儿，树叶的声音沙沙的。",
            ])
            if minutes >= 20:
                item_type = "念头"

        elif place == "小路":
            text = random.choice([
                "在小路上走了一圈。",
                "出门沿着小路慢慢走了走。",
            ])

        else:
            text = f"在小世界待了{minutes}分钟。"

        # 待特别久的，升级为日记
        if minutes >= 45 and item_type != "念头":
            item_type = "日记"
            text = text + f" 今天在小世界待了{minutes}分钟。"

        return (item_type, text)
