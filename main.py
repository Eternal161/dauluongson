import os
import re
import time
import json
import uuid
import hashlib
import datetime
import requests
import unicodedata
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# =========================================================
# BỘ GIÁP STEALTH
# =========================================================
def apply_stealth(page):
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except: pass
    except: pass

# =========================================================
# CONFIG LƯƠNG SƠN TV
# =========================================================
TARGET_SITE   = "https://luongsontv60sv.com/"
BASE_URL      = "https://luongsontv60sv.com"
FILE_PATH     = "luongson.json"
LIMIT_MATCHES = 15 

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauluongson")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
LOGO_CACHE = {}

# =========================================================
# HELPER FUNCTIONS
# =========================================================
def make_id(seed: str = "") -> str:
    h = hashlib.md5((seed or str(uuid.uuid4())).encode()).hexdigest()
    return f"luongson-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

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

def parse_time_from_url(url: str) -> str:
    try:
        slug = url.rstrip('/').split('/')[-1]
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
        if m:
            y, mth, d, h, mn = map(int, m.groups())
            if mth > 12: mth, d = d, mth
            dt_utc = datetime.datetime(y, mth, d, h, mn)
            dt_vn = dt_utc + datetime.timedelta(hours=7)
            return dt_vn.strftime("%H:%M %d/%m/%Y")
        m2 = re.search(r'(\d{2})(\d{2})-(\d{2})-(\d{2})-(\d{4})', slug)
        if m2:
            hh, mm, dd, mo, yyyy = m2.groups()
            return f"{hh}:{mm} {dd}/{mo}/{yyyy}"
    except: pass
    return ""

def parse_teams_from_title(title: str):
    clean = re.sub(r'[-_]\d{4}-\d{2}-\d{2}[-_]\d{4}$', '', title)
    clean = re.sub(r'\.\s*[A-Za-z0-9 \-]{3,30}$', '', clean).strip()
    if re.fullmatch(r'[a-z0-9\-]+', clean): clean = clean.replace('-', ' ')
    m = re.split(r'\s+vs\.?\s+', clean, maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2 and m[0].strip() and m[1].strip(): return m[0].strip().title(), m[1].strip().title()
    return clean.strip().title(), "Unknown"

# =========================================================
# JS: LẤY DỮ LIỆU TỪ LƯƠNG SƠN TV
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();

    const anchors = Array.from(document.querySelectorAll('a[href]')).filter(a => {
        const h = a.href || '';
        if (h.includes('#') || h.endsWith('.online') || h.endsWith('.online/')) return false;
        if (h.includes('/lich-thi-dau') || h.includes('/ket-qua') || h.includes('/tin-tuc') || h.includes('nhan-dinh') || h.includes('highlight')) return false;
        return h.includes('/truc-tiep/') || h.includes('/match/') || h.includes('-vs-');
    });

    for (const a of anchors) {
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);

        let league = '';
        const leagueEl = a.querySelector('[class*="league" i], [class*="tournament" i], h3, h4');
        if (leagueEl) league = clean(leagueEl.innerText);

        const cardText = clean(a.innerText).toLowerCase();
        const isLiveUI = /live|trực tiếp|đang phát/.test(cardText) || !!a.querySelector('[class*="live" i]');

        let home = '', away = '';
        const teamNames = Array.from(a.querySelectorAll('.team-name, p[class*="team-name"]'));
        if (teamNames.length >= 2) {
            home = clean(teamNames[0].innerText);
            away = clean(teamNames[teamNames.length - 1].innerText);
        }

        let homeLogo = '', awayLogo = '';
        const teamLogos = Array.from(a.querySelectorAll('.team-logo, img[class*="team-logo"]'));
        if (teamLogos.length >= 2) {
            homeLogo = teamLogos[0].src;
            awayLogo = teamLogos[teamLogos.length - 1].src;
        }

        let fullText = clean(a.innerText || '');
        let timeStr = '';
        let scoreStr = '';
        
        let timeMatch = fullText.match(/(\\d{1,2}:\\d{2})/);
        let dateMatch = fullText.match(/(\\d{1,2}\\/\\d{1,2})/);
        if (timeMatch) {
            timeStr = timeMatch[1];
            if (dateMatch) timeStr += ' ' + dateMatch[1]; 
        }
        
        let scoreMatch = fullText.match(/(\\d+)\\s*[:\\-]\\s*(\\d+)/);
        if (scoreMatch) {
            scoreStr = scoreMatch[0];
            let minuteMatch = fullText.match(/\\b(H[T12]|FT|\\d{1,3}')\\b/i);
            if (minuteMatch) scoreStr = minuteMatch[0] + ' ' + scoreStr; 
        }
        
        let title = a.querySelector('.team-name') ? a.innerText : 'Match';
        let tournament = a.querySelector('.league-name')?.innerText?.trim() || '';
        
        results.push({ href, home, away, timeStr, scoreStr, isLiveUI, homeLogo, awayLogo, tournament });
    }
    return results;
}
"""

# =========================================================
# LƯỚI QUÉT SIÊU TỐC (CHỈ LẤY M3U8 + TIẾNG VIỆT CHUẨN)
# =========================================================
def capture_stream(page, match_url, global_seen_streams):
    stream_dict = {} # url -> tên BLV
    state = {"current_blv": "Mặc định"}
    BAD_KEYWORDS = ["quangcao", "tvc", ".ts"]
    
    # 💡 Lọc thẳng tay, CHỈ giữ lại M3U8
    def is_valid_m3u8(url_check):
        u = url_check.lower()
        return ".m3u8" in u and not any(bad in u for bad in BAD_KEYWORDS)

    def extract_current_dom(blv_name):
        try:
            api_data = page.evaluate("window.__apiData || []")
            for item in api_data:
                text = item.get("text", "")
                matches = re.findall(r'https?:\/\/[^"\'\s<>]+?\.m3u8[^"\'\s<>]*', text)
                for m in matches:
                    clean_link = m.replace('\\/', '/')
                    if is_valid_m3u8(clean_link):
                        if "cdnfaster-a.live/" in clean_link and "cdnfaster-a.live/live/" not in clean_link:
                            clean_link = clean_link.replace("cdnfaster-a.live/", "cdnfaster-a.live/live/")
                        if clean_link not in stream_dict:
                            stream_dict[clean_link] = blv_name
        except: pass
        for frame in page.frames:
            try:
                if is_valid_m3u8(frame.url) and frame.url not in stream_dict:
                    stream_dict[frame.url] = blv_name
            except: pass

    def handle_response(response):
        try:
            u = response.url
            if is_valid_m3u8(u):
                if "cdnfaster-a.live/" in u and "cdnfaster-a.live/live/" not in u:
                    u = u.replace("cdnfaster-a.live/", "cdnfaster-a.live/live/")
                if u not in stream_dict:
                    stream_dict[u] = state["current_blv"]
        except: pass

    page.on("response", handle_response)
    
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        try:
            vp = page.viewport_size
            if vp: page.mouse.click(vp["width"] // 2, vp["height"] // 2)
        except: pass

        # 1. Bóc Danh Sách BLV Chuẩn Tiếng Việt có dấu từ DOM
        blvs = page.evaluate("""() => {
            let links = [];
            document.querySelectorAll('a[href*="blv="]').forEach(a => {
                let name = a.innerText.split('\\n')[0].trim();
                name = name.replace(/Theo dõi/gi, '').replace(/BLV\\s*:\\s*/i, '').trim();
                if (!name.toUpperCase().includes("BLV")) name = "BLV " + name;
                links.push({href: a.href, name: name});
            });
            // Lọc trùng
            let unique = [];
            let seen = new Set();
            for(let l of links) {
                if(!seen.has(l.href)) {
                    seen.add(l.href);
                    unique.push(l);
                }
            }
            return unique;
        }""")

        if blvs:
            print(f"      > Phát hiện {len(blvs)} BLV. Đang quét thần tốc (Chỉ lấy M3U8)...")
            # Giới hạn 5 BLV để tránh làm Bot quá tải
            for blv in blvs[:5]: 
                state["current_blv"] = blv["name"]
                try:
                    page.evaluate(f"""(h) => {{
                        let btn = document.querySelector(`a[href="${{h}}"]`);
                        if(btn) btn.click();
                    }}""", blv["href"])
                except: pass
                
                # 💡 Polling Siêu Tốc: Dừng ngay lập tức khi tóm được 1 link cho BLV này!
                poll_deadline = time.time() + 2.5
                while time.time() < poll_deadline:
                    extract_current_dom(state["current_blv"])
                    if any(v == blv["name"] for v in stream_dict.values()):
                        break # Đã bắt được! Next BLV luôn!
                    time.sleep(0.3)
        else:
            # Fallback nếu phòng không có nút chọn BLV
            state["current_blv"] = "Mặc định"
            poll_deadline = time.time() + 4.0
            while time.time() < poll_deadline:
                extract_current_dom(state["current_blv"])
                if stream_dict: break
                time.sleep(0.4)
                
    except Exception as e:
        print(f"      ⚠️ Lỗi khi mở phòng Live: {e}")
    finally:
        try: page.remove_listener("response", handle_response)
        except: pass
        
    valid_streams = []
    
    # Gom link và tính điểm ưu tiên server
    for u, name in stream_dict.items():
        if u not in global_seen_streams:
            score = 0
            lo = u.lower()
            if "cdnfaster" in lo: score += 1000
            if "100ycdn" in lo: score += 500
            valid_streams.append({"name": name, "url": u, "score": score})
    
    valid_streams.sort(key=lambda x: x["score"], reverse=True)
    for s in valid_streams:
        s.pop("score", None)
        
    return valid_streams

# =========================================================
# XÂY DỰNG CẤU TRÚC JSON
# =========================================================
def build_channel(m: dict, stream_data: list) -> dict:
    home = m.get("home", "").title()
    away = m.get("away", "").title()
    thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', m.get("timeStr", ""))
    
    cid = make_id(m["href"])
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {m.get('tournament')}" if m.get('tournament') else "") + (f" | {thoi_gian}" if thoi_gian else "")

    is_live = len(stream_data) > 0
    
    if is_live:
        label_text = f"[x]  Live {m.get('scoreStr', '')}".strip()
    else:
        label_text = "🔴 Chờ stream" if m.get("isLiveUI") else "⏳ Chưa live"
        
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get("isLiveUI") else "#d54f1a")

    stream_links = []
    for idx, s in enumerate(stream_data):
        # Mặc định tất cả đều là HLS (M3U8)
        stream_links.append({
            "id": make_link_id(), 
            "name": s["name"], 
            "type": "hls", 
            "default": idx == 0, 
            "url": s["url"]
        })

    return {
        "id": cid, "name": display_name, 
        "tournament": m.get("tournament", ""),
        "logo_nha": m.get("homeLogo"), "logo_khach": m.get("awayLogo"),
        "type": "single", "display": "thumbnail-only", "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": m.get("homeLogo"), "width": 1600, "height": 1200},
        "labels": [{"text": label_text, "position": "top-left", "color": "#00ffffff", "text_color": label_color}],
        "sources": [{
            "id": cid, "name": "Lương Sơn",
            "contents": [{
                "id": cid, "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": stream_links}]
            }]
        }],
    }

# =========================================================
# CHƯƠNG TRÌNH CHÍNH
# =========================================================
def scrape_and_push():
    now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT LƯƠNG SƠN (Bản Siêu Tốc - Tên Chuẩn Tiếng Việt): {now_str}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", 
            "--disable-web-security",
            "--autoplay-policy=no-user-gesture-required", 
            "--disable-blink-features=AutomationControlled"
        ])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        apply_stealth(page)

        js_interceptor = r"""
        window.__apiData = [];
        const origFetch = window.fetch;
        window.fetch = async function(...args) {
            let reqUrl = (typeof args[0] === 'string') ? args[0] : (args[0] && args[0].url ? args[0].url : '');
            const response = await origFetch.apply(this, args);
            try { 
                response.clone().text().then(t => {
                    if (t.length > 50) window.__apiData.push({url: reqUrl, text: t});
                }).catch(()=>({})); 
            } catch(e) {}
            return response;
        };
        const origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._reqUrl = url;
            this.addEventListener('load', function() {
                if (this.responseText && this.responseText.length > 50) {
                    window.__apiData.push({url: this._reqUrl, text: this.responseText});
                }
            });
            origOpen.apply(this, arguments);
        };
        """
        page.add_init_script(js_interceptor)

        try: page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
        except: pass
        page.wait_for_timeout(5000)

        for _ in range(5):
            try:
                btn_xem_them = page.get_by_text("Xem thêm", exact=True).last
                if btn_xem_them.is_visible(timeout=2000):
                    btn_xem_them.click()
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1500)
            except:
                break 

        raw_matches = page.evaluate(JS_EXTRACT)
        valid_matches = []
        seen_keys = set()

        for m in raw_matches:
            h = (m.get("home") or "").strip()
            a = (m.get("away") or "").strip()
            if not h or not a or h == a or len(h) < 2:
                slug = m["href"].rstrip("/").split("/")[-1]
                fh, fa = parse_teams_from_title(slug)
                m["home"], m["away"] = fh, fa

            m["home"] = re.sub(r' vao luc.*$', '', m["home"], flags=re.IGNORECASE).strip()
            m["away"] = re.sub(r' vao luc.*$', '', m["away"], flags=re.IGNORECASE).strip()

            h_lower, a_lower = m["home"].lower(), m["away"].lower()
            
            if any(x in h_lower for x in ["unknown", "luongson", "#main", "nhan dinh", "nhận định"]) or \
               any(x in a_lower for x in ["unknown", "luongson", "#main", "nhan dinh", "nhận định"]):
                continue
                
            match_key = f"{h_lower} vs {a_lower}"
            if match_key not in seen_keys:
                seen_keys.add(match_key)
                valid_matches.append(m)

        raw_matches = valid_matches[:LIMIT_MATCHES]
        print(f"\n🎥 QUÉT TẤT CẢ {len(raw_matches)} TRẬN (BAO GỒM TRẬN SẮP TỚI)...")

        global_seen_streams = set()

        for idx, m in enumerate(raw_matches, 1):
            
            m["timeStr"] = m.get("timeStr") or parse_time_from_url(m["href"]) or "Không rõ"
            print(f"[{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            
            m["streams"] = []
            if m.get("isLiveUI") or any(char.isdigit() for char in m["timeStr"]):
                m["streams"] = capture_stream(page, m["href"], global_seen_streams)
                
                if m["streams"]:
                    print(f"      ✅ Đã tóm được {len(m['streams'])} link!")
                    for s in m["streams"]:
                        global_seen_streams.add(s["url"])
                else:
                    print("      ❌ Không có link.")
            
            m["homeLogo"] = get_final_logo(m["home"], m.get("homeLogo"))
            m["awayLogo"] = get_final_logo(m["away"], m.get("awayLogo"))

    channels = [build_channel(m, m["streams"]) for m in raw_matches]
    content = json.dumps({
        "id": "luongson", 
        "name": "Lương Sơn TV", 
        "last_updated": now_str, 
        "groups": [{"id": "live", "name": "🔴 Trực tiếp & Sắp tới", "channels": channels}]
    }, indent=2, ensure_ascii=False)
    
    if GITHUB_TOKEN:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        msg = "⚽ Sync Lương Sơn: " + now_str
        try:
            existing = repo.get_contents(FILE_PATH)
            repo.update_file(existing.path, msg, content, existing.sha)
            print("\n✅ Đã cập nhật thành công lên GitHub!")
        except:
            repo.create_file(FILE_PATH, msg, content)
            print("\n✅ Đã khởi tạo file mới trên GitHub!")

if __name__ == "__main__":
    scrape_and_push()
Thư viện
/
Văn bản đã dán (1).txt


import os
import re
import time
import json
import uuid
import hashlib
import datetime
import requests
import unicodedata
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# =========================================================
# BỘ GIÁP STEALTH
# =========================================================
def apply_stealth(page):
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except: pass
    except: pass

# =========================================================
# CONFIG LƯƠNG SƠN TV
# =========================================================
TARGET_SITE   = "https://luongsontv60sv.com/"
BASE_URL      = "https://luongsontv60sv.com"
FILE_PATH     = "luongson.json"
LIMIT_MATCHES = 15 

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauluongson")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
LOGO_CACHE = {}

# =========================================================
# HELPER FUNCTIONS
# =========================================================
def make_id(seed: str = "") -> str:
    h = hashlib.md5((seed or str(uuid.uuid4())).encode()).hexdigest()
    return f"luongson-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

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

def parse_time_from_url(url: str) -> str:
    try:
        slug = url.rstrip('/').split('/')[-1]
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
        if m:
            y, mth, d, h, mn = map(int, m.groups())
            if mth > 12: mth, d = d, mth
            dt_utc = datetime.datetime(y, mth, d, h, mn)
            dt_vn = dt_utc + datetime.timedelta(hours=7)
            return dt_vn.strftime("%H:%M %d/%m/%Y")
        m2 = re.search(r'(\d{2})(\d{2})-(\d{2})-(\d{2})-(\d{4})', slug)
        if m2:
            hh, mm, dd, mo, yyyy = m2.groups()
            return f"{hh}:{mm} {dd}/{mo}/{yyyy}"
    except: pass
    return ""

def parse_teams_from_title(title: str):
    clean = re.sub(r'[-_]\d{4}-\d{2}-\d{2}[-_]\d{4}$', '', title)
    clean = re.sub(r'\.\s*[A-Za-z0-9 \-]{3,30}$', '', clean).strip()
    if re.fullmatch(r'[a-z0-9\-]+', clean): clean = clean.replace('-', ' ')
    m = re.split(r'\s+vs\.?\s+', clean, maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2 and m[0].strip() and m[1].strip(): return m[0].strip().title(), m[1].strip().title()
    return clean.strip().title(), "Unknown"

# =========================================================
# JS: LẤY DỮ LIỆU TỪ LƯƠNG SƠN TV
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();

    const anchors = Array.from(document.querySelectorAll('a[href]')).filter(a => {
        const h = a.href || '';
        if (h.includes('#') || h.endsWith('.online') || h.endsWith('.online/')) return false;
        if (h.includes('/lich-thi-dau') || h.includes('/ket-qua') || h.includes('/tin-tuc') || h.includes('nhan-dinh') || h.includes('highlight')) return false;
        return h.includes('/truc-tiep/') || h.includes('/match/') || h.includes('-vs-');
    });

    for (const a of anchors) {
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);

        let league = '';
        const leagueEl = a.querySelector('[class*="league" i], [class*="tournament" i], h3, h4');
        if (leagueEl) league = clean(leagueEl.innerText);

        const cardText = clean(a.innerText).toLowerCase();
        const isLiveUI = /live|trực tiếp|đang phát/.test(cardText) || !!a.querySelector('[class*="live" i]');

        let home = '', away = '';
        const teamNames = Array.from(a.querySelectorAll('.team-name, p[class*="team-name"]'));
        if (teamNames.length >= 2) {
            home = clean(teamNames[0].innerText);
            away = clean(teamNames[teamNames.length - 1].innerText);
        }

        let homeLogo = '', awayLogo = '';
        const teamLogos = Array.from(a.querySelectorAll('.team-logo, img[class*="team-logo"]'));
        if (teamLogos.length >= 2) {
            homeLogo = teamLogos[0].src;
            awayLogo = teamLogos[teamLogos.length - 1].src;
        }

        let fullText = clean(a.innerText || '');
        let timeStr = '';
        let scoreStr = '';
        
        let timeMatch = fullText.match(/(\\d{1,2}:\\d{2})/);
        let dateMatch = fullText.match(/(\\d{1,2}\\/\\d{1,2})/);
        if (timeMatch) {
            timeStr = timeMatch[1];
            if (dateMatch) timeStr += ' ' + dateMatch[1]; 
        }
        
        let scoreMatch = fullText.match(/(\\d+)\\s*[:\\-]\\s*(\\d+)/);
        if (scoreMatch) {
            scoreStr = scoreMatch[0];
            let minuteMatch = fullText.match(/\\b(H[T12]|FT|\\d{1,3}')\\b/i);
            if (minuteMatch) scoreStr = minuteMatch[0] + ' ' + scoreStr; 
        }
        
        let title = a.querySelector('.team-name') ? a.innerText : 'Match';
        let tournament = a.querySelector('.league-name')?.innerText?.trim() || '';
        
        results.push({ href, home, away, timeStr, scoreStr, isLiveUI, homeLogo, awayLogo, tournament });
    }
    return results;
}
"""

# =========================================================
# LƯỚI QUÉT SIÊU TỐC (CHỈ LẤY M3U8 + TIẾNG VIỆT CHUẨN)
# =========================================================
def capture_stream(page, match_url, global_seen_streams):
    stream_dict = {} # url -> tên BLV
    state = {"current_blv": "Mặc định"}
    BAD_KEYWORDS = ["quangcao", "tvc", ".ts"]
    
    # 💡 Lọc thẳng tay, CHỈ giữ lại M3U8
    def is_valid_m3u8(url_check):
        u = url_check.lower()
        return ".m3u8" in u and not any(bad in u for bad in BAD_KEYWORDS)

    def extract_current_dom(blv_name):
        try:
            api_data = page.evaluate("window.__apiData || []")
            for item in api_data:
                text = item.get("text", "")
                matches = re.findall(r'https?:\/\/[^"\'\s<>]+?\.m3u8[^"\'\s<>]*', text)
                for m in matches:
                    clean_link = m.replace('\\/', '/')
                    if is_valid_m3u8(clean_link):
                        if "cdnfaster-a.live/" in clean_link and "cdnfaster-a.live/live/" not in clean_link:
                            clean_link = clean_link.replace("cdnfaster-a.live/", "cdnfaster-a.live/live/")
                        if clean_link not in stream_dict:
                            stream_dict[clean_link] = blv_name
        except: pass
        for frame in page.frames:
            try:
                if is_valid_m3u8(frame.url) and frame.url not in stream_dict:
                    stream_dict[frame.url] = blv_name
            except: pass

    def handle_response(response):
        try:
            u = response.url
            if is_valid_m3u8(u):
                if "cdnfaster-a.live/" in u and "cdnfaster-a.live/live/" not in u:
                    u = u.replace("cdnfaster-a.live/", "cdnfaster-a.live/live/")
                if u not in stream_dict:
                    stream_dict[u] = state["current_blv"]
        except: pass

    page.on("response", handle_response)
    
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        try:
            vp = page.viewport_size
            if vp: page.mouse.click(vp["width"] // 2, vp["height"] // 2)
        except: pass

        # 1. Bóc Danh Sách BLV Chuẩn Tiếng Việt có dấu từ DOM
        blvs = page.evaluate("""() => {
            let links = [];
            document.querySelectorAll('a[href*="blv="]').forEach(a => {
                let name = a.innerText.split('\\n')[0].trim();
                name = name.replace(/Theo dõi/gi, '').replace(/BLV\\s*:\\s*/i, '').trim();
                if (!name.toUpperCase().includes("BLV")) name = "BLV " + name;
                links.push({href: a.href, name: name});
            });
            // Lọc trùng
            let unique = [];
            let seen = new Set();
            for(let l of links) {
                if(!seen.has(l.href)) {
                    seen.add(l.href);
                    unique.push(l);
                }
            }
            return unique;
        }""")

        if blvs:
            print(f"      > Phát hiện {len(blvs)} BLV. Đang quét thần tốc (Chỉ lấy M3U8)...")
            # Giới hạn 5 BLV để tránh làm Bot quá tải
            for blv in blvs[:5]: 
                state["current_blv"] = blv["name"]
                try:
                    page.evaluate(f"""(h) => {{
                        let btn = document.querySelector(`a[href="${{h}}"]`);
                        if(btn) btn.click();
                    }}""", blv["href"])
                except: pass
                
                # 💡 Polling Siêu Tốc: Dừng ngay lập tức khi tóm được 1 link cho BLV này!
                poll_deadline = time.time() + 2.5
                while time.time() < poll_deadline:
                    extract_current_dom(state["current_blv"])
                    if any(v == blv["name"] for v in stream_dict.values()):
                        break # Đã bắt được! Next BLV luôn!
                    time.sleep(0.3)
        else:
            # Fallback nếu phòng không có nút chọn BLV
            state["current_blv"] = "Mặc định"
            poll_deadline = time.time() + 4.0
            while time.time() < poll_deadline:
                extract_current_dom(state["current_blv"])
                if stream_dict: break
                time.sleep(0.4)
                
    except Exception as e:
        print(f"      ⚠️ Lỗi khi mở phòng Live: {e}")
    finally:
        try: page.remove_listener("response", handle_response)
        except: pass
        
    valid_streams = []
    
    # Gom link và tính điểm ưu tiên server
    for u, name in stream_dict.items():
        if u not in global_seen_streams:
            score = 0
            lo = u.lower()
            if "cdnfaster" in lo: score += 1000
            if "100ycdn" in lo: score += 500
            valid_streams.append({"name": name, "url": u, "score": score})
    
    valid_streams.sort(key=lambda x: x["score"], reverse=True)
    for s in valid_streams:
        s.pop("score", None)
        
    return valid_streams

# =========================================================
# XÂY DỰNG CẤU TRÚC JSON
# =========================================================
def build_channel(m: dict, stream_data: list) -> dict:
    home = m.get("home", "").title()
    away = m.get("away", "").title()
    thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', m.get("timeStr", ""))
    
    cid = make_id(m["href"])
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {m.get('tournament')}" if m.get('tournament') else "") + (f" | {thoi_gian}" if thoi_gian else "")

    is_live = len(stream_data) > 0
    
    if is_live:
        label_text = f"[x]  Live {m.get('scoreStr', '')}".strip()
    else:
        label_text = "🔴 Chờ stream" if m.get("isLiveUI") else "⏳ Chưa live"
        
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get("isLiveUI") else "#d54f1a")

    stream_links = []
    for idx, s in enumerate(stream_data):
        # Mặc định tất cả đều là HLS (M3U8)
        stream_links.append({
            "id": make_link_id(), 
            "name": s["name"], 
            "type": "hls", 
            "default": idx == 0, 
            "url": s["url"]
        })

    return {
        "id": cid, "name": display_name, 
        "tournament": m.get("tournament", ""),
        "logo_nha": m.get("homeLogo"), "logo_khach": m.get("awayLogo"),
        "type": "single", "display": "thumbnail-only", "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": m.get("homeLogo"), "width": 1600, "height": 1200},
        "labels": [{"text": label_text, "position": "top-left", "color": "#00ffffff", "text_color": label_color}],
        "sources": [{
            "id": cid, "name": "Lương Sơn",
            "contents": [{
                "id": cid, "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": stream_links}]
            }]
        }],
    }

# =========================================================
# CHƯƠNG TRÌNH CHÍNH
# =========================================================
def scrape_and_push():
    now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT LƯƠNG SƠN (Bản Siêu Tốc - Tên Chuẩn Tiếng Việt): {now_str}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", 
            "--disable-web-security",
            "--autoplay-policy=no-user-gesture-required", 
            "--disable-blink-features=AutomationControlled"
        ])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        apply_stealth(page)

        js_interceptor = r"""
        window.__apiData = [];
        const origFetch = window.fetch;
        window.fetch = async function(...args) {
            let reqUrl = (typeof args[0] === 'string') ? args[0] : (args[0] && args[0].url ? args[0].url : '');
            const response = await origFetch.apply(this, args);
            try { 
                response.clone().text().then(t => {
                    if (t.length > 50) window.__apiData.push({url: reqUrl, text: t});
                }).catch(()=>({})); 
            } catch(e) {}
            return response;
        };
        const origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._reqUrl = url;
            this.addEventListener('load', function() {
                if (this.responseText && this.responseText.length > 50) {
                    window.__apiData.push({url: this._reqUrl, text: this.responseText});
                }
            });
            origOpen.apply(this, arguments);
        };
        """
        page.add_init_script(js_interceptor)

        try: page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
        except: pass
        page.wait_for_timeout(5000)

        for _ in range(5):
            try:
                btn_xem_them = page.get_by_text("Xem thêm", exact=True).last
                if btn_xem_them.is_visible(timeout=2000):
                    btn_xem_them.click()
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1500)
            except:
                break 

        raw_matches = page.evaluate(JS_EXTRACT)
        valid_matches = []
        seen_keys = set()

        for m in raw_matches:
            h = (m.get("home") or "").strip()
            a = (m.get("away") or "").strip()
            if not h or not a or h == a or len(h) < 2:
                slug = m["href"].rstrip("/").split("/")[-1]
                fh, fa = parse_teams_from_title(slug)
                m["home"], m["away"] = fh, fa

            m["home"] = re.sub(r' vao luc.*$', '', m["home"], flags=re.IGNORECASE).strip()
            m["away"] = re.sub(r' vao luc.*$', '', m["away"], flags=re.IGNORECASE).strip()

            h_lower, a_lower = m["home"].lower(), m["away"].lower()
            
            if any(x in h_lower for x in ["unknown", "luongson", "#main", "nhan dinh", "nhận định"]) or \
               any(x in a_lower for x in ["unknown", "luongson", "#main", "nhan dinh", "nhận định"]):
                continue
                
            match_key = f"{h_lower} vs {a_lower}"
            if match_key not in seen_keys:
                seen_keys.add(match_key)
                valid_matches.append(m)

        raw_matches = valid_matches[:LIMIT_MATCHES]
        print(f"\n🎥 QUÉT TẤT CẢ {len(raw_matches)} TRẬN (BAO GỒM TRẬN SẮP TỚI)...")

        global_seen_streams = set()

        for idx, m in enumerate(raw_matches, 1):
            
            m["timeStr"] = m.get("timeStr") or parse_time_from_url(m["href"]) or "Không rõ"
            print(f"[{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            
            m["streams"] = []
            if m.get("isLiveUI") or any(char.isdigit() for char in m["timeStr"]):
                m["streams"] = capture_stream(page, m["href"], global_seen_streams)
                
                if m["streams"]:
                    print(f"      ✅ Đã tóm được {len(m['streams'])} link!")
                    for s in m["streams"]:
                        global_seen_streams.add(s["url"])
                else:
                    print("      ❌ Không có link.")
            
            m["homeLogo"] = get_final_logo(m["home"], m.get("homeLogo"))
            m["awayLogo"] = get_final_logo(m["away"], m.get("awayLogo"))

    channels = [build_channel(m, m["streams"]) for m in raw_matches]
    content = json.dumps({
        "id": "luongson", 
        "name": "Lương Sơn TV", 
        "last_updated": now_str, 
        "groups": [{"id": "live", "name": "🔴 Trực tiếp & Sắp tới", "channels": channels}]
    }, indent=2, ensure_ascii=False)
    
    if GITHUB_TOKEN:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        msg = "⚽ Sync Lương Sơn: " + now_str
        try:
            existing = repo.get_contents(FILE_PATH)
            repo.update_file(existing.path, msg, content, existing.sha)
            print("\n✅ Đã cập nhật thành công lên GitHub!")
        except:
            repo.create_file(FILE_PATH, msg, content)
            print("\n✅ Đã khởi tạo file mới trên GitHub!")

if __name__ == "__main__":
    scrape_and_push()
