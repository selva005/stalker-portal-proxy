// Stalker-Portal To M3U Generator Script
// Created by: @tg_aadi
// Telegram: https://t.me/tg_aadi
// This script is free for everyone to use
// If you encounter issues, check the console logs for debug information

// ⚙ INSTRUCTIONS FOR USERS ⚙
// Update the 'config' object below with your Stalker-Portal details
// Access the generated M3U playlist by visiting: <your-deployment-url>/playlist.m3u8

// ============ ⚙ CONFIGURATION ============
const config = {
    host: '', // Replace with your Stalker-Portal host (e.g., 'example.com')
    mac_address: '', // Replace with your MAC address
    serial_number: '', // Replace with your serial number
    device_id: '', // Replace with your device_id
    device_id_2: '', // Replace with your device_id_2
    stb_type: 'MAG250', // Replace with Stalker-Portal Stb_type
    api_signature: '263', // No need to change
};

// Auto-generate hw_version & hw_version_2
async function generateHardwareVersions() {
    config.hw_version = '1.7-BD-' + (await hash(config.mac_address)).substring(0, 2).toUpperCase();
    config.hw_version_2 = await hash(config.serial_number.toLowerCase() + config.mac_address.toLowerCase());
}

async function hash(str) {
    const data = new TextEncoder().encode(str);
    const digest = await crypto.subtle.digest('MD5', data);
    return Array.from(new Uint8Array(digest)).map(x => x.toString(16).padStart(2, '0')).join('');
}

function logDebug(message) {
    console.log(`${new Date().toISOString()} - ${message}`);
}

function getHeaders(token = '') {
    const headers = {
        'Cookie': `mac=${config.mac_address}; stb_lang=en; timezone=GMT`,
        'Referer': `http://${config.host}/stalker_portal/c/`,
        'User-Agent': 'Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3',
        'X-User-Agent': `Model: ${config.stb_type}; Link: WiFi`
    };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
}

async function getToken() {
    const url = `http://${config.host}/stalker_portal/server/load.php?type=stb&action=handshake&token=&JsHttpRequest=1-xml`;
    try {
        logDebug(`Fetching token from ${url}`);
        const response = await fetch(url, { headers: getHeaders() });
        logDebug(`getToken response status: ${response.status}`);
        if (!response.ok) {
            logDebug(`getToken failed with status: ${response.status} ${response.statusText}`);
            return '';
        }
        const text = await response.text();
        logDebug(`getToken response (first 500 chars): ${text.substring(0, 500)}`);
        const data = JSON.parse(text);
        const token = data.js?.token || '';
        logDebug(`Extracted token: ${token ? 'Success' : 'Empty'}`);
        return token;
    } catch (e) {
        logDebug(`Error in getToken: ${e.message}`);
        return '';
    }
}

async function auth(token) {
    const metrics = {
        mac: config.mac_address,
        model: '',
        type: 'STB',
        uid: '',
        device: '',
        random: ''
    };
    const metricsEncoded = encodeURIComponent(JSON.stringify(metrics));

    const url = `http://${config.host}/stalker_portal/server/load.php?type=stb&action=get_profile`
        + `&hd=1&ver=ImageDescription:%200.2.18-r14-pub-250;`
        + `%20PORTAL%20version:%205.5.0;%20API%20Version:%20328;`
        + `&num_banks=2&sn=${config.serial_number}`
        + `&stb_type=${config.stb_type}&client_type=STB&image_version=218&video_out=hdmi`
        + `&device_id=${config.device_id}&device_id2=${config.device_id_2}`
        + `&signature=&auth_second_step=1&hw_version=${config.hw_version}`
        + `&not_valid_token=0&metrics=${metricsEncoded}`
        + `&hw_version_2=${config.hw_version_2}&api_signature=${config.api_signature}`
        + `&prehash=&JsHttpRequest=1-xml`;

    try {
        logDebug(`Authenticating with URL: ${url.substring(0, 200)}...`);
        const response = await fetch(url, { headers: getHeaders(token) });
        logDebug(`auth response status: ${response.status}`);
        if (!response.ok) {
            logDebug(`auth failed with status: ${response.status} ${response.statusText}`);
            return [];
        }
        const text = await response.text();
        logDebug(`auth response (first 500 chars): ${text.substring(0, 500)}`);
        const data = JSON.parse(text);
        return data.js || [];
    } catch (e) {
        logDebug(`Error in auth: ${e.message}`);
        return [];
    }
}

async function handShake(token) {
    const url = `http://${config.host}/stalker_portal/server/load.php?type=stb&action=handshake&token=${token}&JsHttpRequest=1-xml`;
    try {
        logDebug(`Performing handshake with token: ${token}`);
        const response = await fetch(url, { headers: getHeaders() });
        logDebug(`handShake response status: ${response.status}`);
        if (!response.ok) {
            logDebug(`handShake failed with status: ${response.status} ${response.statusText}`);
            return '';
        }
        const text = await response.text();
        logDebug(`handShake response (first 500 chars): ${text.substring(0, 500)}`);
        const data = JSON.parse(text);
        const newToken = data.js?.token || '';
        logDebug(`New token: ${newToken ? 'Success' : 'Empty'}`);
        return newToken;
    } catch (e) {
        logDebug(`Error in handShake: ${e.message}`);
        return '';
    }
}

async function getAccountInfo(token) {
    const url = `http://${config.host}/stalker_portal/server/load.php?type=account_info&action=get_main_info&JsHttpRequest=1-xml`;
    try {
        logDebug(`Fetching account info from ${url}`);
        const response = await fetch(url, { headers: getHeaders(token) });
        logDebug(`getAccountInfo response status: ${response.status}`);
        if (!response.ok) {
            logDebug(`getAccountInfo failed with status: ${response.status} ${response.statusText}`);
            return [];
        }
        const text = await response.text();
        logDebug(`getAccountInfo response (first 500 chars): ${text.substring(0, 500)}`);
        const data = JSON.parse(text);
        logDebug(`Account info response: ${JSON.stringify(data, null, 2)}`);
        return data.js || [];
    } catch (e) {
        logDebug(`Error in getAccountInfo: ${e.message}`);
        return [];
    }
}

async function getGenres(token) {
    const url = `http://${config.host}/stalker_portal/server/load.php?type=itv&action=get_genres&JsHttpRequest=1-xml`;
    try {
        logDebug(`Fetching genres from ${url}`);
        const response = await fetch(url, { headers: getHeaders(token) });
        logDebug(`getGenres response status: ${response.status}`);
        if (!response.ok) {
            logDebug(`getGenres failed with status: ${response.status} ${response.statusText}`);
            return [];
        }
        const text = await response.text();
        logDebug(`getGenres response (first 500 chars): ${text.substring(0, 500)}`);
        const data = JSON.parse(text);
        logDebug(`Fetched genres data`);
        return data.js || [];
    } catch (e) {
        logDebug(`Error in getGenres: ${e.message}`);
        return [];
    }
}

async function getStreamURL(id, token) {
    const url = `http://${config.host}/stalker_portal/server/load.php?type=itv&action=create_link&cmd=ffrt%20http://localhost/ch/${id}&JsHttpRequest=1-xml`;
    try {
        logDebug(`Fetching stream URL for channel ID: ${id}`);
        const response = await fetch(url, { headers: getHeaders(token) });
        logDebug(`getStreamURL response status: ${response.status}`);
        if (!response.ok) {
            logDebug(`getStreamURL failed with status: ${response.status} ${response.statusText}`);
            return '';
        }
        const text = await response.text();
        logDebug(`getStreamURL response (first 500 chars): ${text.substring(0, 500)}`);
        const data = JSON.parse(text);
        const stream = data.js?.cmd || '';
        logDebug(`Stream URL: ${stream ? 'Success' : 'Empty'}`);
        return stream;
    } catch (e) {
        logDebug(`Error in getStreamURL: ${e.message}`);
        return '';
    }
}

async function genToken() {
    await generateHardwareVersions();
    const token = await getToken();
    if (!token) {
        logDebug('Failed to retrieve initial token');
        return { token: '', profile: [], account_info: [] };
    }
    const profile = await auth(token);
    const newToken = await handShake(token);
    if (!newToken) {
        logDebug('Failed to retrieve new token');
        return { token: '', profile, account_info: [] };
    }
    const account_info = await getAccountInfo(newToken);
    return { token: newToken, profile, account_info };
}

async function convertJsonToM3U(channels, profile, account_info, request) {
    let m3u = [
        '#EXTM3U',
        `# Total Channels => ${channels.length}`,
        '# Script => @tg_aadi',
        ''
    ];

    let server_ip = profile.ip || 'Unknown';
    m3u.push(`#EXTINF:-1 tvg-name="IP" tvg-logo="https://img.icons8.com/?size=160&id=OWj5Eo00EaDP&format=png" group-title="Portal | Info",IP • ${server_ip}`);
    m3u.push('https://tg-aadi.vercel.app/intro.m3u8');

    m3u.push('#EXTINF:-1 tvg-name="Telegram: @tg_aadi" tvg-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/8/82/Telegram_logo.svg/1024px-Telegram_logo.svg.png?20220101141644" group-title="Portal | Info",Telegram • @tg_aadi');
    m3u.push('https://tg-aadi.vercel.app/intro.m3u8');

    let user_ip = request.headers.get('CF-Connecting-IP') || 'Unknown';
    m3u.push(`#EXTINF:-1 tvg-name="User IP" tvg-logo="https://uxwing.com/wp-content/themes/uxwing/download/location-travel-map/ip-location-color-icon.svg" group-title="Portal | Info",User IP • ${user_ip}`);
    m3u.push('https://tg-aadi.vercel.app/intro.m3u8');

    m3u.push(`#EXTINF:-1 tvg-name="Portal" tvg-logo="https://upload.wikimedia.org/wikipedia/commons/6/6f/IPTV.png?20180223064625" group-title="Portal | Info",Portal • ${config.host}`);
    m3u.push('https://tg-aadi.vercel.app/intro.m3u8');

    const created = profile.created || 'Unknown';
    m3u.push(`#EXTINF:-1 tvg-name="Created" tvg-logo="https://cdn-icons-png.flaticon.com/128/1048/1048953.png" group-title="Portal | Info",Created • ${created}`);
    m3u.push('https://tg-aadi.vercel.app/intro.m3u8');

    const end_date = account_info.end_date || 'Unknown';
    m3u.push(`#EXTINF:-1 tvg-name="Expire" tvg-logo="https://www.citypng.com/public/uploads/preview/hand-drawing-clipart-14-feb-calendar-icon-701751694973910ds70zl0u9u.png" group-title="Portal | Info",End date • ${end_date}`);
    m3u.push('https://tg-aadi.vercel.app/intro.m3u8');

    const tariff_plan = account_info.tariff_plan || 'Unknown';
    m3u.push(`#EXTINF:-1 tvg-name="Tariff Plan" tvg-logo="https://img.lovepik.com/element/45004/5139.png_300.png" group-title="Portal | Info",Tariff Plan • ${tariff_plan}`);
    m3u.push('https://tg-aadi.vercel.app/intro.m3u8');

    let max_online = 'Unknown';
    if (profile.storages && Object.keys(profile.storages).length > 0) {
        const first_storage = Object.values(profile.storages)[0];
        max_online = first_storage.max_online || 'Unknown';
    }
    m3u.push(`#EXTINF:-1 tvg-name="Max Online" tvg-logo="https://thumbs.dreamstime.com/b/people-vector-icon-group-symbol-illustration-businessman-logo-multiple-users-silhouette-153484048.jpg?w=1600" group-title="Portal | Info",Max Connection • ${max_online}`);
    m3u.push('https://tg-aadi.vercel.app/intro.m3u8');

    const origin = new URL(request.url).origin;

    if (!channels.length) {
        logDebug('No channels found');
    } else {
        channels.forEach((channel, index) => {
            let cmd = channel.cmd || '';
            let real_cmd = cmd.replace('ffrt http://localhost/ch/', '');
            if (!real_cmd) {
                real_cmd = 'unknown';
                logDebug(`Invalid or empty cmd for channel #${index}: ${channel.name}`);
            }
            const logo_url = channel.logo ? `http://${config.host}/stalker_portal/misc/logos/320/${channel.logo}` : '';
            m3u.push(`#EXTINF:-1 tvg-id="${channel.tvgid}" tvg-name="${channel.name}" tvg-logo="${logo_url}" group-title="${channel.title}",${channel.name}`);
            const channel_stream_url = `${origin}/${real_cmd}.m3u8`;
            m3u.push(channel_stream_url);
            if (index < 5) {
                logDebug(`M3U Channel #${index}: ${channel.name}, URL: ${channel_stream_url}`);
            }
        });
    }

    return m3u.join('\n');
}

addEventListener('fetch', event => {
    event.respondWith(handleRequest(event.request));
});

async function handleRequest(request) {
    const url = new URL(request.url);
    const pathParts = url.pathname.split('/');
    const lastPart = pathParts[pathParts.length - 1];

    try {
        // Generate token, profile, and account info
        logDebug('Starting token generation');
        const { token, profile, account_info } = await genToken();
        if (!token) {
            logDebug('Token generation failed, exiting');
            return new Response('Token generation failed', { status: 500 });
        }
        logDebug('Token generation successful');

        // Handle playlist request
        if (url.pathname === '/playlist.m3u8') {
            // Fetch all channels
            const channelsUrl = `http://${config.host}/stalker_portal/server/load.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml`;
            let channelsData;
            try {
                logDebug(`Fetching channels from ${channelsUrl}`);
                const response = await fetch(channelsUrl, { headers: getHeaders(token) });
                logDebug(`Channels response status: ${response.status}`);
                if (!response.ok) {
                    const errorText = await response.text();
                    logDebug(`Fetch error in fetching channels: ${response.status} ${response.statusText}, Body: ${errorText.substring(0, 500)}`);
                    return new Response(`Failed to fetch channels: ${response.status} ${response.statusText}`, { status: 500 });
                }
                const text = await response.text();
                logDebug(`Channels response (first 500 chars): ${text.substring(0, 500)}`);
                channelsData = JSON.parse(text);
            } catch (e) {
                logDebug(`Error in fetching channels: ${e.message}`);
                return new Response(`Error fetching channels: ${e.message}`, { status: 500 });
            }

            // Fetch genres
            logDebug('Fetching genres');
            const genres = await getGenres(token);

            // Parse channels
            let channels = [];
            if (channelsData.js?.data) {
                logDebug(`Found ${channelsData.js.data.length} channels in response`);
                channels = channelsData.js.data.map((item, index) => {
                    const channel = {
                        name: item.name || 'Unknown',
                        cmd: item.cmd || '',
                        tvgid: item.xmltv_id || '',
                        id: item.tv_genre_id || '',
                        logo: item.logo || ''
                    };
                    if (index < 5) {
                        logDebug(`Channel #${index}: ${JSON.stringify(channel, null, 2)}`);
                    }
                    return channel;
                });
            } else {
                logDebug('No channel data found in response');
            }

            // Map genres to channels
            const groupTitleMap = {};
            genres.forEach(group => {
                groupTitleMap[group.id] = group.title || 'Other';
            });

            channels = channels.map(channel => ({
                ...channel,
                title: groupTitleMap[channel.id] || 'Other'
            }));

            // Generate M3U
            logDebug('Generating M3U content');
            const m3uContent = await convertJsonToM3U(channels, profile, account_info, request);

            // Return M3U response
            logDebug('Returning M3U response');
            return new Response(m3uContent, {
                headers: {
                    'Content-Type': 'application/vnd.apple.mpegurl'
                }
            });
        }

        // Handle stream link request
        if (lastPart.endsWith('.m3u8') && lastPart !== 'playlist.m3u8') {
            const id = lastPart.replace(/\.m3u8$/, '');
            if (!id) {
                logDebug('Missing channel ID in URL');
                return new Response('❌ Missing channel ID in URL', { status: 400 });
            }

            const stream = await getStreamURL(id, token);
            if (!stream) {
                logDebug('No stream URL received');
                return new Response('No stream URL received', { status: 500 });
            }

            return Response.redirect(stream, 302);
        }

        // Handle invalid paths
        logDebug(`Invalid path requested: ${url.pathname}`);
        return new Response('Not Found', { status: 404 });
    } catch (e) {
        logDebug(`Unexpected error: ${e.message}`);
        return new Response(`Internal Server Error: ${e.message}`, { status: 500 });
    }
}

// ========================================== { THE END } ================================================================================
