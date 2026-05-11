import os
import logging
import threading
import requests
import re
import json
import datetime
from bs4 import BeautifulSoup
from flask import Flask, render_template, abort, request, redirect, url_for, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from playwright.sync_api import sync_playwright

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

BASE_RACECARD_URL = "https://racing.hkjc.com/zh-hk/local/information/racecard"
CHROME_CDP_URL = os.environ.get("CHROME_CDP_URL", "http://chrome:9222")

TOPBAR_LINKS = [
    {"label": "賽期表", "url": "https://racing.hkjc.com/zh-hk/local/information/fixture", "desc": "查看賽期安排"},
    {"label": "賽道選用", "url": "https://racing.hkjc.com/zh-hk/local/page/racing-course-select", "desc": "查看賽道選用"},
    {"label": "跑道標準", "url": "https://racing.hkjc.com/zh-hk/local/page/racing-course-time", "desc": "查看跑道標準"},
    {"label": "特別獎金馬", "url": "https://racing.hkjc.com/zh-hk/local/page/fwb-declared-starters", "desc": "查看特別獎金馬"},
]

USERS = {
    "toveythuang": generate_password_hash(os.environ.get("APP_PASSWORD", "HongKong852!"))
}

# Fallback 數據（當 config.json 不存在時使用）
LOCAL_FALLBACK_HORSES = {
    1: {"id": 1, "name": "嘉應高昇", "trainer": "大衛希斯", "trainer_id": "david_hayes", "draw": "1", "weight": "126", "rating": "140", "form": "1-1-1", "official_link": "https://racing.hkjc.com/zh-hk/local/information/horse?horseid=HK_2023_J062"},
    2: {"id": 2, "name": "浪漫勇士", "trainer": "沈集成", "trainer_id": "danny_shum", "draw": "2", "weight": "128", "rating": "135", "form": "1-2-1", "official_link": "https://racing.hkjc.com/zh-hk/local/information/horse?horseid=HK_2020_E486"},
    3: {"id": 3, "name": "燈胆將軍", "trainer": "黎昭昇", "trainer_id": "richard_lee", "draw": "3", "weight": "121", "rating": "92", "form": "2-3-1", "official_link": "https://racing.hkjc.com/zh-hk/local/information/horse?horseid=HK_2024_K218"},
    4: {"id": 4, "name": "美麗星晨", "trainer": "告東尼", "trainer_id": "tony_cruz", "draw": "4", "weight": "120", "rating": "88", "form": "4-2-2", "official_link": "https://racing.hkjc.com/zh-hk/local/information/horse?horseid=HK_2024_K491"},
}

LOCAL_FALLBACK_TRAINERS = {
    "david_hayes": {"name": "大衛希斯", "horses": [1]},
    "danny_shum": {"name": "沈集成", "horses": [2]},
    "richard_lee": {"name": "黎昭昇", "horses": [3]},
    "tony_cruz": {"name": "告東尼", "horses": [4]},
}


class User(UserMixin):
    def __init__(self, username):
        self.id = username


@login_manager.user_loader
def load_user(user_id):
    return User(user_id) if user_id in USERS else None


def slugify_trainer(name):
    return re.sub(r'[^\w\s-]', '_', name.replace(" ", "_").strip("_"))


# ======================== Config 管理 ========================

def load_config():
    """讀取 config.json 文件"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("config.json not found, using default config")
        return get_default_config()
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return get_default_config()


def get_default_config():
    """返回默認配置"""
    return {
        "racedate": datetime.date.today().strftime("%Y/%m/%d"),
        "racecourse": "ST",
        "races": [
            {
                "race_no": i,
                "title": f"R{i}",
                "class": "Class 4",
                "time": f"{18 + i // 2}:{(i * 15) % 60:02d}",
                "distance": 1200 + (i % 3) * 200
            }
            for i in range(1, 12)
        ]
    }


def save_config(config):
    """保存配置到 config.json"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Config saved to {config_path}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save config: {e}")
        return False


# ======================== 數據加載 ========================

def load_real_data(racedate="", racecourse=""):
    """
    從 config.json 讀取賽事數據
    
    Args:
        racedate: 格式 "YYYY/MM/DD" 或 "YYYY-MM-DD"（可選，不提供時用 config 的值）
        racecourse: 賽馬場代碼，如 "ST", "HV"（可選，不提供時用 config 的值）
    
    Returns:
        (races_data, all_horses, all_trainers)
    """
    config = load_config()
    
    # 使用提供的參數或 config 的默認值
    racedate = racedate or config.get("racedate", datetime.date.today().strftime("%Y/%m/%d"))
    racecourse = racecourse or config.get("racecourse", "ST")
    
    # 規范化日期格式：2026-05-10 → 2026/05/10
    if "-" in racedate:
        racedate = racedate.replace("-", "/")
    
    races_data = []
    
    # 從 config.json 構建賽事列表
    for race_config in config.get("races", []):
        race_no = race_config.get("race_no", 1)
        
        # 生成 HKJC 直連 URL
        hkjc_url = f"https://racing.hkjc.com/zh-hk/local/information/racecard?racedate={racedate}&Racecourse={racecourse}&RaceNo={race_no}"
        
        race = {
            "id": race_no,
            "title": race_config.get("title", f"R{race_no}"),
            "class": race_config.get("class", "Class 4"),
            "time": race_config.get("time", "TBA"),
            "distance": race_config.get("distance", 1200),
            "date": racedate.replace("/", "-"),  # 轉回 YYYY-MM-DD 格式用於顯示
            "course": racecourse,
            "horses": race_config.get("horses", []),  # 從 config 讀取馬匹 ID
            "hkjc_url": hkjc_url
        }
        
        races_data.append(race)
    
    # 使用 fallback horses 和 trainers
    all_horses = LOCAL_FALLBACK_HORSES
    all_trainers = LOCAL_FALLBACK_TRAINERS
    
    logger.info(f"✅ Loaded {len(races_data)} races from config")
    return races_data, all_horses, all_trainers


def make_dummy_race(race_id):
    """生成虛擬賽事數據（當無法從列表中找到時）"""
    horse_ids = [1, 2] if race_id % 4 == 1 else [2, 3] if race_id % 4 == 2 else [3, 4] if race_id % 4 == 3 else [1, 4]
    race = {
        "id": race_id,
        "title": f"Race {race_id} - Dummy Data",
        "date": "2026-05-10",
        "course": "ST",
        "distance": 1200 + (race_id % 4) * 200,
        "horses": horse_ids,
        "class": f"Class {5 - (race_id % 4)}",
        "time": f"{18 + race_id:02d}:45",
        "hkjc_url": ""
    }
    return race, LOCAL_FALLBACK_HORSES, LOCAL_FALLBACK_TRAINERS


# ======================== 數據轉換 ========================

def build_race_detail(race, horses_map, trainers_map):
    """構建賽事詳情"""
    race_horses = [horses_map[h_id] for h_id in race.get("horses", []) if h_id in horses_map]
    race_trainers = []
    seen = set()

    for h in race_horses:
        tid = h.get("trainer_id")
        if tid not in seen and tid in trainers_map:
            seen.add(tid)
            race_trainers.append({"id": tid, "name": trainers_map[tid]["name"]})

    active_detail = {
        "type": "race",
        "title": race.get("title", f"Race {race.get('id', '')}"),
        "rows": [
            ("Race", f"R{race.get('id', '')}"),
            ("Class", race.get("class", "")),
            ("Course", race.get("course", "")),
            ("Date", race.get("date", "")),
            ("Time", race.get("time", "")),
            ("Distance", f"{race.get('distance', '')}m"),
            ("Horses", str(len(race_horses))),
            ("Trainers", str(len(race_trainers))),
        ],
    }

    summary = {
        "race_no": race.get("id", ""),
        "class": race.get("class", ""),
        "course": race.get("course", ""),
        "date": race.get("date", ""),
        "time": race.get("time", ""),
        "distance": race.get("distance", ""),
        "horse_count": len(race_horses),
        "trainer_count": len(race_trainers),
    }
    return race_horses, race_trainers, summary, active_detail


def build_horse_detail(horse):
    """構建馬匹詳情"""
    return {
        "type": "horse",
        "title": horse.get("name", ""),
        "rows": [
            ("Horse", horse.get("name", "")),
            ("Trainer", horse.get("trainer", "")),
            ("Draw", str(horse.get("draw", ""))),
            ("Weight", str(horse.get("weight", ""))),
            ("Rating", str(horse.get("rating", ""))),
            ("Form", horse.get("form", "")),
        ],
    }


def build_trainer_detail(trainer, trainer_horses):
    """構建練馬師詳情"""
    return {
        "type": "trainer",
        "title": trainer.get("name", ""),
        "rows": [
            ("Trainer", trainer.get("name", "")),
            ("Horse Count", str(len(trainer_horses))),
            ("Horses", ", ".join(h.get("name", "") for h in trainer_horses)),
        ],
    }


def fetch_external_page(url):
    """抓取外部頁面內容"""
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else url
        body_text = soup.get_text("\n", strip=True)
        return {"ok": True, "title": title, "url": url, "body_text": body_text[:12000], "error": None}
    except Exception as e:
        logger.exception("Failed to fetch external page: %s", e)
        return {"ok": False, "title": "Fetch failed", "url": url, "body_text": "", "error": str(e)}


def open_remote_chrome(url: str):
    """打開遠程 Chrome 實例"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CHROME_CDP_URL)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        logger.exception("Failed to open remote chrome: %s", e)


# ======================== 路由 ========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username in USERS and check_password_hash(USERS[username], password):
            login_user(User(username))
            return redirect(request.args.get("next") or url_for("home"))
        error = "帳號或密碼錯誤"
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    # 獲取查詢參數
    q = request.args.get("q", "").strip().lower()
    course = request.args.get("course", "").strip()
    sort = request.args.get("sort", "id")
    racedate = request.args.get("racedate", "").strip()
    racecourse = request.args.get("racecourse", "").strip()
    
    races_data, horses_data, trainers_data = load_real_data(
        racedate=racedate, 
        racecourse=racecourse
    )

    filtered = races_data[:]
    if q:
        filtered = [r for r in filtered if q in str(r.get("title", "")).lower() or q in str(r.get("course", "")).lower()]
    if course:
        filtered = [r for r in filtered if r.get("course") == course]
    if sort == "date":
        filtered = sorted(filtered, key=lambda x: x.get("date", ""))
    elif sort == "distance":
        filtered = sorted(filtered, key=lambda x: x.get("distance", 0))
    else:
        filtered = sorted(filtered, key=lambda x: x.get("id", 0))

    courses = sorted(set(r.get("course", "") for r in races_data if r.get("course")))
    featured_horses = [horses_data[h_id] for h_id in [1, 2, 3, 4] if h_id in horses_data]
    race_track_notes = [
        ("跑道", "Sha Tin Turf"),
        ("特性", "視乎草地狀態與欄位位置"),
        ("前領", "部分情況可能較著數"),
        ("注意", "彎位與直路形勢會影響表現"),
    ]

    return render_template(
        "index.html",
        races=filtered,
        q=q,
        course=course,
        sort=sort,
        courses=courses,
        featured_horses=featured_horses,
        race_track_notes=race_track_notes,
        topbar_links=TOPBAR_LINKS,
    )


@app.route("/race/<int:race_id>")
@login_required
def race_detail(race_id):
    races_data, horses_data, trainers_data = load_real_data()
    race = next((r for r in races_data if int(r.get("id", 0)) == race_id), None)

    if race:
        race_horses, race_trainers, summary, active_detail = build_race_detail(race, horses_data, trainers_data)
        race_for_template = race
    else:
        dummy_race, dummy_horses, dummy_trainers = make_dummy_race(race_id)
        race_horses, race_trainers, summary, active_detail = build_race_detail(dummy_race, dummy_horses, dummy_trainers)
        race_for_template = dummy_race

    return render_template(
        "race.html",
        race=race_for_template,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=races_data,
        active_detail=active_detail,
        topbar_links=TOPBAR_LINKS,
        left_panel=None,
        right_panel=None,
        browser_url="",
    )


@app.route("/open-link")
@login_required
def open_link():
    url = request.args.get("url", "").strip()
    if not url:
        abort(400)

    fetched = fetch_external_page(url)
    races_data, horses_data, trainers_data = load_real_data()
    race = next((r for r in races_data if int(r.get("id", 0)) == 1), None)

    if race:
        race_horses, race_trainers, summary, active_detail = build_race_detail(race, horses_data, trainers_data)
        race_for_template = race
    else:
        dummy_race, dummy_horses, dummy_trainers = make_dummy_race(1)
        race_horses, race_trainers, summary, active_detail = build_race_detail(dummy_race, dummy_horses, dummy_trainers)
        race_for_template = dummy_race

    return render_template(
        "race.html",
        race=race_for_template,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=races_data,
        active_detail=active_detail,
        topbar_links=TOPBAR_LINKS,
        left_panel=fetched,
        right_panel=None,
        browser_url=url,
    )


@app.route("/open-topbar")
@login_required
def open_topbar_link():
    url = request.args.get("url", "").strip()
    if not url:
        abort(400)

    fetched = fetch_external_page(url)
    races_data, horses_data, trainers_data = load_real_data()
    race = next((r for r in races_data if int(r.get("id", 0)) == 1), None)

    if race:
        race_horses, race_trainers, summary, active_detail = build_race_detail(race, horses_data, trainers_data)
        race_for_template = race
    else:
        dummy_race, dummy_horses, dummy_trainers = make_dummy_race(1)
        race_horses, race_trainers, summary, active_detail = build_race_detail(dummy_race, dummy_horses, dummy_trainers)
        race_for_template = dummy_race

    proxy_url = url_for("hkjc_proxy", url=url)
    threading.Thread(target=open_remote_chrome, args=(url,), daemon=True).start()

    return render_template(
        "race.html",
        race=race_for_template,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=races_data,
        active_detail=active_detail,
        topbar_links=TOPBAR_LINKS,
        left_panel=None,
        right_panel=fetched,
        browser_url=proxy_url,
    )


@app.route("/open-browser")
@login_required
def open_browser():
    url = request.args.get("url", "").strip() or "https://racing.hkjc.com/zh-hk/local/information/fixture"
    races_data, horses_data, trainers_data = load_real_data()
    race = next((r for r in races_data if int(r.get("id", 0)) == 1), None)

    if race:
        race_horses, race_trainers, summary, active_detail = build_race_detail(race, horses_data, trainers_data)
        race_for_template = race
    else:
        dummy_race, dummy_horses, dummy_trainers = make_dummy_race(1)
        race_horses, race_trainers, summary, active_detail = build_race_detail(dummy_race, dummy_horses, dummy_trainers)
        race_for_template = dummy_race

    proxy_url = url_for("hkjc_proxy", url=url)
    threading.Thread(target=open_remote_chrome, args=(url,), daemon=True).start()

    return render_template(
        "race.html",
        race=race_for_template,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=races_data,
        active_detail=active_detail,
        topbar_links=TOPBAR_LINKS,
        left_panel=None,
        right_panel=None,
        browser_url=proxy_url,
    )


@app.route("/horse/<int:horse_id>")
@login_required
def horse_detail(horse_id):
    races_data, horses_data, trainers_data = load_real_data()
    horse = horses_data.get(horse_id)
    if not horse:
        abort(404)

    race = next((r for r in races_data if horse_id in r.get("horses", [])), None)
    if race:
        race_horses, race_trainers, summary, _ = build_race_detail(race, horses_data, trainers_data)
    else:
        race_horses, race_trainers, summary = [], [], None

    active_detail = build_horse_detail(horse)
    return render_template(
        "race.html",
        race=race,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=races_data,
        active_detail=active_detail,
        topbar_links=TOPBAR_LINKS,
        left_panel=None,
        right_panel=None,
        browser_url="",
    )


@app.route("/trainer/<trainer_id>")
@login_required
def trainer_detail(trainer_id):
    races_data, horses_data, trainers_data = load_real_data()
    trainer = trainers_data.get(trainer_id)
    if not trainer:
        abort(404)

    trainer_horses = [horses_data[h_id] for h_id in trainer.get("horses", []) if h_id in horses_data]
    race = next((r for r in races_data if any(h_id in r.get("horses", []) for h_id in trainer.get("horses", []))), None)

    if race:
        race_horses, race_trainers, summary, _ = build_race_detail(race, horses_data, trainers_data)
    else:
        race_horses, race_trainers, summary = [], [], None

    active_detail = build_trainer_detail(trainer, trainer_horses)
    return render_template(
        "race.html",
        race=race,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=races_data,
        active_detail=active_detail,
        topbar_links=TOPBAR_LINKS,
        left_panel=None,
        right_panel=None,
        browser_url="",
    )


@app.route("/calculator")
@login_required
def calculator():
    return render_template("calculator.html")


@app.route("/admin/config", methods=["GET", "POST"])
@login_required
def edit_config():
    """管理員配置編輯頁面"""
    if request.method == "POST":
        try:
            config = request.get_json()
            if save_config(config):
                return jsonify({"status": "success", "message": "Config saved successfully"})
            else:
                return jsonify({"status": "error", "message": "Failed to save config"}), 500
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return jsonify({"status": "error", "message": str(e)}), 400
    
    config = load_config()
    return render_template("admin_config.html", config=config)


@app.route("/proxy")
@login_required
def hkjc_proxy():
    """代理路由"""
    target_url = request.args.get("url")
    if not target_url:
        return "No URL provided", 400
    return redirect(target_url)


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", topbar_links=TOPBAR_LINKS), 404


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
