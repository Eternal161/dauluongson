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
from playwright.sync_api import sync_playwright

# =========================================================
# BỘ GIÁP STEALTH
# =========================================================
def apply_stealth(page):
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except Exception: pass

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
        
        let tournament = a.querySelector('.league-name')?.innerText?.trim() || '';
        
        results.push({ href, home, away, timeStr, scoreStr, isLiveUI, homeLogo, awayLogo, tournament });
    }
    return results;
}
"""

# =========================================================
# LƯỚI QUÉT & SỔ ĐEN ĐỘC QUYỀN BLV
# =========================================================
def capture_stream(page, match_url, global_seen_blvs):
    found_urls = set()
    BAD_KEYWORDS = ["quangcao", "tvc", ".ts", "playlist", "ad.m3u8"]
    
    def is_valid_m3u8(url_check):
        u = url_check.lower()
        return ".m3u8" in u and not any(bad in u for bad in BAD_KEYWORDS)

    def get_blv_id(url):
        m = re.search(r'/live/([^/?#\.]+)', url, re.IGNORECASE)
        if m and m.group(1).upper() != "PLAYLIST":
            return m.group(1).upper()
        return None

    def handle_response(response):
        try:
            u = response.url
            if is_valid_m3u8(u):
                if "cdnfaster-a.live/" in u and "cdnfaster-a.live/live/" not in u:
                    u = u.replace("cdnfaster-a.live/", "cdnfaster-a.live/live/")
                found_urls.add(u)
        except: pass

    page.on("response", handle_response)
    
    dom_blvs = []
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        try:
            vp = page.viewport_size
            if vp: page.mouse.click(vp["width"] // 2, vp["height"] // 2)
        except: pass

        # Bóc tên Tiếng Việt chuẩn từ các nút chọn BLV
        dom_blvs = page.evaluate("""() => {
            let links = [];
            let currentPath = window.location.pathname.replace(/\\/$/, '');
            document.querySelectorAll('a[href*="blv="]').forEach(a => {
                try {
                    let u = new URL(a.href);
                    // Chỉ lấy nút thuộc trận này
                    if (u.pathname.replace(/\\/$/, '') === currentPath) {
                        let name = a.innerText.split('\\n')[0].trim();
                        name = name.replace(/Theo dõi.*/gi, '').replace(/BLV\\s*:\\s*/i, '').trim();
                        if (!name.toUpperCase().includes("BLV")) name = "BLV " + name;
                        links.push({href: a.href, name: name});
                    }
                } catch(e) {}
            });
            return links;
        }""")

        if dom_blvs:
            for blv in dom_blvs[:6]: 
                page.evaluate("window.__apiData = [];")
                try:
                    page.evaluate(f"""(h) => {{ document.querySelector(`a[href="${{h}}"]`)?.click(); }}""", blv["href"])
                except: pass
                
                poll_deadline = time.time() + 2.0
                while time.time() < poll_deadline:
                    try:
                        api_data = page.evaluate("window.__apiData || []")
                        for item in api_data:
                            matches = re.findall(r'https?:\/\/[^"\'\s<>]+?\.m3u8[^"\'\s<>]*', item.get("text", ""))
                            for m in matches:
                                cl = m.replace('\\/', '/')
                                if is_valid_m3u8(cl):
                                    if "cdnfaster-a.live/" in cl and "cdnfaster-a.live/live/" not in cl:
                                        cl = cl.replace("cdnfaster-a.live/", "cdnfaster-a.live/live/")
                                    found_urls.add(cl)
                    except: pass
                    time.sleep(0.3)
        else:
            page.wait_for_timeout(3000)
                
    except Exception as e:
        print(f"      ⚠️ Lỗi khi mở phòng: {e}")
    finally:
        try: page.remove_listener("response", handle_response)
        except: pass
        
    # ==============================================
    # 💡 BỘ LỌC SỔ ĐEN & GHÉP TÊN TIẾNG VIỆT
    # ==============================================
    def normalize_name(s):
        # Bỏ dấu Tiếng Việt và khoảng trắng để so khớp dễ hơn (VD: "LƯU BỊ" -> "LUUBI")
        return unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8').upper().replace(" ", "")
        
    valid_streams = []
    
    for u in found_urls:
        blv_id = get_blv_id(u)
        if not blv_id: continue # Bỏ qua nếu không có mã BLV (rác)
        
        # 1. KIỂM TRA SỔ ĐEN: BLV này đã có trận rồi thì đá văng luồng này ra khỏi JSON!
        if blv_id in global_seen_blvs:
            continue
            
        # 2. GHÉP TÊN TIẾNG VIỆT TỪ DOM
        nice_name = f"BLV {blv_id}" # Tên mặc định nếu không khớp được
        for b in dom_blvs:
            if blv_id in normalize_name(b["name"]):
                nice_name = b["name"] # Khớp thành công -> Lấy tên có dấu (VD: BLV LƯU BỊ)
                break
                
        score = 1000 if "cdnfaster" in u.lower() else (500 if "100ycdn" in u.lower() else 0)
        valid_streams.append({"name": nice_name, "url": u, "score": score, "blv_id": blv_id})
        
    valid_streams.sort(key=lambda x: x["score"], reverse=True)
    
    # 3. CHỐT SỔ ĐEN: Chỉ lấy 1 link xịn nhất cho mỗi BLV và thêm mã BLV vào Sổ đen toàn cục
    final_streams = []
    seen_local_blvs = set()
    
    for s in valid_streams:
        if s["blv_id"] not in seen_local_blvs:
            seen_local_blvs.add(s["blv_id"])
            global_seen_blvs.add(s["blv_id"]) # Ghi sổ đen, các trận sau khỏi xài lại BLV này!
            s.pop("score", None)
            s.pop("blv_id", None)
            final_streams.append(s)
            
    return final_streams

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
        label_text = f"● Live {m.get('scoreStr', '')}".strip()
    else:
        label_text = "🔴 Chờ stream" if m.get("isLiveUI") else "⏳ Chưa live"
        
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get("isLiveUI") else "#d54f1a")

    stream_links = []
    for idx, s in enumerate(stream_data):
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
    print(f"🚀 BẮT ĐẦU BOT LƯƠNG SƠN (Bản Phân Lô Tách Thửa BLV): {now_str}")

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

        # 💡 SỔ ĐEN TOÀN CỤC CHÍNH THỨC SỐNG LẠI (Dùng Mã BLV làm thẻ)
        global_seen_blvs = set()

        for idx, m in enumerate(raw_matches, 1):
            
            m["timeStr"] = m.get("timeStr") or parse_time_from_url(m["href"]) or "Không rõ"
            print(f"[{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            
            m["streams"] = []
            if m.get("isLiveUI") or any(char.isdigit() for char in m["timeStr"]):
                # Chuyển sổ đen vào hàm
                m["streams"] = capture_stream(page, m["href"], global_seen_blvs)
                
                if m["streams"]:
                    print(f"      ✅ Đã tóm được {len(m['streams'])} link!")
                else:
                    print("      ❌ Không có link hoặc đã thuộc về trận khác.")
            
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
