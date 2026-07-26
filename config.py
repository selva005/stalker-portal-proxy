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
        self.listing_cache_ttl_seconds = float(os.environ.get("LISTING_CACHE_TTL_SECONDS", "600"))


try:
    config = Config()
except ConfigError as e:
    print(f"Configuration error: {e}", file=sys.stderr)
    sys.exit(1)
