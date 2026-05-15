import os
import re
import time
import json
import uuid
import hashlib
import datetime
import requests
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG CHO LƯƠNG SƠN TV
# =========================================================
TARGET_SITE   = "https://luongsontv60.online/"
BASE_URL      = "https://luongsontv60.online"
FILE_PATH     = "luongson.json"
LIMIT_MATCHES = 20 

# MÚI GIỜ VIỆT NAM (GMT+7)
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

# Lấy token từ repo Lương Sơn
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauluongson")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGO_CACHE = {}

# =========================================================
# HELPER
# =========================================================
def make_id(seed: str = "") -> str:
    raw = seed or str(uuid.uuid4())
    h = hashlib.md5(raw.encode()).hexdigest()
    return f"luongson-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

# =========================================================
# LOGO
# =========================================================
def get_api_logo(team_name: str) -> str:
    if not team_name or team_name == "Unknown": return ""
    team_name = re.sub(r"\bFc\b$", "FC", team_name).strip()
    if team_name in LOGO_CACHE: return LOGO_CACHE[team_name]
    try:
        slug = team_name.lower().replace(" ", "-")
        r = requests.get(f"https://football-logos.cc/{slug}/", headers=_HEADERS, timeout=5)
        m = re.search(r'https://football-logos\.cc/logos/[^"]+\.png', r.text)
        if m:
            LOGO_CACHE[team_name] = m.group(0)
            return m.group(0)
    except: pass
    LOGO_CACHE[team_name] = ""
    return ""

def get_final_logo(team_name: str, site_logo: str) -> str:
    api_logo = get_api_logo(team_name)
    if api_logo: return api_logo
    if site_logo and site_logo.startswith("http"): return site_logo
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# PARSE THỜI GIAN TỪ URL
# =========================================================
def parse_time_from_url(url: str) -> str:
    slug = url.rstrip('/').split('/')[-1]
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
    if m:
        dt_utc = datetime.datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5))
        )
        dt_vn = dt_utc + datetime.timedelta(hours=7)
        return dt_vn.strftime("%H:%M %d/%m/%Y")
    return ""

def parse_teams_from_title(title: str):
    clean = re.sub(r'[-_]\d{4}-\d{2}-\d{2}[-_]\d{4}$', '', title)
    clean = re.sub(r'\.\s*[A-Za-z0-9 \-]{3,30}$', '', clean).strip()
    if re.fullmatch(r'[a-z0-9\-]+', clean): clean = clean.replace('-', ' ')
    m = re.split(r'\s+vs\.?\s+', clean, maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2 and m[0].strip() and m[1].strip():
        return m[0].strip().title(), m[1].strip().title()
    m2 = re.split(r'\s+-\s+', clean, maxsplit=1)
    if len(m2) == 2 and m2[0].strip() and m2[1].strip():
        return m2[0].strip().title(), m2[1].strip().title()
    return clean.strip().title(), "Unknown"

# =========================================================
# JS: EXTRACT MATCH DATA LƯƠNG SƠN
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();
    const SKIP = /^(vs\\.?|live|blv|bóng đá|sắp diễn ra|đang phát|đặt cược|bảng|giải|\\d+[:\\-\\/]\\d+|\\d+$)/i;

    const anchors = Array.from(document.querySelectorAll('a[href]')).filter(a => {
        const h = a.href || '';
        // Bắt các link thuộc luongsontv hoặc các link có chữ truc-tiep, match
        return (h.includes('luongson') || h.includes('/truc-tiep/') || h.includes('/match/')) &&
               !h.includes('/lich-thi-dau') && !h.includes('/ket-qua') && h.length > 20;
    });

    for (const a of anchors) {
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);

        let league = '';
        const leagueSelectors = ['[class*="league" i]', '[class*="tournament" i]', 'h3', 'h4'];
        for (const sel of leagueSelectors) {
            const el = a.querySelector(sel);
            if (el) {
                const t = clean(el.innerText);
                if (t && t.length < 40 && !/\\d+\\s*[-:]\\s*\\d+/.test(t)) { league = t; break; }
            }
        }

        const cardText = clean(a.innerText).toLowerCase();
        const isLive = /live|trực tiếp|đang phát/.test(cardText) || !!a.querySelector('[class*="live" i]');

        let home = '', away = '';
        // Thử tìm trong cấu trúc 2 cột hoặc 3 cột
        const gridBox = a.querySelector('div[class*="grid-cols-[1fr_auto_1fr]"], div[class*="flex"]');
        if (gridBox && gridBox.children.length >= 3) {
            home = clean(gridBox.children[0].innerText);
            away = clean(gridBox.children[gridBox.children.length - 1].innerText);
        }

        if (!home || !away) {
            for (const seg of href.split('/').reverse()) {
                const base = seg.split('.')[0];
                const vm = base.match(/^(.+?)-vs-(.+)$/i);
                if (vm) {
                    const toTitle = s => s.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                    home = toTitle(vm[1]); away = toTitle(vm[2]);
                    break;
                }
            }
        }

        let homeLogo = '', awayLogo = '';
        const validImgs = Array.from(a.querySelectorAll('img')).filter(img => {
            const c = (img.className || '').toLowerCase();
            const s = (img.src || '').toLowerCase();
            return !c.includes('w-6') && !c.includes('rounded-full') &&
                   !s.includes('gif') && !s.includes('avatar') && !s.includes('user');
        });
        
        if (validImgs.length >= 2) {
            homeLogo = validImgs[0].src;
            awayLogo = validImgs[validImgs.length - 1].src;
        }

        let timeStr = '';
        const timeEl = a.querySelector('[class*="time" i], [class*="date" i]');
        if (timeEl) timeStr = clean(timeEl.innerText);
        if (!timeStr) {
            const tm = clean(a.innerText).match(/(\\d{1,2}:\\d{2})\\s*(\\d{1,2}\\/\\d{1,2})?/);
            if (tm) timeStr = tm[0].trim();
        }

        results.push({ href, home, away, timeStr, isLive, league, homeLogo, awayLogo });
    }
    return results;
}
"""

# =========================================================
# CAPTURE STREAM - ƯU TIÊN MÁY CHỦ cdnfaster
# =========================================================
def capture_stream(context, match_url: str) -> list:
    page = context.new_page()
    try: Stealth().apply_stealth_sync(page)
    except: pass

    streams = set()
    BAD = [
        ".gif", ".png", ".jpg", ".jpeg", ".webp", ".svg",
        ".mp4", ".mp3", ".vtt", ".srt",
        "waiting", "loop", "placeholder", "fallback", "saba.m3u8",
        "/ad/", "/ads/", "/vast/", "quangcao", "banner", "preroll", "postroll",
    ]

    def process_url(url):
        u = url.lower()
        if ".m3u8" not in u: return
        if any(b in u for b in BAD): return
        streams.add(url)

    page.on("request",  lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.goto(match_url, wait_until="load", timeout=60000)
        
        # Nhấp chuột ảo vào giữa màn hình để kích hoạt video nếu Lương Sơn yêu cầu tương tác
        try:
            vp = page.viewport_size
            if vp:
                page.mouse.click(vp["width"] // 2, vp["height"] // 2)
        except: pass

        page.wait_for_timeout(8000)
        
        # Đợi để tóm được m3u8 xịn nhất
        deadline = time.time() + 15
        while time.time() < deadline:
            if any("cdnfaster-a.live" in s.lower() for s in streams):
                break
            time.sleep(1)
            
    except PWTimeout: print("      ⚠️ TIMEOUT")
    except Exception as e: print(f"      ❌ {e}")
    finally: page.close()

    if not streams: return []

    scored = []
    for s in streams:
        score = 0
        lo = s.lower()
        # ĐÁNH GIÁ ĐIỂM SỐ: CHỈ ĐỊNH CDN CỦA LƯƠNG SƠN
        if "cdnfaster-a.live" in lo: score += 10000 # Điểm tuyệt đối cho server xịn
        if "100ycdn" in lo: score += 5000
        if "playlist.m3u8" in lo: score += 500
        if "index.m3u8" in lo: score += 200
        scored.append((score, s))

    # Xếp hạng và trả về danh sách link (link xịn nhất đứng đầu)
    scored.sort(reverse=True, key=lambda x: x[0])
    return [s for sc, s in scored]

# =========================================================
# BUILD JSON
# =========================================================
def build_channel(home: str, away: str, thoi_gian: str, is_live: bool,
                  stream_urls: list, match_url: str, logo_nha: str, logo_khach: str,
                  league: str = "") -> dict:
    cid = make_id(match_url)
    title_clean  = f"{home} vs {away}"
    
    thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', thoi_gian)

    display_name = f"⚽ {title_clean}"
    if league: display_name += f" | {league}"
    if thoi_gian: display_name += f" | {thoi_gian}"

    if is_live and stream_urls:
        label = {"text": "● Live", "position": "top-left", "color": "#00ffffff", "text_color": "#ff0000"}
    elif is_live:
        label = {"text": "🔴 Chờ stream", "position": "top-left", "color": "#00ffffff", "text_color": "#ff6600"}
    else:
        label = {"text": "⏳ Chưa live", "position": "top-left", "color": "#00ffffff", "text_color": "#d54f1a"}

    stream_links = []
    for idx, url in enumerate(stream_urls[:2], 1):
        stream_links.append({
            "id": make_link_id(),
            "name": f"Link {idx}",
            "type": "hls",
            "default": idx == 1,
            "url": url,
        })

    return {
        "id": cid,
        "name": display_name,
        "logo_nha": logo_nha,      
        "logo_khach": logo_khach,  
        "type": "single",
        "display": "thumbnail-only",
        "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": logo_nha, "width": 1600, "height": 1200},
        "labels": [label],
        "sources": [{
            "id": cid,
            "name": "Lương Sơn",
            "contents": [{
                "id": cid,
                "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": stream_links}]
            }]
        }],
    }

def build_json(channels: list) -> dict:
    return {
        "id": "luongson",
        "url": "https://raw.githubusercontent.com/Eternal161/dauluongson/main/luongson.json",
        "name": "Lương Sơn TV",
        "color": "#1cb57a",
        "grid_number": 3,
        "image": {"type": "cover", "url": "https://i.imgur.com/your-luongson-logo.png"},
        "groups": [{
            "id": "live",
            "name": "🔴 Live bóng đá",
            "display": "vertical",
            "grid_number": 2,
            "enable_detail": False,
            "channels": channels,
        }],
    }

# =========================================================
# GITHUB PUSH
# =========================================================
def push_to_github(content: str):
    if not GITHUB_TOKEN:
        print("⚠️ Không có GH_TOKEN, đang lưu file local...")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return
    g    = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg  = "⚽ Update Lương Sơn: " + datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print("✅ Đã cập nhật lên GitHub")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print("✅ Đã tạo file mới trên GitHub")

# =========================================================
# MAIN SCRAPER
# =========================================================
def scrape_and_push():
    now_vn = datetime.datetime.now(VN_TZ)
    print("=" * 70)
    print(f"🚀 BẮT ĐẦU BOT LƯƠNG SƠN (Giờ VN): {now_vn.strftime('%H:%M:%S %d/%m/%Y')}")
    print("=" * 70)

    raw_matches = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_HEADERS["User-Agent"],
            timezone_id="Asia/Ho_Chi_Minh"
        )

        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass

        print(f"\n📺 QUÉT: {TARGET_SITE}")
        try: 
            page.goto(TARGET_SITE, wait_until="networkidle", timeout=60000)
        except: 
            try: page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
            except: pass

        page.wait_for_timeout(5000)

        for _ in range(5):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(700)

        try:
            raw_matches = page.evaluate(JS_EXTRACT)
            print(f"   JS extract: {len(raw_matches)} trận")
        except Exception as e:
            print(f"   ❌ Lỗi JS: {e}")

        # Lọc thủ công tên đội nếu JS bắt hụt
        for m in raw_matches:
            h = (m.get("home") or "").strip()
            a = (m.get("away") or "").strip()
            if not h or not a or h == a or len(h) < 2:
                slug = m["href"].rstrip("/").split("/")[-1]
                fh, fa = parse_teams_from_title(slug)
                m["home"], m["away"] = fh, fa

        if LIMIT_MATCHES:
            raw_matches = raw_matches[:LIMIT_MATCHES]

        # QUÉT LUỒNG TẤT CẢ CÁC TRẬN
        print(f"\n🎥 ĐANG QUÉT TÌM LINK CHO TẤT CẢ {len(raw_matches)} TRẬN...")
        for idx, m in enumerate(raw_matches, 1):
            m["timeStr"] = m.get("timeStr") or parse_time_from_url(m["href"]) or "Không rõ"
            print(f"\n   [{idx}/{len(raw_matches)}] Đang kiểm tra: {m['home']} vs {m['away']} ({m['timeStr']})")
            
            streams = capture_stream(context, m["href"])
            m["streams"] = streams
            if streams:
                print(f"      ✅ ĐÃ BẮT ĐƯỢC {len(streams)} LINK M3U8!")
                m["isLive"] = True 
            else:
                print(f"      ⚠️ Không tìm thấy luồng.")

    # Đóng gói và đẩy dữ liệu
    channels = []
    for m in raw_matches:
        home      = (m.get("home") or "Unknown").strip().title()
        away      = (m.get("away") or "Unknown").strip().title()
        thoi_gian = m.get("timeStr") or "Không rõ"
        is_live   = m.get("isLive", False)
        league    = (m.get("league") or "").strip()
        logo_nha  = get_final_logo(home, m.get("homeLogo"))
        logo_khach= get_final_logo(away, m.get("awayLogo"))

        ch = build_channel(home, away, thoi_gian, is_live, m["streams"], m["href"], logo_nha, logo_khach, league)
        channels.append(ch)

    content = json.dumps(build_json(channels), indent=2, ensure_ascii=False)
    push_to_github(content)
    print("\n" + "=" * 70)
    print(f"✅ HOÀN TẤT: Cập nhật thành công {len(channels)} trận!")
    print("=" * 70)

if __name__ == "__main__":
    scrape_and_push()
