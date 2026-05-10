import os
import logging
import threading
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, abort, request, redirect, url_for, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from playwright.sync_api import sync_playwright

app = Flask(__name__, static_folder='static') # 強制指定 static 資料夾)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

BASE_RACECARD_URL = "https://racing.hkjc.com/en-us/local/information/racecard"
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

LOCAL_FALLBACK_RACES = [
    {"id": 1, "title": "Sha Tin R1 - Class 5 - 1200m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1200, "horses": [1, 2], "class": "Class 5", "time": "18:45"},
    {"id": 2, "title": "Sha Tin R2 - Class 4 - 1400m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1400, "horses": [3, 4], "class": "Class 4", "time": "19:15"},
    {"id": 3, "title": "Sha Tin R3 - Class 4 - 1600m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1600, "horses": [1, 3], "class": "Class 4", "time": "19:50"},
    {"id": 4, "title": "Sha Tin R4 - Class 3 - 1400m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1400, "horses": [2, 4], "class": "Class 3", "time": "20:20"},
    {"id": 5, "title": "Sha Tin R5 - Class 3 - 1800m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1800, "horses": [1, 4], "class": "Class 3", "time": "20:50"},
    {"id": 6, "title": "Sha Tin R6 - Class 2 - 1200m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1200, "horses": [2, 3], "class": "Class 2", "time": "21:20"},
    {"id": 7, "title": "Sha Tin R7 - Class 2 - 1600m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1600, "horses": [1, 2, 3], "class": "Class 2", "time": "21:50"},
    {"id": 8, "title": "Sha Tin R8 - Class 2 - 1400m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1400, "horses": [4], "class": "Class 2", "time": "22:20"},
    {"id": 9, "title": "Sha Tin R9 - Class 1 - 1200m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1200, "horses": [1], "class": "Class 1", "time": "22:50"},
    {"id": 10, "title": "Sha Tin R10 - Class 1 - 1600m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1600, "horses": [2], "class": "Class 1", "time": "23:20"},
    {"id": 11, "title": "Sha Tin R11 - Class 1 - 2000m", "date": "2026-05-10", "course": "Sha Tin", "distance": 2000, "horses": [3, 4], "class": "Class 1", "time": "23:50"},
]

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
    return name.replace(" ", "_").replace(".", "_").replace("/", "_").replace("-", "_").strip("_")

def make_dummy_race(race_id):
    horse_ids = [1, 2] if race_id % 4 == 1 else [2, 3] if race_id % 4 == 2 else [3, 4] if race_id % 4 == 3 else [1, 4]
    race = {
        "id": race_id,
        "title": f"Race {race_id} - Dummy Data",
        "date": "2026-05-10",
        "course": "Sha Tin",
        "distance": 1200 + (race_id % 4) * 200,
        "horses": horse_ids,
        "class": f"Class {5 - (race_id % 4)}",
        "time": f"{18 + race_id:02d}:45",
    }
    return race, LOCAL_FALLBACK_HORSES, LOCAL_FALLBACK_TRAINERS

def parse_racecard_page(html, racedate="", racecourse="", raceno=None):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "HKJC Race Card"
    parsed_races, parsed_horses, parsed_trainers = [], {}, {}
    horse_id = 1

    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        rows = table.find_all("tr")
        if not headers or len(rows) < 2:
            continue

        header_join = " | ".join(h.lower() for h in headers)
        if not any(key in header_join for key in ["horse", "trainer", "draw", "rtg", "wt", "weight"]):
            continue

        header_map = {h.lower(): i for i, h in enumerate(headers)}

        def find_idx(keys, default=None):
            for k in keys:
                if k in header_map:
                    return header_map[k]
            return default

        idx_draw = find_idx(["draw", "d"])
        idx_horse = find_idx(["horse", "horse name", "name"], 1)
        idx_weight = find_idx(["wt", "weight", "wgt"])
        idx_rating = find_idx(["rtg", "rating"])
        idx_trainer = find_idx(["trainer", "tr."])
        idx_form = find_idx(["form", "frm"])

        if not parsed_races:
            race_id = int(raceno) if raceno else 1
            parsed_races.append({
                "id": race_id,
                "title": title,
                "date": racedate,
                "course": racecourse,
                "distance": "",
                "horses": [],
                "class": "",
                "time": "",
            })

        for tr in rows[1:]:
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cols) < 2:
                continue

            horse_name = cols[idx_horse] if idx_horse is not None and idx_horse < len(cols) else cols[1]
            draw = cols[idx_draw] if idx_draw is not None and idx_draw < len(cols) else (cols[0] if len(cols) > 0 else "")
            weight = cols[idx_weight] if idx_weight is not None and idx_weight < len(cols) else ""
            rating = cols[idx_rating] if idx_rating is not None and idx_rating < len(cols) else ""
            trainer = cols[idx_trainer] if idx_trainer is not None and idx_trainer < len(cols) else ""
            form = cols[idx_form] if idx_form is not None and idx_form < len(cols) else ""

            trainer_id = slugify_trainer(trainer) if trainer else "unknown_trainer"
            if trainer_id not in parsed_trainers:
                parsed_trainers[trainer_id] = {"name": trainer or "Unknown", "horses": []}

            parsed_horses[horse_id] = {
                "id": horse_id,
                "name": horse_name,
                "trainer": trainer or "Unknown",
                "trainer_id": trainer_id,
                "draw": draw,
                "weight": weight,
                "rating": rating,
                "form": form,
                "official_link": BASE_RACECARD_URL,
            }
            parsed_trainers[trainer_id]["horses"].append(horse_id)
            parsed_races[0]["horses"].append(horse_id)
            horse_id += 1

    return parsed_races, parsed_horses, parsed_trainers

def load_real_data(racedate="", racecourse="", raceno=None):
    params = {}
    if racedate:
        params["racedate"] = racedate
    if racecourse:
        params["Racecourse"] = racecourse
    if raceno:
        params["RaceNo"] = raceno

    try:
        resp = requests.get(BASE_RACECARD_URL, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.raise_for_status()
        parsed_races, parsed_horses, parsed_trainers = parse_racecard_page(resp.text, racedate=racedate, racecourse=racecourse, raceno=raceno)
        if parsed_races:
            return parsed_races, parsed_horses, parsed_trainers
    except Exception as e:
        logger.exception("Live fetch failed, fallback used: %s", e)

    return LOCAL_FALLBACK_RACES, LOCAL_FALLBACK_HORSES, LOCAL_FALLBACK_TRAINERS

def build_race_detail(race, horses_map, trainers_map):
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
            ("Distance", f"{race.get('distance', '')}"),
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
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CHROME_CDP_URL)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        logger.exception("Failed to open remote chrome: %s", e)

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
    races_data, horses_data, trainers_data = load_real_data()
    q = request.args.get("q", "").strip().lower()
    course = request.args.get("course", "").strip()
    sort = request.args.get("sort", "id")

    filtered = races_data[:]
    if q:
        filtered = [r for r in filtered if q in str(r.get("title", "")).lower() or q in str(r.get("course", "")).lower()]
    if course:
        filtered = [r for r in filtered if r.get("course") == course]
    if sort == "date":
        filtered = sorted(filtered, key=lambda x: x.get("date", ""))
    elif sort == "distance":
        filtered = sorted(filtered, key=lambda x: x.get("distance", ""))
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

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", topbar_links=TOPBAR_LINKS), 404

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)