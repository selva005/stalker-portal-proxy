"""Builds an XMLTV document from Stalker-Portal channel and EPG data."""
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring


def _format_xmltv_time(unix_timestamp: str) -> str:
    dt = datetime.fromtimestamp(int(unix_timestamp), tz=timezone.utc)
    return dt.strftime("%Y%m%d%H%M%S +0000")


def build_xmltv(channels: list[dict], programs_by_channel: dict[str, list[dict]]) -> bytes:
    """Build an XMLTV `<tv>` document.

    `channels` are entries from get_all_channels (must have `xmltv_id`, `name`).
    `programs_by_channel` maps a channel's `id` (not `xmltv_id`) to its get_short_epg
    program list. Channels without an `xmltv_id` are skipped; channels with no programs
    are still included as an empty `<channel>` entry.
    """
    tv = Element("tv")

    for channel in channels:
        xmltv_id = channel.get("xmltv_id")
        if not xmltv_id:
            continue
        channel_el = SubElement(tv, "channel", {"id": xmltv_id})
        display_name = SubElement(channel_el, "display-name")
        display_name.text = channel.get("name", xmltv_id)

    for channel in channels:
        xmltv_id = channel.get("xmltv_id")
        if not xmltv_id:
            continue
        programs = programs_by_channel.get(str(channel.get("id")), [])
        for program in programs:
            start = program.get("start_timestamp")
            stop = program.get("stop_timestamp")
            if not start or not stop:
                continue
            programme_el = SubElement(
                tv,
                "programme",
                {
                    "start": _format_xmltv_time(start),
                    "stop": _format_xmltv_time(stop),
                    "channel": xmltv_id,
                },
            )
            title_el = SubElement(programme_el, "title")
            title_el.text = program.get("name", "")
            desc_el = SubElement(programme_el, "desc")
            desc_el.text = program.get("descr", "")

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(tv, encoding="utf-8")


EMPTY_XMLTV = b'<?xml version="1.0" encoding="UTF-8"?>\n<tv></tv>'
