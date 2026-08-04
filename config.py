"""Environment-based configuration for the Stalker-Portal client."""
import os
import sys


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


class Config:
    def __init__(self):
        self.host = _require("STALKER_HOST").removeprefix("https://").removeprefix("http://").rstrip("/")
        self.mac_address = _require("STALKER_MAC")
        self.serial_number = _require("STALKER_SERIAL")
        self.device_id = os.environ.get("STALKER_DEVICE_ID", "").strip()
        self.device_id_2 = os.environ.get("STALKER_DEVICE_ID2", "").strip()
        self.stb_type = os.environ.get("STALKER_STB_TYPE", "MAG250")
        self.api_signature = os.environ.get("STALKER_API_SIGNATURE", "263")
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.blocked_category_names = [
            name.strip().lower()
            for name in os.environ.get("BLOCKED_CATEGORY_NAMES", "").split(",")
            if name.strip()
        ]
        self.listing_cache_ttl_seconds = float(os.environ.get("LISTING_CACHE_TTL_SECONDS", "21600"))
        self.prewarm_delay_seconds = float(os.environ.get("PREWARM_DELAY_SECONDS", "0.3"))
        self.prewarm_vod_series_pages = int(os.environ.get("PREWARM_VOD_SERIES_PAGES", "3"))
        self.prewarm_rate_limit_backoff_seconds = float(
            os.environ.get("PREWARM_RATE_LIMIT_BACKOFF_SECONDS", "30")
        )
        self.prewarm_max_consecutive_rate_limits = int(
            os.environ.get("PREWARM_MAX_CONSECUTIVE_RATE_LIMITS", "5")
        )
        self.cache_file_path = os.environ.get("CACHE_FILE_PATH", "/data/cache.json")
        self.epg_file_path = os.environ.get("EPG_FILE_PATH", "/data/epg.xml")
        self.epg_hours = int(os.environ.get("EPG_HOURS", "24"))
        self.epg_publish_every_n_channels = int(os.environ.get("EPG_PUBLISH_EVERY_N_CHANNELS", "200"))
        self.epg_freshness_ttl_seconds = float(
            os.environ.get("EPG_FRESHNESS_TTL_SECONDS", str(self.listing_cache_ttl_seconds))
        )


try:
    config = Config()
except ConfigError as e:
    print(f"Configuration error: {e}", file=sys.stderr)
    sys.exit(1)
