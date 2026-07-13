# 📺 Stalker-Portal to live M3U Playlist Generator

**Author:** [@tg_aadi](https://t.me/tg_aadi)  
**Telegram Support:** [https://t.me/tg_aadi](https://t.me/tg_aadi)   

This project allows you to **convert any Stalker-Portal account into an M3U playlist** that can be used in IPTV players like **TiviMate, OTT Navigator, VLC, Perfect Player**, etc.

With this script, you can:
- 🔑 Authenticate to any Stalker-Portal using **MAC Address** and **Device IDs**  
- 🎯 Generate a **dynamic M3U playlist** directly from your portal  
- 📡 Get direct stream links (`.m3u8`) for any channel  
- 🌍 Host your own IPTV playlist server using **Cloudflare Workers** (free)

---

## ⚡ Features
- Works with most Stalker-Portals worldwide
- Fetches **Account Info, Profile, Channel List, Genres**
- Auto-detects **User IP** and shows it in playlist info
- Fully compatible with all IPTV players
- 100% serverless hosting using Cloudflare Workers

---

## 📋 Requirements
- A **working Stalker-Portal account** (Host, MAC address, serial number, etc.)
- A **Cloudflare account** (free) to deploy the worker
- Basic GitHub knowledge to fork & deploy

---
📂 Usage

Once deployed:

Playlist:

https://YOUR-WORKER-NAME.YOUR-SUBDOMAIN.workers.dev/playlist.m3u8


Add this link to any IPTV player.

⚠️ Notes & Troubleshooting

Make sure your portal credentials are correct

Cloudflare Workers have a 10-second fetch limit; huge playlists may take time

If streams don't play, your portal may require encryption or be geo-restricted

📜 License

This project is free for educational purposes. You are responsible for how you use it.

💬 Support

If you need help or encounter issues:

Contact me on Telegram: @tg_aadi

Open a GitHub issue in this repository

## 🛠 Configuration

Edit the **`config`** object in the script before deployment:

```javascript
const config = {
    host: 'sky.dittotvv.cc',             // e.g. 'example.com' (your portal host without http/https)
    mac_address: '00:1A:79:F2:B3:14',      // Your portal MAC address (e.g., 00:1A:79:12:34:56)
    serial_number: 'A9790F4160084',    // Your device serial number
    device_id: '09F00F06FD94E7A4034ADB2121040AB31C3B284A89C3295879CC94CF86A36228',        // Your Device ID
    device_id_2: '09F00F06FD94E7A4034ADB2121040AB31C3B284A89C3295879CC94CF86A36228',      // Your Device ID 2
    stb_type: 'MAG250',   // STB type (default MAG250)
    api_signature: '263', // Keep as is
};
```

---

## 🐍 Python / Docker Port

This repo also includes a Python (FastAPI) port of the same functionality, for self-hosting
in a Docker container instead of Cloudflare Workers. Files: `main.py`, `config.py`,
`stalker_client.py`, `m3u.py`, `Dockerfile`, `docker-compose.yml`.

### Configuration

Copy `.env.example` to `.env` and fill in your portal details (`STALKER_HOST`,
`STALKER_MAC`, `STALKER_SERIAL`, `STALKER_DEVICE_ID`, `STALKER_DEVICE_ID2` are required;
`STALKER_STB_TYPE`, `STALKER_API_SIGNATURE`, `LOG_LEVEL` have defaults).

```bash
cp .env.example .env
```

### Run with Docker Compose

```bash
docker compose up --build
```

Playlist URL: `http://<host>:8000/playlist.m3u8`

### Run locally without Docker

```bash
pip install -r requirements.txt
export $(cat .env | xargs)
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Endpoints

- `GET /playlist.m3u8` — full M3U playlist (channels + account info rows)
- `GET /{channel_id}.m3u8` — 302-redirects to the live stream URL for that channel

### Notes

- The portal session (token/profile/account info) is cached in memory and only
  re-authenticated when a portal call fails, avoiding a full re-auth on every request.
- Logs go to stdout (`docker logs`); control verbosity with `LOG_LEVEL` (default `INFO`).
