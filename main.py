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
from playwright_stealth import Stealth

# =========================================================
# CONFIG LƯƠNG SƠN TV - BẢN FULL CHỐNG ĐẠN
# =========================================================
TARGET_SITE   = "https://ls91.site/"
BASE_URL      = "https://ls91.site"
FILE_PATH     = "luongson.json"
LIMIT_MATCHES = 15 # Tăng số lượng để hiện được nhiều trận sắp tới hơn

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

# =========================================================
# XỬ LÝ THỜI GIAN (CHỐNG LỖI THÁNG 15)
# =========================================================
def parse_time_from_url(url: str) -> str:
    try:
        slug = url.rstrip('/').split('/')[-1]
        # Format: YYYY-MM-DD-HHMM
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
        if m:
            y, mth, d, h, mn = map(int, m.groups())
            if mth > 12: mth, d = d, mth
            dt_utc = datetime.datetime(y, mth, d, h, mn)
            dt_vn = dt_utc + datetime.timedelta(hours=7)
            return dt_vn.strftime("%H:%M %d/%m/%Y")
        # Format: HHMM-DD-MM-YYYY
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

        // 💡 BỘ LỌC THÔNG MINH MỚI: Tách bạch Thời gian và Tỷ số
        let fullText = clean(a.innerText || '');
        let timeStr = '';
        let scoreStr = '';
        
        // 1. Dò tìm Giờ (vd: 14:00) và Ngày (vd: 25/06)
        let timeMatch = fullText.match(/(\\d{1,2}:\\d{2})/);
        let dateMatch = fullText.match(/(\\d{1,2}\\/\\d{1,2})/);
        if (timeMatch) {
            timeStr = timeMatch[1];
            if (dateMatch) timeStr += ' ' + dateMatch[1]; // Thành phẩm: "14:00 25/06"
        }
        
        // 2. Dò tìm Tỷ số (vd: 0:0, 2-1) và Số phút (vd: H1, 45')
        let scoreMatch = fullText.match(/(\\d+)\\s*[:\\-]\\s*(\\d+)/);
        if (scoreMatch) {
            scoreStr = scoreMatch[0];
            let minuteMatch = fullText.match(/\\b(H[T12]|FT|\\d{1,3}')\\b/i);
            if (minuteMatch) scoreStr = minuteMatch[0] + ' ' + scoreStr; // Thành phẩm: "H1 0:0"
        }
        
        let title = a.querySelector('.team-name') ? a.innerText : 'Match';
        let tournament = a.querySelector('.league-name')?.innerText?.trim() || '';
        
        results.push({ href, home, away, timeStr, scoreStr, isLiveUI, homeLogo, awayLogo, tournament });
    }
    return results;
}
"""

def capture_stream(context, match_url: str, global_seen_streams: set) -> list:
    page = context.new_page()
    try:
        Stealth().apply_stealth_sync(page)
    except:
        pass

    def norm_key(s: str) -> str:
        s = s or ""
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return re.sub(r"[^A-Za-z0-9]", "", s).upper()

    def get_stream_key(url: str) -> str:
        m = re.search(r"/live/([^/?#]+)/", url, flags=re.I)
        return norm_key(m.group(1)) if m else ""

    seen_urls = set()
    current_captured = []
    BAD = [".gif", ".png", ".jpg", ".mp4", "saba.m3u8", "/ad/", "/ads/", "quangcao", "banner", "tvc.", "tvc/"]

    def process_url(url):
        u = url.lower()
        if ".m3u8" in u and not any(b in u for b in BAD):
            if "cdnfaster-a.live/" in u and "cdnfaster-a.live/live/" not in u:
                url = url.replace("cdnfaster-a.live/", "cdnfaster-a.live/live/")

            if url not in seen_urls and url not in global_seen_streams:
                seen_urls.add(url)
                current_captured.append(url)

    page.on("request", lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    streams_dict = {}

    def add_stream(url: str, name_hint: str = "", expected_key: str = ""):
        key = get_stream_key(url)
        if not key:
            key = norm_key(url)

        expected_key = norm_key(expected_key)

        # Nếu đang bấm BLV VÕ TÒNG thì chỉ nhận m3u8 /live/VOTONG/
        # Tránh lấy nhầm request cũ của LEO hoặc BLV khác.
        if expected_key and get_stream_key(url) and key != expected_key:
            return False

        if key not in streams_dict:
            name = name_hint or f"BLV {key}"
            streams_dict[key] = {
                "name": name,
                "url": url
            }
        return True

    def read_blv_data():
        return page.evaluate("""
        () => {
            const clean = t => (t || '').replace(/\\s+/g, ' ').trim();
            const norm = s => (s || '')
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-zA-Z0-9]/g, '')
                .toUpperCase();

            function pickName(text) {
                const lines = (text || '')
                    .split('\\n')
                    .map(clean)
                    .filter(Boolean);

                for (const line of lines) {
                    const m = line.match(/^BLV\\s*(.+)$/i);
                    if (m) return 'BLV ' + clean(m[1]);
                }

                const m2 = clean(text).match(/BLV\\s+[^0-9|•]+/i);
                return m2 ? clean(m2[0]) : '';
            }

            let current = '';
            const bodyLines = (document.body.innerText || '')
                .split('\\n')
                .map(clean)
                .filter(Boolean);

            // Ưu tiên dòng trên player: "BLV: BLV VÕ TÒNG"
            for (const line of bodyLines) {
                let m = line.match(/BLV:\\s*(BLV\\s*[^•|]+?)(?:\\s+Theo dõi|\\s+👁|\\s*$)/i);
                if (m) {
                    current = clean(m[1]);
                    break;
                }
            }

            // Fallback: đoạn mô tả bên dưới
            if (!current) {
                for (const line of bodyLines) {
                    let m = line.match(/Bình luận viên:\\s*(BLV\\s*[^.]+?)(?:\\s+Tỷ số|\\s+thuộc|\\s*$)/i);
                    if (m) {
                        current = clean(m[1]);
                        break;
                    }
                }
            }

            const currentPath = window.location.pathname.replace(/\\/$/, '');
            const best = new Map();

            document.querySelectorAll('a[href*="blv="]').forEach(a => {
                let url;
                try {
                    url = new URL(a.href);
                } catch(e) {
                    return;
                }

                if (url.pathname.replace(/\\/$/, '') !== currentPath) return;

                const keyRaw = url.searchParams.get('blv') || '';
                const key = norm(keyRaw);
                if (!key) return;

                const rawText = a.innerText || '';
                const text = clean(rawText);
                if (!text) return;
                if (/replay/i.test(text)) return;
                if (/vào phòng/i.test(text)) return;

                let name = pickName(rawText);
                if (!name) return;

                const item = {
                    key,
                    name,
                    href: a.href,
                    len: text.length
                };

                // Nếu DOM có nhiều thẻ trùng href/key, lấy thẻ có text ngắn nhất,
                // thường là card BLV thật, không phải container slider.
                const old = best.get(key);
                if (!old || item.len < old.len) {
                    best.set(key, item);
                }
            });

            const links = Array.from(best.values());
            const map = {};
            links.forEach(x => map[x.key] = x.name);

            return { current, links, map };
        }
        """)

    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=15000)

        try:
            vp = page.viewport_size
            if vp:
                page.mouse.click(vp["width"] // 2, vp["height"] // 2)
        except:
            pass

        deadline = time.time() + 6
        while time.time() < deadline:
            if current_captured:
                break
            time.sleep(0.5)

        blv_data = read_blv_data()
        blv_map = blv_data.get("map", {}) or {}

        # Luồng đang mở mặc định
        if current_captured:
            current_name = blv_data.get("current") or ""
            for u in list(dict.fromkeys(current_captured)):
                key = get_stream_key(u)
                name = blv_map.get(key) or current_name or f"BLV {key}"
                add_stream(u, name_hint=name)

        # Cào từng BLV khác
        for link in blv_data.get("links", []):
            link_name = link.get("name") or ""
            expected_key = link.get("key") or ""

            print(f"      > Đang cào thêm: {link_name} [{expected_key}]...")

            current_captured.clear()

            try:
                page.goto(link["href"], wait_until="domcontentloaded", timeout=10000)

                try:
                    vp = page.viewport_size
                    if vp:
                        page.mouse.click(vp["width"] // 2, vp["height"] // 2)
                except:
                    pass

                deadline = time.time() + 5
                while time.time() < deadline:
                    if current_captured:
                        break
                    time.sleep(0.5)

                page_blv_data = read_blv_data()
                page_current_name = page_blv_data.get("current") or link_name

                if current_captured:
                    accepted = False

                    for u in list(dict.fromkeys(current_captured)):
                        key = get_stream_key(u)
                        name = blv_map.get(key) or page_current_name or link_name or f"BLV {key}"

                        if add_stream(u, name_hint=name, expected_key=expected_key):
                            accepted = True

                    # Fallback nếu key trên URL web không trùng key trong m3u8
                    if not accepted:
                        for u in list(dict.fromkeys(current_captured)):
                            key = get_stream_key(u)
                            name = blv_map.get(key) or page_current_name or link_name or f"BLV {key}"
                            add_stream(u, name_hint=name)

            except Exception as e:
                print(f"      ⚠️ Lỗi khi cào {link_name}: {e}")

    except Exception as e:
        print(f"      ⚠️ Lỗi capture_stream: {e}")

    finally:
        page.close()

    streams = list(streams_dict.values())
    if not streams:
        return []

    for s in streams:
        score = 0
        lo = s["url"].lower()
        if "cdnfaster-a.live" in lo:
            score += 10000
        if "100ycdn" in lo:
            score += 5000
        s["score"] = score

    streams.sort(reverse=True, key=lambda x: x.get("score", 0))

    for s in streams:
        s.pop("score", None)

    return streams
# =========================================================
# XÂY DỰNG CẤU TRÚC JSON
# =========================================================
def build_channel(m: dict, stream_data: list) -> dict:
    home = m.get("home", "").title()
    away = m.get("away", "").title()
    thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', m.get("timeStr", ""))
    
    cid = make_id(m["href"])
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {m.get('league')}" if m.get('league') else "") + (f" | {thoi_gian}" if thoi_gian else "")

    # TRẠNG THÁI HIỂN THỊ
    is_live = len(stream_data) > 0
    
    # 💡 NẾU ĐANG LIVE -> BƠM TỶ SỐ VÀO LABEL ĐỂ FLUTTER HIỆN BẢNG TỶ SỐ
    if is_live:
        label_text = f"● Live {m.get('scoreStr', '')}".strip()
    else:
        label_text = "🔴 Chờ stream" if m.get("isLiveUI") else "⏳ Chưa live"
        
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get("isLiveUI") else "#d54f1a")

    # 💡 MAP DỮ LIỆU ĐA LUỒNG BLV VÀO JSON
    stream_links = []
    for idx, s in enumerate(stream_data):
        stream_links.append({
            "id": make_link_id(), 
            "name": s["name"] if s.get("name") else f"Link {idx+1}", 
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
    print(f"🚀 BẮT ĐẦU BOT LƯƠNG SƠN (Giờ VN): {now_str}  Untitled1:239 - ls.py:334")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass

        try: page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
        except: pass
        page.wait_for_timeout(5000)

        # Cuộn trang sâu hơn để lấy cả trận sắp tới
        # 💡 BẤM NÚT "XEM THÊM" ĐỂ MỞ KHÓA TẤT CẢ CÁC TRẬN
        for _ in range(5):
            try:
                # Tìm nút có chữ "Xem thêm" và bấm vào
                btn_xem_them = page.get_by_text("Xem thêm", exact=True).last
                if btn_xem_them.is_visible(timeout=2000):
                    btn_xem_them.click()
                
                # Cuộn xuống để load ảnh logo
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1500)
            except:
                break # Thoát vòng lặp nếu không còn nút Xem thêm

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
            
            # CHỐNG RÁC NHẬN ĐỊNH
            if any(x in h_lower for x in ["unknown", "luongson", "#main", "nhan dinh", "nhận định"]) or \
               any(x in a_lower for x in ["unknown", "luongson", "#main", "nhan dinh", "nhận định"]):
                continue
                
            match_key = f"{h_lower} vs {a_lower}"
            if match_key not in seen_keys:
                seen_keys.add(match_key)
                valid_matches.append(m)

        raw_matches = valid_matches[:LIMIT_MATCHES]
        print(f"\n🎥 QUÉT TẤT CẢ {len(raw_matches)} TRẬN (BAO GỒM TRẬN SẮP TỚI)...")

        # 💡 TẠO SỔ ĐEN TOÀN CỤC CHỐNG TRÙNG M3U8 GIỮA CÁC TRẬN
        global_seen_streams = set()

        for idx, m in enumerate(raw_matches, 1):
            m["timeStr"] = m.get("timeStr") or parse_time_from_url(m["href"]) or "Không rõ"
            print(f"[{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            
            m["streams"] = []
            if m.get("isLiveUI") or any(char.isdigit() for char in m["timeStr"]):
                # 💡 Truyền global_seen_streams vào trong
                m["streams"] = capture_stream(context, m["href"], global_seen_streams)
                
                # 💡 Bổ sung các luồng vừa cào được vào danh sách cấm của các trận đi sau
                for s in m["streams"]:
                    global_seen_streams.add(s["url"])
            
            m["homeLogo"] = get_final_logo(m["home"], m.get("homeLogo"))
            m["awayLogo"] = get_final_logo(m["away"], m.get("awayLogo"))

    # Đóng gói JSON
    channels = [build_channel(m, m["streams"]) for m in raw_matches]
    content = json.dumps({
        "id": "luongson", 
        "name": "Lương Sơn TV", 
        "last_updated": now_str, 
        "groups": [{"id": "live", "name": "🔴 Trực tiếp & Sắp tới", "channels": channels}]
    }, indent=2, ensure_ascii=False)
    
    # Đẩy lên GitHub
    if GITHUB_TOKEN:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        msg = "⚽ Sync Lương Sơn: " + now_str
        try:
            existing = repo.get_contents(FILE_PATH)
            repo.update_file(existing.path, msg, content, existing.sha)
            print("\n✅ Đã cập nhật thành công lên GitHub!  Untitled1:316 - ls.py:411")
        except:
            repo.create_file(FILE_PATH, msg, content)
            print("\n✅ Đã khởi tạo file mới trên GitHub!  Untitled1:319 - ls.py:414")

if __name__ == "__main__":
    scrape_and_push()
