import os
import logging
from flask import Flask, render_template, abort, request, redirect, url_for, flash
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

BASE_RACECARD_URL = "https://racing.hkjc.com/en-us/local/information/racecard"

USERS = {
    "admin": generate_password_hash(os.environ.get("APP_PASSWORD", "admin123"))
}

class User(UserMixin):
    def __init__(self, username):
        self.id = username

@login_manager.user_loader
def load_user(user_id):
    if user_id in USERS:
        return User(user_id)
    return None

LOCAL_FALLBACK_RACES = [
    {"id": 1, "title": "Sha Tin R1 - Class 5 - 1200m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1200, "horses": [1, 2, 3], "class": "Class 5", "time": "18:45"},
    {"id": 2, "title": "Sha Tin R2 - Class 4 - 1400m", "date": "2026-05-10", "course": "Sha Tin", "distance": 1400, "horses": [4, 5, 6], "class": "Class 4", "time": "19:15"},
]

LOCAL_FALLBACK_HORSES = {
    1: {"id": 1, "name": "Speed King", "trainer": "A. S. Cruz", "trainer_id": "A_S_Cruz", "draw": 3, "weight": 128, "rating": 52, "form": "10/8/7", "official_link": "https://racing.hkjc.com/"},
    2: {"id": 2, "name": "Lucky Hero", "trainer": "P. F. Yiu", "trainer_id": "P_F_Yiu", "draw": 7, "weight": 126, "rating": 49, "form": "6/3/5", "official_link": "https://racing.hkjc.com/"},
    3: {"id": 3, "name": "Golden Strike", "trainer": "C. S. Shum", "trainer_id": "C_S_Shum", "draw": 1, "weight": 122, "rating": 45, "form": "4/2/1", "official_link": "https://racing.hkjc.com/"},
    4: {"id": 4, "name": "Bright Future", "trainer": "D. J. Hall", "trainer_id": "D_J_Hall", "draw": 4, "weight": 130, "rating": 60, "form": "1/1/3", "official_link": "https://racing.hkjc.com/"},
    5: {"id": 5, "name": "Victory Star", "trainer": "F. C. Lor", "trainer_id": "F_C_Lor", "draw": 8, "weight": 125, "rating": 58, "form": "2/4/6", "official_link": "https://racing.hkjc.com/"},
    6: {"id": 6, "name": "Winning Dragon", "trainer": "J. Size", "trainer_id": "J_Size", "draw": 2, "weight": 123, "rating": 57, "form": "5/2/1", "official_link": "https://racing.hkjc.com/"},
}

LOCAL_FALLBACK_TRAINERS = {
    "A_S_Cruz": {"name": "A. S. Cruz", "horses": [1]},
    "P_F_Yiu": {"name": "P. F. Yiu", "horses": [2]},
    "C_S_Shum": {"name": "C. S. Shum", "horses": [3]},
    "D_J_Hall": {"name": "D. J. Hall", "horses": [4]},
    "F_C_Lor": {"name": "F. C. Lor", "horses": [5]},
    "J_Size": {"name": "J. Size", "horses": [6]},
}

def slugify_trainer(name):
    return name.replace(" ", "_").replace(".", "_").replace("/", "_").replace("-", "_").strip("_")

def parse_racecard_page(html, racedate="", racecourse="", raceno=None):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "HKJC Race Card"

    parsed_races = []
    parsed_horses = {}
    parsed_trainers = {}

    tables = soup.find_all("table")
    horse_id = 1

    for table in tables:
        header_cells = table.find_all("th")
        headers = [th.get_text(" ", strip=True) for th in header_cells]
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
                "time": ""
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
                "official_link": BASE_RACECARD_URL
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
        resp = requests.get(
            BASE_RACECARD_URL,
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )
        resp.raise_for_status()

        parsed_races, parsed_horses, parsed_trainers = parse_racecard_page(
            resp.text,
            racedate=racedate,
            racecourse=racecourse,
            raceno=raceno
        )

        if parsed_races:
            return parsed_races, parsed_horses, parsed_trainers

        logger.warning("No parsed race data from live page, fallback used.")
    except Exception as e:
        logger.exception(f"Live fetch failed, fallback used: {e}")

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
        "items": [
            ("Race", f"R{race.get('id', '')}"),
            ("Class", race.get("class", "")),
            ("Course", race.get("course", "")),
            ("Date", race.get("date", "")),
            ("Time", race.get("time", "")),
            ("Distance", f"{race.get('distance', '')}"),
            ("Horses", str(len(race_horses))),
            ("Trainers", str(len(race_trainers))),
        ]
    }
    return race_horses, race_trainers, summary, active_detail

def build_horse_detail(horse):
    return {
        "type": "horse",
        "title": horse.get("name", ""),
        "items": [
            ("Horse", horse.get("name", "")),
            ("Trainer", horse.get("trainer", "")),
            ("Draw", str(horse.get("draw", ""))),
            ("Weight", str(horse.get("weight", ""))),
            ("Rating", str(horse.get("rating", ""))),
            ("Form", horse.get("form", "")),
        ]
    }

def build_trainer_detail(trainer, trainer_horses):
    return {
        "type": "trainer",
        "title": trainer.get("name", ""),
        "items": [
            ("Trainer", trainer.get("name", "")),
            ("Horse Count", str(len(trainer_horses))),
            ("Horses", ", ".join(h.get("name", "") for h in trainer_horses)),
        ]
    }

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
            next_url = request.args.get("next")
            return redirect(next_url or url_for("home"))

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
    return render_template("index.html", races=filtered, q=q, course=course, sort=sort, courses=courses)

@app.route("/race/<int:race_id>")
@login_required
def race_detail(race_id):
    races_data, horses_data, trainers_data = load_real_data()
    race = next((r for r in races_data if int(r.get("id", 0)) == race_id), None)
    if not race:
        abort(404)

    race_horses, race_trainers, summary, active_detail = build_race_detail(race, horses_data, trainers_data)
    return render_template(
        "race.html",
        race=race,
        race_horses=race_horses,
        race_trainers=race_trainers,
        summary=summary,
        quick_races=races_data,
        active_detail=active_detail
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
        active_detail=active_detail
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
        active_detail=active_detail
    )

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

if __name__ == "__main__":
    app.run(debug=True)