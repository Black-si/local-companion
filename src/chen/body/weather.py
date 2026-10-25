"""现实天气获取。

从 Open-Meteo 免费 API 获取你的城市当前天气，不需要 API key。
获取成功后缓存，缓存有效期内不重复请求。
网络失败时返回 None，由调用方降级处理。
"""

import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# 你的城市经纬度
LATITUDE = 25.04
LONGITUDE = 102.71

# 缓存有效期（分钟）
CACHE_MINUTES = 30
MAX_RESPONSE_BYTES = 128 * 1024

# Open-Meteo weather_code → 中文天气
WEATHER_CODE_MAP = {
    0: "晴",
    1: "多云",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "毛毛雨",
    56: "冻雨",
    57: "冻雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "阵雪",
    95: "雷暴",
    96: "雷暴",
    99: "雷暴",
}


class WeatherFetcher:
    def __init__(self, cache_dir="memory"):
        self.cache_file = Path(cache_dir) / "weather_cache.json"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

    def get_weather(self):
        """返回当前天气字符串，如"晴""小雨"。失败返回 None。"""
        # 先看缓存
        cached = self._read_cache()
        if cached:
            return cached["weather"]

        # 请求 API
        weather = self._fetch()
        if weather:
            self._write_cache(weather)
            return weather
        return None

    def _fetch(self):
        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={LATITUDE}&longitude={LONGITUDE}"
                f"&current=temperature_2m,weather_code"
                f"&timezone=Asia%2FShanghai"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "ChenChen/0.3"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    return None
                data = json.loads(raw.decode("utf-8"))
            code = data.get("current", {}).get("weather_code", -1)
            weather = WEATHER_CODE_MAP.get(code, "未知")
            if weather == "未知":
                return None
            return weather
        except Exception:
            return None

    def _read_cache(self):
        if not self.cache_file.exists():
            return None
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(data["fetched_at"])
            if datetime.now() - fetched_at < timedelta(minutes=CACHE_MINUTES):
                return data
        except Exception:
            pass
        return None

    def _write_cache(self, weather):
        data = {
            "weather": weather,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self.cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


# 降级用：按季节随机生成合理天气
def fallback_weather():
    month = datetime.now().month
    if month in (12, 1, 2):  # 冬
        return ["晴", "多云", "阴"][hash(datetime.now().strftime("%Y%m%d")) % 3]
    elif month in (3, 4, 5):  # 春
        return ["晴", "多云", "小雨", "阴"][hash(datetime.now().strftime("%Y%m%d")) % 4]
    elif month in (6, 7, 8):  # 夏
        return ["晴", "多云", "阵雨", "小雨", "雷暴"][hash(datetime.now().strftime("%Y%m%d")) % 5]
    else:  # 秋
        return ["晴", "多云", "阴", "小雨"][hash(datetime.now().strftime("%Y%m%d")) % 4]
