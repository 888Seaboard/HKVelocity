from email.mime import text
import os
import logging
import db
import threading
import requests
import re
import json
import datetime
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from flask import (
    Flask,
    render_template,
    abort,
    request,
    redirect,
    url_for,
    jsonify,
    session,
    send_from_directory,
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from playwright.sync_api import sync_playwright


app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────
# 在 app.py 頂部先定義 global 常數
# ─────────────────────────────────────


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


login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User(user_id) if user_id in USERS else None


def slugify_trainer(name):
    return re.sub(r"[^\w\s-]", "_", name.replace(" ", "_").strip("_"))


# ─────────────────────────────────────
# Config management
# ─────────────────────────────────────


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("config.json not found, using default config")
        return get_default_config()
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return get_default_config()


def get_default_config():
    now = datetime.datetime.now()
    return {
        "racedate": now.strftime("%Y/%m/%d"),
        "racecourse": "ST",
        "races": [
            {
                "race_no": i,
                "title": f"R{i}",
                "class": "Class 4",
                "time": f"{18 + i // 2}:{(i * 15) % 60:02d}",
                "distance": 1200 + (i % 3) * 200,
                "horses": [],
            }
            for i in range(1, 12)
        ],
    }


def save_config(config):
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Config saved to {config_path}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save config: {e}")
        return False


# ─────────────────────────────────────
# parse_racecard_page
# ─────────────────────────────────────


def parse_racecard_page(html, racedate="2026/05/13", racecourse="HV", raceno=None):
    soup = BeautifulSoup(html, "html.parser")

    race_info_div = soup.find("div", class_="f_fs13", style="line-height: 20px;")
    if race_info_div:
        text = race_info_div.get_text(" ", strip=True)

        title = f"第 {raceno} 場" if raceno else "第 1 場"
        m_title = re.search(r"第\s*(\d+)\s*場\s*-\s*(.+?)(?=\s+\w{3,}\s+\d{4}|\s+Turf|\s+All Weather|$)", text)
        if m_title:
            title = f"第 {raceno} 場 - {m_title.group(2).strip()}"

        distance = 1200
        m_distance = re.search(r"(\d+)\s*M", text, re.IGNORECASE)
        if m_distance:
            distance = int(m_distance.group(1))

        prize = ""
        m_prize = re.search(r"Prize Money:\s*([^,]+(?:,\s*[^,]+)*)", text, re.IGNORECASE)
        if m_prize:
            prize = m_prize.group(1).strip()

        rating = ""
        m_rating = re.search(r"Rating:\s*([0-9\-]+)", text, re.IGNORECASE)
        if m_rating:
            rating = m_rating.group(1).strip()

        race_class = ""
        m_class = re.search(r"(?:Class\s*([1-5])|第\s*([一二三四五])\s*班|([一二三四五])班)", text, re.IGNORECASE)
        if m_class:
            cls = m_class.group(1) or m_class.group(2) or m_class.group(3)
            if cls in ["1", "2", "3", "4", "5"]:
                race_class = f"第{cls}班"
            else:
                mapping = {"一": "第一班", "二": "第二班", "三": "第三班", "四": "第四班", "五": "第五班"}
                race_class = mapping.get(cls, "")
    else:
        title, distance, race_class, prize, rating = "第 ? 場", 1200, "Class 4", "", ""

    races = [
        {
            "id": int(raceno) if raceno else 1,
            "title": title,
            "date": racedate,
            "course": racecourse,
            "distance": distance,
            "class": race_class,
            "prize": prize,
            "rating": rating,
            "horses": [],
        }
    ]

    parsed_horses = {}
    parsed_trainers = {}

    table = soup.find("table", class_="starter")
    if table:
        tbody = table.find("tbody")
        if tbody:
            rows = tbody.find_all("tr")
            horse_id = 1

            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 15:
                    continue

                horse_no = cols[0].get_text(strip=True)
                form = cols[1].get_text(strip=True)
                silk_img = cols[2].img["src"] if cols[2].find("img") else ""
                horse_name = cols[3].get_text(strip=True)
                weight = cols[5].get_text(strip=True)
                jockey = cols[6].get_text(strip=True)
                draw = cols[8].get_text(strip=True)
                trainer = cols[9].get_text(strip=True)
                rating_no = cols[11].get_text(strip=True)
                rating_change = cols[12].get_text(strip=True)
                body_weight = cols[13].get_text(strip=True)
                gear = cols[-1].get_text(strip=True)

                trainer_id = slugify_trainer(trainer)
                horse = {
                    "id": horse_id,
                    "no": horse_no,
                    "name": horse_name,
                    "silk": silk_img,
                    "weight": weight,
                    "jockey": jockey,
                    "draw": draw,
                    "trainer": trainer,
                    "trainer_id": trainer_id,
                    "rating": rating_no,
                    "rating_change": rating_change,
                    "body_weight": body_weight,
                    "form": form,
                    "gear": gear,
                    "official_link": "",
                }
                parsed_horses[horse_id] = horse

                if trainer_id not in parsed_trainers:
                    parsed_trainers[trainer_id] = {"name": trainer, "horses": []}
                parsed_trainers[trainer_id]["horses"].append(horse_id)
                races[0]["horses"].append(horse_id)
                horse_id += 1

    return races, parsed_horses, parsed_trainers


# ─────────────────────────────────────
# Data loading & model service
# 這裡統一「一個入口」，以後你只改這裡就好
# ─────────────────────────────────────


def fetch_race_info(raceno, racedate="2026/05/13", racecourse="HV"):
    """單場賽事資訊，統一輸出格式： title, time, distance, class, course, date"""
    params = {
        "racedate": racedate.replace("-", "/"),
        "Racecourse": racecourse,
        "RaceNo": raceno,
    }
    try:
        resp = requests.get(
            BASE_RACECARD_URL,
            params=params,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        race_div = soup.find("div", class_="f_fs13", style="line-height: 20px;")
        if race_div:
            text = race_div.get_text(strip=True)

            # title
            match = re.search(r"第\s*(\d+)\s*場\s*[-\s]*(.+?)(?=\s+\w{3,}\s+\d{4}|\s+Turf|\s+All Weather|$)", text)
            title = f"第 {raceno} 場" if not match else f"第 {raceno} 場 - {match.group(2).strip()}"

            # time
            time_match = re.search(r"(\d{2}:\d{2})", text)
            time_str = time_match.group(1) if time_match else ""

            # distance
            distance = 0
            patterns = [r"(\d+)\s*米", r"(\d+)\s*M\b", r"(\d{3,4})\s*[mM]\b"]
            for p in patterns:
                m = re.search(p, text, re.IGNORECASE)
                if m:
                    distance = int(m.group(1))
                    break

            # class
            race_class = ""
            class_match = re.search(r"(?:Class\s*([1-5])|第\s*([一二三四五])\s*班|([一二三四五])班)", text, re.IGNORECASE)
            if class_match:
                cls = class_match.group(1) or class_match.group(2) or class_match.group(3)
                if cls in ["1", "2", "3", "4", "5"]:
                    race_class = f"第{cls}班"
                else:
                    mapping = {
                        "一": "第一班",
                        "二": "第二班",
                        "三": "第三班",
                        "四": "第四班",
                        "五": "第五班",
                    }
                    race_class = mapping.get(cls, "")

            return {
                "id": raceno,
                "title": title,
                "time": time_str,
                "distance": distance,
                "class": race_class,
                "course": racecourse,
                "date": racedate.replace("/", "-"),
            }
    except Exception as e:
        logger.warning(f"Failed to fetch race {raceno}: {e}")

    return {
        "id": raceno,
        "title": f"第 {raceno} 場",
        "time": "",
        "distance": "",
        "class": "",
        "course": "",
        "date": "",
    }


def load_all_race_buttons_from_hkjc():
    """一鍵抓 R1–R11 賽事標題，用於首頁按鈕，同時存入 session"""
    data = {}
    with ThreadPoolExecutor(max_workers=5) as exe:
        futures = [exe.submit(fetch_race_info, i) for i in range(1, 12)]
        for future in futures:
            r = future.result(timeout=15)
            if r:
                data[r["id"]] = r

    session["race_buttons"] = data
    session["race_buttons_updated_at"] = datetime.datetime.now().isoformat()
    session.modified = True
    return data


def get_race_buttons():
    """從 session 拿，自動刷新如果過舊（例如超過 5 分鐘）"""
    if "race_buttons" not in session or "race_buttons_updated_at" not in session:
        return load_all_race_buttons_from_hkjc()

    ts = datetime.datetime.fromisoformat(session["race_buttons_updated_at"])
    if datetime.datetime.now() - ts > datetime.timedelta(minutes=5):
        return load_all_race_buttons_from_hkjc()

    return session["race_buttons"]


def load_real_data(racedate=None, racecourse=None, raceno=None, use_real=False):
    """
    use_real=True: 抓單場真實數據
    use_real=False: 基於 config.json 資訊，只用於 index 首頁
    """
    if use_real and raceno:
        params = {
            "racedate": (racedate or "2026/05/13").replace("-", "/"),
            "Racecourse": racecourse or "HV",
            "RaceNo": raceno,
        }
        try:
            resp = requests.get(BASE_RACECARD_URL, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            resp.raise_for_status()
            races, horses, trainers = parse_racecard_page(
                resp.text,
                racedate or "2026/05/13",
                racecourse or "HV",
                raceno,
            )
            logger.info(f"✅ 真實數據：第{raceno}場 {races[0]['title']}")
            return races, horses, trainers
        except Exception as e:
            logger.error(f"❌ 無法取得真實數據：{e}")
            return [], LOCAL_FALLBACK_HORSES, LOCAL_FALLBACK_TRAINERS

    # 用 config.json 的資料（只用於首頁列賽事，不解析馬匹細節）
    config = load_config()
    racedate = racedate or config.get("racedate", datetime.date.today().strftime("%Y/%m/%d"))
    racecourse = racecourse or config.get("racecourse", "ST")

    races_data = []
    for race_config in config.get("races", []):
        race_no = race_config.get("race_no", 1)
        hkjc_url = f"https://racing.hkjc.com/zh-hk/local/information/racecard?racedate={racedate}&Racecourse={racecourse}&RaceNo={race_no}"

        race = {
            "id": race_no,
            "title": race_config.get("title", f"R{race_no}"),
            "class": race_config.get("class", "Class 4"),
            "time": race_config.get("time", "TBA"),
            "distance": race_config.get("distance", 1200),
            "date": racedate.replace("/", "-"),
            "course": racecourse,
            "horses": race_config.get("horses", []),
            "hkjc_url": hkjc_url,
        }
        races_data.append(race)

    return races_data, LOCAL_FALLBACK_HORSES, LOCAL_FALLBACK_TRAINERS


def make_dummy_race(race_id):
    """生成虛擬賽事數據（當無法從列表中找到時）"""
    horse_ids = (
        [1, 2]
        if race_id % 4 == 1
        else [2, 3]
        if race_id % 4 == 2
        else [3, 4]
        if race_id % 4 == 3
        else [1, 4]
    )
    race = {
        "id": race_id,
        "title": f"Race {race_id} - Dummy Data",
        "date": "2026-05-10",
        "course": "ST",
        "distance": 1200 + (race_id % 4) * 200,
        "horses": horse_ids,
        "class": f"Class {5 - (race_id % 4)}",
        "time": f"{18 + race_id:02d}:45",
        "hkjc_url": "",
    }
    return race, LOCAL_FALLBACK_HORSES, LOCAL_FALLBACK_TRAINERS

def build_race_detail(race, horses_map, trainers_map):
    race_horses = [horses_map[h_id] for h_id in race.get("horses", []) if h_id in horses_map]
    race_trainers = []
    seen = set()

    for h in race_horses:
        tid = h.get("trainer_id")
        if tid not in seen and tid in trainers_map:
            seen.add(tid)
            race_trainers.append({"id": tid, "name": trainers_map[tid]["name"]})

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

# ─────────────────────────────────────
# 路由
# ─────────────────────────────────────


@app.route("/public/<path:filename>")
def serve_public_files(filename):
    return send_from_directory(os.path.join(app.root_path, "public"), filename)



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
    race_buttons = get_race_buttons()
    races = []
    for n in range(1, 12):
        btn = race_buttons.get(n)

        # 防止 None
        if btn is None:
            btn = {}

        races.append(
            {
                "id": n,
                "title": btn.get("title", f"第 {n} 場"),
                "class": btn.get("class", ""),
                "time": btn.get("time", ""),
                "distance": btn.get("distance", ""),
                "course": btn.get("course", "HV"),
                "date": btn.get("date", "2026-05-13"),
            }
        )

    q = request.args.get("q", "").strip().lower()
    filtered = [r for r in races if q in r.get("title", "").lower()] if q else races

    courses = sorted(set(r.get("course", "") for r in races))

    return render_template(
        "index.html",
        races=filtered,
        q=q,
        courses=courses,
        topbar_links=TOPBAR_LINKS,
        race_buttons=race_buttons,
    )


@app.route("/force-refresh-races")
@login_required
def force_refresh_races():
    race_buttons = get_race_buttons()
    return jsonify(
        {
            "status": "success",
            "count": len(race_buttons),
            "message": "賽事資料已刷新",
            "sample": dict(list(race_buttons.items())[:2]),
        }
    )

@app.route("/race/<int:race_id>")
@login_required
def race_detail(race_id):
    races_real, horses_real, trainers_real = load_real_data(
        racedate="2026/05/13",
        racecourse="HV",
        raceno=race_id,
        use_real=True,
    )

    if races_real:
        race = races_real[0]
        race_horses = list(horses_real.values())
        race_trainers = list(trainers_real.values())

        summary = {
            "race_no": f"第 {race_id} 場",
            "class": race.get("class", ""),
            "distance": f"{race.get('distance', '')}米" if race.get("distance") else "",
            "title": race.get("title", "").split("-", 1)[-1].strip(),
            "prize": f"獎金: {race.get('prize', '')}" if race.get("prize") else "",
            "rating": f"評分: {race.get('rating', '')}" if race.get("rating") else "",
        }
        active_detail = {
            "type": "race",
            "title": race["title"],
            "rows": [
                ("場次", f"R{race_id}"),
                ("賽事", race["title"]),
            ],
        }
    else:
        race, fallback_horses, fallback_trainers = make_dummy_race(race_id)
        race_horses, race_trainers, summary, active_detail = build_race_detail(
            race, fallback_horses, fallback_trainers
        )

    return render_template(
        "race.html",
        race=race,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=[],  # 你之後可加
        active_detail=active_detail,
        topbar_links=TOPBAR_LINKS,
        race_buttons=get_race_buttons(),
        current_race=race_id,
    )



@app.route("/standards")
@login_required
def standards():
    standards = fetch_standard_times()
    now = datetime.datetime.now()
    return render_template(
        "standards.html",
        standards=standards,
        update_time=now.strftime("%Y-%m-%d %H:%M")
    )



@app.route("/api/standards")
@login_required
def api_standards():
    standards = fetch_standard_times()
    return jsonify(standards)



@app.route("/refresh-standards")
@login_required
def refresh_standards():
    standards = fetch_standard_times()
    return jsonify({
        "status": "success",
        "count": len(standards),
        "message": f"刷新完成 {len(standards)} 組途程",
        "data": standards
    })



@app.route("/rebuild-horses")
@login_required
def rebuild_horses():
    try:
        from scraper import scrape_hkjc_horses
        # 🔥 新版參數：每人 100 匹，30 個練馬師
        df = scrape_hkjc_horses(limit_per_trainer=100, max_trainers=30)
        db.save_horses(df.to_dict('records'))
        return f"""
        ✅ <b>超大規模爬取完成！</b><br>
        🏆 總計 <b>{len(df)}</b> 匹頂尖賽馬<br>
        📊 練馬師 <b>{len(set(row['trainer'] for row in df.to_dict('records')))}</b> 個<br>
        💾 已存入 <b>{db.DB_PATH}</b> + <b>horses.csv</b><br><br>
        <a href='/search-horse' class='btn'>🔍 試馬匹搜尋</a> 
        <a href='/race/9' class='btn'>🏁 排位表</a>
        """
    except Exception as e:
        return f"❌ 爬取失敗：{str(e)}<br><a href='/'>← 重試</a>"



@app.route("/admin/config", methods=["GET", "POST"])
@login_required
def edit_config():
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



@app.route("/api/update-buttons")
def api_update_buttons():
    race_buttons = get_race_buttons()
    return jsonify(race_buttons)



def is_valid_hkjc_url(url):
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc == "racing.hkjc.com"
    except:
        return False



@app.route("/proxy")
def hkjc_proxy():
    target_url = request.args.get("url")
    if not target_url or not is_valid_hkjc_url(target_url):
        abort(400)
    return redirect(target_url)



def open_remote_chrome(url: str):
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CHROME_CDP_URL)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        logger.exception("Failed to open remote chrome: %s", e)



@app.route("/open-browser")
@login_required
def open_browser():
    url = request.args.get("url", "").strip() or "https://racing.hkjc.com/zh-hk/local/information/fixture"
    threading.Thread(target=open_remote_chrome, args=(url,), daemon=True).start()
    proxy_url = url_for("hkjc_proxy", url=url)

    # 傳一場 dummy race 給 race.html
    dummy_race, dummy_horses, dummy_trainers = make_dummy_race(1)
    race_horses, race_trainers, summary, active_detail = build_race_detail(
        dummy_race, LOCAL_FALLBACK_HORSES, LOCAL_FALLBACK_TRAINERS
    )

    return render_template(
        "race.html",
        race=dummy_race,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=[],  # 你之後可加
        active_detail=active_detail,
        topbar_links=TOPBAR_LINKS,
        left_panel=None,
        right_panel=None,
        browser_url=proxy_url,
    )

def fetch_standard_times():
    data = {
        "ST_1000": {"G": "0.55.90", "1": "-", "2": "0.56.05", "3": "0.56.45", "4": "0.56.65", "5": "0.57.00", "M": "0.56.65"},
        "ST_1200": {"G": "1.08.15", "1": "1.08.45", "2": "1.08.65", "3": "1.09.00", "4": "1.09.35", "5": "1.09.55", "M": "1.09.90"},
        "ST_1400": {"G": "1.21.10", "1": "1.21.25", "2": "1.21.45", "3": "1.21.65", "4": "1.22.00", "5": "1.22.30", "M": "-"},
        "ST_1600": {"G": "1.33.90", "1": "1.34.05", "2": "1.34.25", "3": "1.34.70", "4": "1.34.90", "5": "1.35.45", "M": "-"},
        "ST_1800": {"G": "1.47.10", "1": "-", "2": "1.47.30", "3": "1.47.50", "4": "1.47.85", "5": "1.48.45", "M": "-"},
        "ST_2000": {"G": "2.00.50", "1": "2.01.20", "2": "2.01.70", "3": "2.01.90", "4": "2.02.35", "5": "2.02.65", "M": "-"},
        "ST_2400": {"G": "2.27.00", "1": "-", "2": "-", "3": "-", "4": "-", "5": "-", "M": "-"},
        "HV_1000": {"G": "-", "1": "-", "2": "0.56.40", "3": "0.56.65", "4": "0.57.20", "5": "0.57.35", "M": "-"},
        "HV_1200": {"G": "-", "1": "1.09.10", "2": "1.09.30", "3": "1.09.60", "4": "1.09.90", "5": "1.10.10", "M": "-"},
        "HV_1650": {"G": "-", "1": "1.39.10", "2": "1.39.30", "3": "1.39.90", "4": "1.40.10", "5": "1.40.30", "M": "-"},
        "HV_1800": {"G": "1.48.95", "1": "-", "2": "1.49.15", "3": "1.49.45", "4": "1.49.65", "5": "1.49.95", "M": "-"},
        "HV_2200": {"G": "-", "1": "-", "2": "-", "3": "-", "4": "2.16.60", "5": "2.17.05", "M": "-"},
        "AW_1200": {"G": "-", "1": "-", "2": "1.08.35", "3": "1.08.55", "4": "1.08.95", "5": "1.09.35", "M": "-"},
        "AW_1650": {"G": "-", "1": "1.37.80", "2": "1.38.40", "3": "1.38.60", "4": "1.39.05", "5": "1.39.45", "M": "-"},
        "AW_1800": {"G": "-", "1": "-", "2": "-", "3": "-", "4": "1.48.05", "5": "1.48.55", "M": "-"},
    }
    with open("standard_times.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ HKJC 標準時間載入：{len(data)} 組")
    return data


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", topbar_links=TOPBAR_LINKS), 404


app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

@app.route("/open-topbar-link")
@login_required
def open_topbar_link():
    target_url = request.args.get("url")
    return redirect(url_for("open_browser", url=target_url))





@app.route("/trainer/<trainer_id>")
@login_required
def trainer_detail(trainer_id):
    races_real, horses_real, trainers_real = load_real_data(
        racedate="2026/05/13",
        racecourse="HV",
        raceno=None,
        use_real=True,
    )

    if races_real:
        # 依家用 fall back 教練
        all_horses = list(horses_real.values())
        all_trainers = list(trainers_real.values())
    else:
        all_horses = list(LOCAL_FALLBACK_HORSES.values())
        all_trainers = list(LOCAL_FALLBACK_TRAINERS.values())

    trainer = next((t for t in all_trainers if t["id"] == trainer_id), None)

    if trainer:
        trainer_horses = [
            h for h in all_horses
            if h.get("trainer_id") == trainer_id
        ]
    else:
        # 喺 fallback 用
        trainer = next((t for t in LOCAL_FALLBACK_TRAINERS.values() if t["id"] == trainer_id), None)
        trainer_horses = []

        if not trainer:
            abort(404)

    detail = build_trainer_detail(trainer, trainer_horses)

    return render_template(
        "trainer.html",
        trainer=trainer,
        trainer_horses=trainer_horses,
        active_detail=detail,
        topbar_links=TOPBAR_LINKS,
        race_buttons=get_race_buttons(),
    )

# 🔥 加到 app2.py 入面（routes 部分）

@app.route("/horse-stats")
@login_required
def horse_stats():
    """馬匹統計面板"""
    import sqlite3
    import db  # 用你現有 db
    
    conn = sqlite3.connect(db.DB_PATH)
    stats = pd.read_sql_query("""
        SELECT trainer, 
               COUNT(*) as horse_count, 
               ROUND(AVG(total_races),1) as avg_races, 
               SUM(wins) as total_wins,
               ROUND(AVG(wins)*100.0/NULLIF(total_races,0),1) as win_rate
        FROM current_horses 
        GROUP BY trainer 
        ORDER BY horse_count DESC
    """, conn)
    conn.close()
    return render_template('horse_stats.html', stats=stats.to_dict('records'), total_horses=len(db.search_horse('')))

@app.route("/api/horse/<name>")
@login_required
def api_horse(name):
    """AJAX 即搜"""
    import db
    results = db.search_horse(name)
    return jsonify(results)





import json
import os


def save_horses(horses_list):
    """示範：用 JSON 存馬資料"""
    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", "horses.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(horses_list, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ 已儲存 {len(horses_list)} 匹馬到 {path}")
    return True

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)