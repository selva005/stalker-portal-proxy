"""Builds an M3U playlist from Stalker-Portal channel/genre/profile data."""
import logging

from config import Config

logger = logging.getLogger("m3u")

INTRO_URL = "https://tg-aadi.vercel.app/intro.m3u8"


def _info_row(name: str, logo: str, group: str, value: str) -> list:
    return [
        f'#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name} • {value}',
        INTRO_URL,
    ]


def build_m3u(
    config: Config,
    channels: list,
    genres: list,
    profile: dict,
    account_info: dict,
    origin: str,
    client_ip: str,
) -> str:
    lines = [
        "#EXTM3U",
        f"# Total Channels => {len(channels)}",
        "",
    ]

    lines += _info_row(
        "IP", "https://img.icons8.com/?size=160&id=OWj5Eo00EaDP&format=png",
        "Portal | Info", profile.get("ip", "Unknown"),
    )
    lines += _info_row(
        "User IP",
        "https://uxwing.com/wp-content/themes/uxwing/download/location-travel-map/ip-location-color-icon.svg",
        "Portal | Info", client_ip or "Unknown",
    )
    lines += _info_row(
        "Portal", "https://upload.wikimedia.org/wikipedia/commons/6/6f/IPTV.png?20180223064625",
        "Portal | Info", config.host,
    )
    lines += _info_row(
        "Created", "https://cdn-icons-png.flaticon.com/128/1048/1048953.png",
        "Portal | Info", profile.get("created", "Unknown"),
    )
    lines += _info_row(
        "Expire",
        "https://www.citypng.com/public/uploads/preview/hand-drawing-clipart-14-feb-calendar-icon-701751694973910ds70zl0u9u.png",
        "Portal | Info", account_info.get("end_date", "Unknown"),
    )
    lines += _info_row(
        "Tariff Plan", "https://img.lovepik.com/element/45004/5139.png_300.png",
        "Portal | Info", account_info.get("tariff_plan", "Unknown"),
    )

    max_online = "Unknown"
    storages = profile.get("storages") or {}
    if storages:
        first_storage = next(iter(storages.values()))
        max_online = first_storage.get("max_online", "Unknown")
    lines += _info_row(
        "Max Online",
        "https://thumbs.dreamstime.com/b/people-vector-icon-group-symbol-illustration-businessman-logo-multiple-users-silhouette-153484048.jpg?w=1600",
        "Portal | Info", str(max_online),
    )

    group_title_map = {g.get("id"): g.get("title", "Other") for g in genres}

    if not channels:
        logger.info("No channels found")
    else:
        for index, item in enumerate(channels):
            name = item.get("name", "Unknown")
            cmd = item.get("cmd", "")
            real_cmd = cmd.replace("ffrt http://localhost/ch/", "") if cmd else ""
            if not real_cmd:
                real_cmd = "unknown"
                logger.debug("Invalid or empty cmd for channel #%d: %s", index, name)
            tvg_id = item.get("xmltv_id", "")
            genre_id = item.get("tv_genre_id", "")
            group_title = group_title_map.get(genre_id, "Other")
            logo = item.get("logo", "")
            logo_url = (
                f"http://{config.host}/stalker_portal/misc/logos/320/{logo}" if logo else ""
            )
            lines.append(
                f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" tvg-logo="{logo_url}" '
                f'group-title="{group_title}",{name}'
            )
            lines.append(f"{origin}/{real_cmd}.m3u8")

    return "\n".join(lines)
