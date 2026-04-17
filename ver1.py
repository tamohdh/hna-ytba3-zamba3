"""
Building Energy Forecaster
Full hierarchy: Building → Floors → Apartments → Rooms
Master Year Project
"""

import streamlit as st
import sqlite3
import json
import math
import copy
import pandas as pd
from datetime import datetime

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Building Energy Forecaster",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
CLIMATE_ZONES = {
    "Coast":     {"cooling": 1.1, "heating": 0.8,  "desc": "Humid, moderate temperatures"},
    "Desert":    {"cooling": 1.4, "heating": 0.6,  "desc": "Hot days, cool nights, high solar"},
    "Mountains": {"cooling": 0.6, "heating": 1.5,  "desc": "Cold winters, mild summers"},
    "City":      {"cooling": 1.0, "heating": 1.0,  "desc": "Urban heat island, baseline"},
}

LOCATION_MULTIPLIERS = {
    "Urban":    1.0,
    "Suburban": 0.95,
    "Rural":    0.9,
}

WALL_U_VALUES = {
    "Brick":            1.84,
    "Concrete":         2.00,
    "Block":            1.50,
    "Insulated (5cm)":  0.80,
    "Insulated (10cm)": 0.35,
}

WINDOW_U_VALUES = {
    "Single": 5.82,
    "Double": 2.70,
}

APPLIANCE_TEMPLATES = [
    {"name": "Refrigerator",        "watts": 150,  "hours": 24},
    {"name": "AC (window unit)",    "watts": 1000, "hours": 8},
    {"name": "AC (split)",          "watts": 800,  "hours": 8},
    {"name": "TV (LED)",            "watts": 100,  "hours": 5},
    {"name": "TV (OLED)",           "watts": 150,  "hours": 5},
    {"name": "Computer (desktop)",  "watts": 200,  "hours": 8},
    {"name": "Computer (laptop)",   "watts": 65,   "hours": 8},
    {"name": "Washing machine",     "watts": 500,  "hours": 1},
    {"name": "Clothes dryer",       "watts": 2500, "hours": 1},
    {"name": "Dishwasher",          "watts": 1200, "hours": 1},
    {"name": "Microwave",           "watts": 1000, "hours": 0.5},
    {"name": "Electric oven",       "watts": 2000, "hours": 1},
    {"name": "Water heater",        "watts": 1500, "hours": 2},
    {"name": "Lighting (per room)", "watts": 15,   "hours": 5},
]

EUI_BENCHMARKS = [
    (50,  "Excellent — Passive/Net-Zero standard"),
    (100, "Good — Energy-efficient building"),
    (150, "Average — Standard construction"),
    (200, "Below average — Needs improvement"),
    (300, "Poor — Major upgrade recommended"),
    (999, "Very poor — Urgent action required"),
]

DB_PATH      = "building_energy.db"
HOURS_IN_YEAR = 8760

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            created_date  TEXT NOT NULL,
            building_json TEXT NOT NULL,
            floors_json   TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_project_to_db(project_name, building_data, floors_data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO projects (name, created_date, building_json, floors_json) VALUES (?,?,?,?)",
        (
            project_name,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            json.dumps(building_data),
            json.dumps(floors_data),
        ),
    )
    conn.commit()
    conn.close()

def load_all_projects():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, created_date FROM projects ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def load_project_from_db(project_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT building_json, floors_json FROM projects WHERE id=?", (project_id,))
    row = c.fetchone()
    conn.close()
    if row:
        st.session_state.building = json.loads(row[0])
        raw = json.loads(row[1])
        st.session_state.floors = {int(k): v for k, v in raw.items()}
        return True
    return False

def delete_project_from_db(project_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────
# DEFAULT DATA FACTORIES
# ─────────────────────────────────────────────
def default_room(name="Room 1"):
    """A room holds all the physical + appliance data (previously on the apartment)."""
    return {
        "name":                      name,
        "area_m2":                   20.0,
        "wall_thickness_cm":         25,
        "wall_material":             "Brick",
        "window_count":              2,
        "window_area_per_window_m2": 1.5,
        "window_type":               "Single",
        "plug_count":                4,
        "ceiling_height_m":          2.5,
        "appliances":                [],
    }

def default_apartment(name="Apartment A"):
    """An apartment is a container for rooms."""
    return {
        "name":  name,
        "rooms": {
            "1": default_room("Living Room"),
            "2": default_room("Bedroom"),
        },
    }

def default_floors(num_floors=3):
    floors = {}
    for f in range(1, num_floors + 1):
        floors[f] = {
            "apartments": {
                "A": default_apartment("Apartment A"),
                "B": default_apartment("Apartment B"),
            }
        }
    return floors

def next_apt_key(existing_keys):
    used = set(existing_keys)
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ch not in used:
            return ch
    return str(len(existing_keys) + 1)

def next_room_key(existing_keys):
    used = set(str(k) for k in existing_keys)
    for i in range(1, 100):
        if str(i) not in used:
            return str(i)
    return str(len(existing_keys) + 1)

def next_floor_num(floors_dict):
    return max(floors_dict.keys()) + 1 if floors_dict else 1

# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────
def init_session_state():
    if "building" not in st.session_state:
        st.session_state.building = {
            "name":                  "My Building",
            "num_floors":            3,
            "climate_zone":          "Coast",
            "location_type":         "Urban",
            "electricity_price_usd": 0.12,
            "exchange_rate":         135.0,
            "created_date":          datetime.now().strftime("%Y-%m-%d"),
        }
    if "floors" not in st.session_state:
        st.session_state.floors = default_floors(3)
    if "templates" not in st.session_state:
        st.session_state.templates = {}
    if "current_floor" not in st.session_state:
        st.session_state.current_floor = 1
    if "current_apartment" not in st.session_state:
        st.session_state.current_apartment = "A"
    if "current_room" not in st.session_state:
        st.session_state.current_room = "1"
    if "page" not in st.session_state:
        st.session_state.page = "Dashboard"
    if "show_cost" not in st.session_state:
        st.session_state.show_cost = True

def get_current_room():
    """Return the currently selected room dict (safe)."""
    floors = st.session_state.floors
    cf = st.session_state.current_floor
    ca = st.session_state.current_apartment
    cr = st.session_state.current_room

    if cf not in floors:
        cf = sorted(floors.keys())[0]
        st.session_state.current_floor = cf
    apts = floors[cf]["apartments"]
    if ca not in apts:
        ca = sorted(apts.keys())[0]
        st.session_state.current_apartment = ca
    rooms = apts[ca]["rooms"]
    if cr not in rooms:
        cr = sorted(rooms.keys())[0]
        st.session_state.current_room = cr
    return floors[cf]["apartments"][ca]["rooms"][cr]

# ─────────────────────────────────────────────
# CALCULATIONS  (room → apartment → building)
# ─────────────────────────────────────────────
def calculate_room_electricity(room, climate_zone, location_type):
    """kWh/year breakdown for one room."""
    area             = room.get("area_m2", 20.0)
    wall_u           = WALL_U_VALUES.get(room.get("wall_material", "Brick"), 1.84)
    window_count     = room.get("window_count", 2)
    window_area_each = room.get("window_area_per_window_m2", 1.5)
    window_u         = WINDOW_U_VALUES.get(room.get("window_type", "Single"), 5.82)
    window_area_total = window_count * window_area_each
    ceiling_h        = room.get("ceiling_height_m", 2.5)
    plug_count       = room.get("plug_count", 4)

    side       = math.sqrt(max(area, 1))
    gross_wall = 4 * side * ceiling_h
    wall_area  = max(gross_wall - window_area_total, 0)

    delta_t      = 15
    cooling_days = 120
    cooling_hrs  = 8

    wall_load   = wall_area   * wall_u   * delta_t * cooling_hrs * cooling_days / 1000
    window_load = window_area_total * window_u * delta_t * cooling_hrs * cooling_days / 1000

    appliance_total     = 0.0
    appliance_breakdown = []
    for appl in room.get("appliances", []):
        w   = appl.get("watts", 0)
        h   = appl.get("hours", 0)
        n   = appl.get("name", "Unknown")
        nl  = n.lower()
        days = 120 if ("ac" in nl or "air con" in nl) else 365
        kwh  = (w * h * days) / 1000
        appliance_total += kwh
        appliance_breakdown.append({"name": n, "kwh": kwh, "watts": w, "hours": h})

    plug_load = plug_count * 50 * 8 * 365 / 1000

    climate_mult = CLIMATE_ZONES.get(climate_zone, {}).get("cooling", 1.0)
    loc_mult     = LOCATION_MULTIPLIERS.get(location_type, 1.0)
    mult         = climate_mult * loc_mult

    total = (wall_load + window_load + appliance_total + plug_load) * mult

    return {
        "wall_load":           wall_load   * mult,
        "window_load":         window_load * mult,
        "appliance_total":     appliance_total * mult,
        "plug_load":           plug_load   * mult,
        "total":               total,
        "appliance_breakdown": appliance_breakdown,
        "area_m2":             area,
    }

def calculate_apartment_total(apt, climate_zone, location_type):
    """Sum of all rooms in an apartment."""
    total        = 0.0
    total_area   = 0.0
    room_results = {}
    for room_key, room_data in apt.get("rooms", {}).items():
        res = calculate_room_electricity(room_data, climate_zone, location_type)
        total      += res["total"]
        total_area += res["area_m2"]
        room_results[room_key] = res
    return {"total": total, "total_area": total_area, "room_results": room_results}

def calculate_building_total(floors_data, climate_zone, location_type):
    """Full building summary."""
    building_total = 0.0
    total_area     = 0.0
    floor_totals   = {}
    apt_results    = {}

    for floor_num, floor_data in floors_data.items():
        floor_total = 0.0
        for apt_key, apt_data in floor_data.get("apartments", {}).items():
            apt_res = calculate_apartment_total(apt_data, climate_zone, location_type)
            floor_total    += apt_res["total"]
            building_total += apt_res["total"]
            total_area     += apt_res["total_area"]
            apt_results[(floor_num, apt_key)] = apt_res
        floor_totals[floor_num] = floor_total

    eui = building_total / total_area if total_area > 0 else 0
    return {
        "building_total": building_total,
        "total_area":     total_area,
        "eui":            eui,
        "floor_totals":   floor_totals,
        "apt_results":    apt_results,
    }

def get_eui_benchmark(eui):
    for threshold, label in EUI_BENCHMARKS:
        if eui <= threshold:
            return label
    return "Very poor — Urgent action required"

# ─────────────────────────────────────────────
# COST HELPERS
# ─────────────────────────────────────────────
def cost_per_year(kwh):
    price = st.session_state.building.get("electricity_price_usd", 0.12)
    rate  = st.session_state.building.get("exchange_rate", 135.0)
    usd   = kwh * price
    dzd   = usd * rate
    return usd, dzd

def cost_per_hour(kwh):
    usd_yr, dzd_yr = cost_per_year(kwh)
    return usd_yr / HOURS_IN_YEAR, dzd_yr / HOURS_IN_YEAR

def fmt_cost_year(kwh):
    usd, dzd = cost_per_year(kwh)
    return f"${usd:,.2f} USD  /  {dzd:,.0f} DZD"

def fmt_cost_hour(kwh):
    usd, dzd = cost_per_hour(kwh)
    return f"${usd:,.4f} USD/hr  /  {dzd:,.3f} DZD/hr"

# ─────────────────────────────────────────────
# JSON EXPORT / IMPORT
# ─────────────────────────────────────────────
def export_to_json():
    return json.dumps(
        {
            "building":  st.session_state.building,
            "floors":    {str(k): v for k, v in st.session_state.floors.items()},
            "templates": st.session_state.templates,
        },
        indent=2,
    )

def import_from_json(content):
    try:
        data = json.loads(content)
        st.session_state.building  = data["building"]
        st.session_state.floors    = {int(k): v for k, v in data["floors"].items()}
        st.session_state.templates = data.get("templates", {})
        return True, "Project imported successfully!"
    except Exception as e:
        return False, f"Import failed: {e}"

# ─────────────────────────────────────────────
# EXAMPLE BUILDING
# ─────────────────────────────────────────────
def load_example_building():
    st.session_state.building = {
        "name":                  "Example Building",
        "num_floors":            3,
        "climate_zone":          "Coast",
        "location_type":         "Urban",
        "electricity_price_usd": 0.12,
        "exchange_rate":         135.0,
        "created_date":          datetime.now().strftime("%Y-%m-%d"),
    }
    sample_appliances = [
        {"name": "Refrigerator", "watts": 150,  "hours": 24},
        {"name": "AC (split)",   "watts": 800,  "hours": 8},
        {"name": "TV (LED)",     "watts": 100,  "hours": 5},
    ]

    def make_room(name, area, appliances):
        r = default_room(name)
        r["area_m2"]    = area
        r["appliances"] = [dict(a) for a in appliances]
        return r

    def make_apt(apt_name, area_living, area_bed):
        return {
            "name": apt_name,
            "rooms": {
                "1": make_room("Living Room", area_living, sample_appliances),
                "2": make_room("Bedroom",     area_bed,    [{"name": "TV (LED)", "watts": 100, "hours": 5}]),
            },
        }

    st.session_state.floors = {
        1: {"apartments": {"A": make_apt("Apartment 1A", 35, 20), "B": make_apt("Apartment 1B", 30, 18)}},
        2: {"apartments": {"A": make_apt("Apartment 2A", 35, 20), "B": make_apt("Apartment 2B", 30, 18)}},
        3: {"apartments": {"A": make_apt("Apartment 3A", 35, 20)}},
    }
    st.session_state.current_floor     = 1
    st.session_state.current_apartment = "A"
    st.session_state.current_room      = "1"

# ─────────────────────────────────────────────
# SIDEBAR — 4-LEVEL HIERARCHY TREE
# ─────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.markdown("## 🏢 Energy Forecaster")
        st.caption("Master Year Project")
        st.divider()

        # ── Top nav buttons ──────────────────
        for icon, pg in [
            ("📈", "Results & Forecast"),
            ("⚙️", "Building Settings"),
            ("📁", "Apartment Templates"),
            ("💾", "Save/Load Project"),
            ("❓", "Help"),
        ]:
            if st.button(
                f"{icon} {pg}",
                use_container_width=True,
                type="primary" if st.session_state.page == pg else "secondary",
                key=f"nav_{pg}",
            ):
                st.session_state.page = pg
                st.rerun()

        st.divider()

        # ── Building label ───────────────────
        b = st.session_state.building
        if st.button(f"🏢  {b['name']}", use_container_width=True, key="nav_bld"):
            st.session_state.page = "Building Settings"
            st.rerun()
        st.caption(f"Zone: {b['climate_zone']}  |  {b['location_type']}")

        # ── Tree ────────────────────────────
        floors     = st.session_state.floors
        floor_nums = sorted(floors.keys())

        cf = st.session_state.current_floor
        ca = st.session_state.current_apartment
        cr = st.session_state.current_room

        for floor_num in floor_nums:
            apts           = floors[floor_num]["apartments"]
            is_active_floor = floor_num == cf

            with st.expander(
                f"📐 Floor {floor_num}  ({len(apts)} apt{'s' if len(apts)!=1 else ''})",
                expanded=is_active_floor,
            ):
                # ── Per-floor actions ────────
                fc1, fc2, fc3 = st.columns(3)
                with fc1:
                    if st.button("➕ Apt", key=f"add_apt_{floor_num}",
                                 help="Add apartment", use_container_width=True):
                        nk = next_apt_key(list(apts.keys()))
                        apts[nk] = default_apartment(f"Apartment {nk}")
                        st.session_state.current_floor     = floor_num
                        st.session_state.current_apartment = nk
                        st.session_state.current_room      = sorted(apts[nk]["rooms"].keys())[0]
                        st.session_state.page              = "Dashboard"
                        st.rerun()
                with fc2:
                    if st.button("📋 Floor", key=f"copy_floor_{floor_num}",
                                 help="Duplicate floor", use_container_width=True):
                        nf = next_floor_num(floors)
                        floors[nf] = copy.deepcopy(floors[floor_num])
                        st.session_state.building["num_floors"] = len(floors)
                        st.session_state.current_floor     = nf
                        first_apt  = sorted(floors[nf]["apartments"].keys())[0]
                        first_room = sorted(floors[nf]["apartments"][first_apt]["rooms"].keys())[0]
                        st.session_state.current_apartment = first_apt
                        st.session_state.current_room      = first_room
                        st.session_state.page              = "Dashboard"
                        st.rerun()
                with fc3:
                    if len(floor_nums) > 1:
                        if st.button("🗑️ Del", key=f"del_floor_{floor_num}",
                                     help="Delete floor", use_container_width=True):
                            del floors[floor_num]
                            st.session_state.building["num_floors"] = len(floors)
                            rem       = sorted(floors.keys())
                            first_apt = sorted(floors[rem[0]]["apartments"].keys())[0]
                            first_rm  = sorted(floors[rem[0]]["apartments"][first_apt]["rooms"].keys())[0]
                            st.session_state.current_floor     = rem[0]
                            st.session_state.current_apartment = first_apt
                            st.session_state.current_room      = first_rm
                            st.rerun()

                # ── Apartments inside this floor ─
                for apt_key in sorted(apts.keys()):
                    apt_obj        = apts[apt_key]
                    rooms          = apt_obj["rooms"]
                    is_active_apt  = (floor_num == cf and apt_key == ca)

                    # Apartment row: label + per-apt actions
                    apt_label = f"{'▶ ' if is_active_apt else '    '}🚪 {apt_key}: {apt_obj['name']}"

                    # Apartment expander (nested via columns + expander trick)
                    apt_exp_label = (
                        f"{'▶ ' if is_active_apt else ''}🚪 {apt_key}: {apt_obj['name']}  "
                        f"({len(rooms)} rm{'s' if len(rooms)!=1 else ''})"
                    )
                    with st.expander(apt_exp_label, expanded=is_active_apt):

                        # Per-apt actions
                        ac1, ac2, ac3 = st.columns(3)
                        with ac1:
                            if st.button("➕ Rm", key=f"add_room_{floor_num}_{apt_key}",
                                         help="Add room", use_container_width=True):
                                nrk = next_room_key(list(rooms.keys()))
                                rooms[nrk] = default_room(f"Room {nrk}")
                                st.session_state.current_floor     = floor_num
                                st.session_state.current_apartment = apt_key
                                st.session_state.current_room      = nrk
                                st.session_state.page              = "Dashboard"
                                st.rerun()
                        with ac2:
                            if st.button("📋 Apt", key=f"copy_apt_{floor_num}_{apt_key}",
                                         help="Duplicate apartment to new slot on same floor",
                                         use_container_width=True):
                                nk = next_apt_key(list(apts.keys()))
                                apts[nk] = copy.deepcopy(apt_obj)
                                apts[nk]["name"] = f"Apartment {nk}"
                                st.session_state.current_floor     = floor_num
                                st.session_state.current_apartment = nk
                                st.session_state.current_room      = sorted(apts[nk]["rooms"].keys())[0]
                                st.session_state.page              = "Dashboard"
                                st.rerun()
                        with ac3:
                            if len(apts) > 1:
                                if st.button("🗑️ Apt", key=f"del_apt_{floor_num}_{apt_key}",
                                             help="Delete apartment", use_container_width=True):
                                    del apts[apt_key]
                                    rem_apt  = sorted(apts.keys())[0]
                                    rem_rm   = sorted(apts[rem_apt]["rooms"].keys())[0]
                                    st.session_state.current_floor     = floor_num
                                    st.session_state.current_apartment = rem_apt
                                    st.session_state.current_room      = rem_rm
                                    st.rerun()

                        # ── Room buttons ─────────
                        for room_key in sorted(rooms.keys(), key=lambda x: int(x) if x.isdigit() else ord(x[0])):
                            room_obj    = rooms[room_key]
                            is_sel_room = (floor_num == cf and apt_key == ca and room_key == cr)
                            prefix      = "▶ " if is_sel_room else "     "
                            btn_type    = "primary" if is_sel_room else "secondary"

                            if st.button(
                                f"{prefix}🏠 {room_key}: {room_obj['name']}",
                                key=f"room_btn_{floor_num}_{apt_key}_{room_key}",
                                use_container_width=True,
                                type=btn_type,
                            ):
                                st.session_state.current_floor     = floor_num
                                st.session_state.current_apartment = apt_key
                                st.session_state.current_room      = room_key
                                st.session_state.page              = "Dashboard"
                                st.rerun()

        st.divider()

        # ── Add floor ────────────────────────
        if st.button("➕ Add Floor", use_container_width=True, key="add_floor_btn"):
            nf = next_floor_num(floors)
            floors[nf] = {"apartments": {"A": default_apartment("Apartment A")}}
            st.session_state.building["num_floors"] = len(floors)
            st.session_state.current_floor     = nf
            st.session_state.current_apartment = "A"
            st.session_state.current_room      = "1"
            st.session_state.page              = "Dashboard"
            st.rerun()

        st.divider()

        # ── Utilities ────────────────────────
        if st.button("🏗️ Load Example Building", use_container_width=True):
            load_example_building()
            st.session_state.page = "Dashboard"
            st.rerun()
        if st.button("🔄 New / Reset Project", use_container_width=True):
            for k in ["building","floors","templates","current_floor","current_apartment","current_room"]:
                st.session_state.pop(k, None)
            st.rerun()

# ─────────────────────────────────────────────
# APPLIANCE TABLE  (shared by Dashboard)
# ─────────────────────────────────────────────
def render_appliance_table(room, key_prefix):
    """Renders the dynamic appliance editor for a room."""
    add_c1, add_c2 = st.columns([1, 2])
    with add_c1:
        if st.button("➕ Add Blank", use_container_width=True, key=f"blank_{key_prefix}"):
            room["appliances"].append({"name": "New Appliance", "watts": 100, "hours": 1})
            st.rerun()
    with add_c2:
        preset_names = [t["name"] for t in APPLIANCE_TEMPLATES]
        psel = st.selectbox(
            "Preset", ["— select preset —"] + preset_names,
            key=f"psel_{key_prefix}", label_visibility="collapsed",
        )
        if psel != "— select preset —":
            if st.button("➕ Add Preset", use_container_width=True, key=f"addp_{key_prefix}"):
                m = next((t for t in APPLIANCE_TEMPLATES if t["name"] == psel), None)
                if m:
                    room["appliances"].append({"name": m["name"], "watts": m["watts"], "hours": m["hours"]})
                    st.rerun()

    appliances = room["appliances"]
    if appliances:
        hcols = st.columns([0.35, 2.1, 1.15, 1.15, 0.75])
        for col, lbl in zip(hcols, ["**#**","**Name**","**Watts**","**Hrs/day**","**Del**"]):
            col.markdown(lbl)
        to_del = None
        for i, appl in enumerate(appliances):
            row = st.columns([0.35, 2.1, 1.15, 1.15, 0.75])
            row[0].write(i + 1)
            appl["name"]  = row[1].text_input("", value=appl["name"],  key=f"an_{key_prefix}_{i}", label_visibility="collapsed")
            appl["watts"] = row[2].number_input("", 0, 5000, int(appl["watts"]),  key=f"aw_{key_prefix}_{i}", label_visibility="collapsed")
            appl["hours"] = row[3].number_input("", 0.0, 24.0, float(appl["hours"]), step=0.5, key=f"ah_{key_prefix}_{i}", label_visibility="collapsed")
            if row[4].button("🗑️", key=f"ad_{key_prefix}_{i}"):
                to_del = i
        if to_del is not None:
            room["appliances"].pop(to_del)
            st.rerun()
    else:
        st.info("No appliances yet. Add one above.")

# ─────────────────────────────────────────────
# COST METRICS BLOCK  (reusable)
# ─────────────────────────────────────────────
def render_cost_metrics(label, kwh):
    """Renders kWh/yr, cost/yr, cost/hr in a 3-column metric row."""
    usd_yr, dzd_yr = cost_per_year(kwh)
    usd_hr, dzd_hr = cost_per_hour(kwh)

    m1, m2, m3 = st.columns(3)
    m1.metric(f"⚡ {label} — kWh/yr",    f"{kwh:,.1f}")
    m2.metric("💵 Cost / Year",
              f"${usd_yr:,.2f} USD",
              f"{dzd_yr:,.0f} DZD")
    m3.metric("⏱️ Avg Cost / Hour",
              f"${usd_hr:,.5f} USD",
              f"{dzd_hr:,.4f} DZD")

# ─────────────────────────────────────────────
# PAGE: DASHBOARD
# ─────────────────────────────────────────────
def page_dashboard():
    floors = st.session_state.floors
    cf = st.session_state.current_floor
    ca = st.session_state.current_apartment
    cr = st.session_state.current_room

    # Safety guards
    if cf not in floors:
        cf = sorted(floors.keys())[0]; st.session_state.current_floor = cf
    if ca not in floors[cf]["apartments"]:
        ca = sorted(floors[cf]["apartments"].keys())[0]; st.session_state.current_apartment = ca
    if cr not in floors[cf]["apartments"][ca]["rooms"]:
        cr = sorted(floors[cf]["apartments"][ca]["rooms"].keys())[0]; st.session_state.current_room = cr

    apt  = floors[cf]["apartments"][ca]
    room = apt["rooms"][cr]

    # ── Header ──────────────────────────────
    st.title("🏢 Building Energy Forecaster")
    st.markdown(
        f"**Editing:** Floor {cf}  ›  Apartment {ca} (*{apt['name']}*)  ›  "
        f"Room {cr} (*{room['name']}*)  |  "
        f"Climate: **{st.session_state.building['climate_zone']}**  |  "
        f"Location: **{st.session_state.building['location_type']}**"
    )
    st.divider()

    # ── Template toolbar ─────────────────────
    tb1, tb2, tb3, tb4 = st.columns([2, 2, 2, 2])
    with tb1:
        tpl_name_input = st.text_input(
            "Template name", value=f"F{cf}_Apt{ca}_Rm{cr}",
            placeholder="Template name…", label_visibility="collapsed",
            key="tpl_name_inp",
        )
    with tb2:
        if st.button("💾 Save Room as Template", use_container_width=True):
            if tpl_name_input.strip():
                st.session_state.templates[tpl_name_input] = copy.deepcopy(room)
                st.success(f"Saved: **{tpl_name_input}**")
            else:
                st.error("Enter a template name first.")
    with tb3:
        if st.session_state.templates:
            tpl_sel = st.selectbox(
                "Load", ["— select —"] + list(st.session_state.templates.keys()),
                key="tpl_sel", label_visibility="collapsed",
            )
        else:
            tpl_sel = "— select —"
            st.caption("No templates yet.")
    with tb4:
        if tpl_sel != "— select —":
            if st.button("⬇️ Apply Template", use_container_width=True):
                room.update(copy.deepcopy(st.session_state.templates[tpl_sel]))
                st.success(f"Applied: **{tpl_sel}**")
                st.rerun()

    st.divider()

    # ── Room name ────────────────────────────
    room["name"] = st.text_input("Room name", value=room["name"], key=f"rname_{cf}_{ca}_{cr}")

    # ── Two-column layout ────────────────────
    left_col, right_col = st.columns([1, 1], gap="large")

    # ════════════════════════════════════════
    # LEFT — Room physical inputs
    # ════════════════════════════════════════
    with left_col:
        st.subheader("🧱 Room Shell")
        c1, c2 = st.columns(2)
        with c1:
            room["area_m2"] = st.number_input(
                "Floor area (m²)", 5.0, 100.0, float(room["area_m2"]),
                step=0.5, key=f"area_{cf}_{ca}_{cr}"
            )
            room["wall_thickness_cm"] = st.slider(
                "Wall thickness (cm)", 10, 50, int(room["wall_thickness_cm"]),
                key=f"wt_{cf}_{ca}_{cr}"
            )
            room["wall_material"] = st.selectbox(
                "Wall material", list(WALL_U_VALUES.keys()),
                index=list(WALL_U_VALUES.keys()).index(room.get("wall_material","Brick")),
                key=f"wm_{cf}_{ca}_{cr}",
            )
            st.caption(f"U-value: {WALL_U_VALUES[room['wall_material']]} W/m²K")

        with c2:
            room["ceiling_height_m"] = st.number_input(
                "Ceiling height (m)", 2.0, 4.0, float(room["ceiling_height_m"]),
                step=0.1, key=f"ch_{cf}_{ca}_{cr}"
            )
            room["window_count"] = st.number_input(
                "Window count", 0, 20, int(room["window_count"]),
                key=f"wc_{cf}_{ca}_{cr}"
            )
            room["window_area_per_window_m2"] = st.number_input(
                "Window area each (m²)", 0.5, 5.0,
                float(room["window_area_per_window_m2"]),
                step=0.1, key=f"wa_{cf}_{ca}_{cr}"
            )
            room["window_type"] = st.selectbox(
                "Window type", ["Single","Double"],
                index=["Single","Double"].index(room.get("window_type","Single")),
                key=f"wtype_{cf}_{ca}_{cr}",
            )
            st.caption(f"Window U-value: {WINDOW_U_VALUES[room['window_type']]} W/m²K")
            room["plug_count"] = st.number_input(
                "Plug/outlet count", 0, 30, int(room["plug_count"]),
                key=f"pc_{cf}_{ca}_{cr}"
            )

        # ── Copy room ────────────────────────
        st.subheader("📋 Copy Room To…")
        cp1, cp2, cp3, cp4 = st.columns([2, 2, 2, 1])
        with cp1:
            tgt_floor = st.selectbox("Floor", sorted(floors.keys()), key=f"cpf_{cf}_{ca}_{cr}")
        with cp2:
            tgt_apt_opts = sorted(floors[tgt_floor]["apartments"].keys())
            tgt_apt = st.selectbox("Apt", tgt_apt_opts, key=f"cpa_{cf}_{ca}_{cr}")
        with cp3:
            tgt_room_opts = sorted(floors[tgt_floor]["apartments"][tgt_apt]["rooms"].keys()) + ["NEW"]
            tgt_room = st.selectbox("Room", tgt_room_opts, key=f"cpr_{cf}_{ca}_{cr}")
        with cp4:
            st.write(""); st.write("")
            if st.button("📤", key=f"do_copy_{cf}_{ca}_{cr}", use_container_width=True, help="Copy room"):
                dest_rooms = floors[tgt_floor]["apartments"][tgt_apt]["rooms"]
                if tgt_room == "NEW":
                    nrk = next_room_key(list(dest_rooms.keys()))
                    dest_rooms[nrk] = copy.deepcopy(room)
                    st.success(f"Copied → Floor {tgt_floor} / Apt {tgt_apt} / Room {nrk}")
                else:
                    dest_rooms[tgt_room] = copy.deepcopy(room)
                    st.success(f"Copied → Floor {tgt_floor} / Apt {tgt_apt} / Room {tgt_room}")
                st.rerun()

        # ── Delete room ──────────────────────
        if len(apt["rooms"]) > 1:
            st.divider()
            if st.button(f"🗑️ Delete Room {cr}: {room['name']}", use_container_width=True,
                         key=f"del_room_{cf}_{ca}_{cr}"):
                del apt["rooms"][cr]
                st.session_state.current_room = sorted(apt["rooms"].keys())[0]
                st.rerun()

    # ════════════════════════════════════════
    # RIGHT — Appliances + Quick Results
    # ════════════════════════════════════════
    with right_col:
        st.subheader("⚡ Appliances")
        render_appliance_table(room, f"{cf}_{ca}_{cr}")

        st.divider()
        st.subheader("📊 Quick Results")

        # Room results
        res_room = calculate_room_electricity(
            room,
            st.session_state.building["climate_zone"],
            st.session_state.building["location_type"],
        )
        st.markdown("**🏠 This Room**")
        render_cost_metrics("Room", res_room["total"])

        cooling = res_room["wall_load"] + res_room["window_load"]
        rm1, rm2, rm3, rm4 = st.columns(4)
        rm1.metric("🌡️ Cooling",    f"{cooling:,.0f} kWh")
        rm2.metric("⚡ Appliances", f"{res_room['appliance_total']:,.0f} kWh")
        rm3.metric("🔌 Plug Load",  f"{res_room['plug_load']:,.0f} kWh")
        rm4.metric("📐 Area",       f"{res_room['area_m2']:.0f} m²")

        st.divider()

        # Apartment results (sum of all rooms)
        res_apt = calculate_apartment_total(
            apt,
            st.session_state.building["climate_zone"],
            st.session_state.building["location_type"],
        )
        st.markdown(f"**🚪 Apartment {ca} Total ({len(apt['rooms'])} rooms)**")
        render_cost_metrics("Apartment", res_apt["total"])

# ─────────────────────────────────────────────
# PAGE: BUILDING SETTINGS
# ─────────────────────────────────────────────
def page_building_settings():
    st.title("⚙️ Building Settings")
    b = st.session_state.building

    c1, c2 = st.columns(2)
    with c1:
        b["name"] = st.text_input("Building name", value=b["name"])
        b["climate_zone"] = st.selectbox(
            "Climate zone", list(CLIMATE_ZONES.keys()),
            index=list(CLIMATE_ZONES.keys()).index(b.get("climate_zone","Coast")),
        )
        zi = CLIMATE_ZONES[b["climate_zone"]]
        st.caption(f"ℹ️ {zi['desc']}  |  Cooling ×{zi['cooling']}  |  Heating ×{zi['heating']}")
        b["location_type"] = st.selectbox(
            "Location type", list(LOCATION_MULTIPLIERS.keys()),
            index=list(LOCATION_MULTIPLIERS.keys()).index(b.get("location_type","Urban")),
        )
        st.caption(f"ℹ️ Location multiplier: ×{LOCATION_MULTIPLIERS[b['location_type']]}")

    with c2:
        st.subheader("💵 Cost Settings")
        b["electricity_price_usd"] = st.number_input(
            "Electricity price (USD/kWh)", 0.01, 1.0,
            float(b.get("electricity_price_usd", 0.12)),
            step=0.001, format="%.3f",
        )
        b["exchange_rate"] = st.number_input(
            "Exchange rate (1 USD = ? DZD)", 1.0, 10000.0,
            float(b.get("exchange_rate", 135.0)), step=1.0,
        )
        dzd_eq = b["electricity_price_usd"] * b["exchange_rate"]
        st.caption(f"≈ {dzd_eq:.2f} DZD / kWh")
        st.session_state.show_cost = st.checkbox(
            "Show cost estimates in results", value=st.session_state.show_cost
        )

    st.divider()

    # Copy floor
    st.subheader("📋 Copy Entire Floor")
    fnums = sorted(st.session_state.floors.keys())
    if len(fnums) >= 2:
        cf1, cf2, cf3 = st.columns(3)
        with cf1: src = st.selectbox("Source floor", fnums, key="src_fl")
        with cf2: tgt = st.selectbox("Target floor", [f for f in fnums if f != src], key="tgt_fl")
        with cf3:
            st.write(""); st.write("")
            if st.button(f"📤 Copy {src} → {tgt}", use_container_width=True):
                st.session_state.floors[tgt] = copy.deepcopy(st.session_state.floors[src])
                st.success(f"Floor {src} copied to Floor {tgt}!")
                st.rerun()
    else:
        st.info("Need at least 2 floors to copy.")

    st.divider()

    # Overview
    st.subheader("🏗️ Building Overview")
    for fn in fnums:
        apts = st.session_state.floors[fn]["apartments"]
        for ak, av in sorted(apts.items()):
            rooms     = av["rooms"]
            room_list = ", ".join([f"{rk}:{rv['name']}" for rk,rv in sorted(rooms.items())])
            st.write(f"  Floor **{fn}** › Apt **{ak}** ({av['name']}) — {len(rooms)} room(s): {room_list}")

    with st.expander("📖 U-Value Reference"):
        st.markdown("**Wall Materials**")
        wdf = pd.DataFrame([
            {"Material": k, "U-value (W/m²K)": v,
             "Rating": "Excellent" if v<0.5 else "Good" if v<1.0 else "Average" if v<1.7 else "Poor"}
            for k, v in WALL_U_VALUES.items()
        ])
        st.dataframe(wdf, use_container_width=True, hide_index=True)
        st.markdown("**Window Types**")
        st.dataframe(
            pd.DataFrame([{"Type":k,"U-value (W/m²K)":v} for k,v in WINDOW_U_VALUES.items()]),
            use_container_width=True, hide_index=True,
        )

# ─────────────────────────────────────────────
# PAGE: APARTMENT TEMPLATES
# ─────────────────────────────────────────────
def page_templates():
    st.title("📁 Room Templates")

    if not st.session_state.templates:
        st.info("No templates saved yet. Go to the Dashboard and click 'Save Room as Template'.")
        return

    for tpl_name, tpl_data in list(st.session_state.templates.items()):
        with st.expander(f"📄 {tpl_name}"):
            tc1, tc2 = st.columns([3, 1])
            with tc1:
                st.write(
                    f"**Area:** {tpl_data.get('area_m2','—')} m²  |  "
                    f"**Wall:** {tpl_data.get('wall_material','—')}  |  "
                    f"**Windows:** {tpl_data.get('window_count','—')} × {tpl_data.get('window_type','—')}"
                )
                appls = tpl_data.get("appliances", [])
                if appls:
                    st.write(f"**Appliances ({len(appls)}):** " + ", ".join([a["name"] for a in appls]))
            with tc2:
                nn = st.text_input("Rename", value=tpl_name, key=f"ren_{tpl_name}")
                if nn != tpl_name and st.button("💾", key=f"savren_{tpl_name}"):
                    st.session_state.templates[nn] = st.session_state.templates.pop(tpl_name)
                    st.rerun()
                if st.button("🗑️ Delete", key=f"dtpl_{tpl_name}"):
                    del st.session_state.templates[tpl_name]
                    st.rerun()

    st.divider()
    st.subheader("📤 Export / Import")
    ec1, ec2 = st.columns(2)
    with ec1:
        st.download_button(
            "⬇️ Export Templates (JSON)",
            data=json.dumps(st.session_state.templates, indent=2),
            file_name="templates.json", mime="application/json",
        )
    with ec2:
        upl = st.file_uploader("⬆️ Import Templates (JSON)", type=["json"], key="tpl_imp")
        if upl:
            try:
                imp = json.load(upl)
                st.session_state.templates.update(imp)
                st.success(f"Imported {len(imp)} template(s).")
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")

# ─────────────────────────────────────────────
# PAGE: RESULTS & FORECAST
# ─────────────────────────────────────────────
def page_results():
    st.title("📈 Results & Forecast")
    b       = st.session_state.building
    results = calculate_building_total(
        st.session_state.floors, b["climate_zone"], b["location_type"]
    )

    bldg_kwh = results["building_total"]
    eui      = results["eui"]
    bench    = get_eui_benchmark(eui)

    # ── Building KPIs ────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🏢 Total kWh/yr",   f"{bldg_kwh:,.0f}")
    k2.metric("📐 EUI",            f"{eui:,.1f} kWh/m²/yr")
    k3.metric("📏 Total Area",     f"{results['total_area']:,.0f} m²")
    k4.metric("🌍 Climate Zone",   b["climate_zone"])
    st.info(f"🔍 EUI Benchmark: **{bench}**")

    # Building cost (year + hour)
    usd_yr, dzd_yr = cost_per_year(bldg_kwh)
    usd_hr, dzd_hr = cost_per_hour(bldg_kwh)
    bc1, bc2, bc3 = st.columns(3)
    bc1.metric("💵 Building Cost / Year",  f"${usd_yr:,.2f} USD", f"{dzd_yr:,.0f} DZD")
    bc2.metric("⏱️ Building Avg Cost / Hr", f"${usd_hr:,.5f} USD", f"{dzd_hr:,.4f} DZD")
    bc3.metric("🔢 Total kWh / Year",      f"{bldg_kwh:,.1f} kWh")

    st.divider()

    # ── Floor bar chart ───────────────────────
    st.subheader("📊 Energy by Floor")
    fdf = pd.DataFrame([
        {"Floor": f"Floor {k}", "kWh/year": round(v, 1)}
        for k, v in sorted(results["floor_totals"].items())
    ])
    st.bar_chart(fdf.set_index("Floor"))

    # ── Apartment breakdown table ─────────────
    st.subheader("🚪 Energy by Apartment")
    apt_rows = []
    for (fn, ak), ar in sorted(results["apt_results"].items()):
        apt_obj = st.session_state.floors[fn]["apartments"][ak]
        n_rooms = len(apt_obj["rooms"])
        usd_y, dzd_y = cost_per_year(ar["total"])
        usd_h, dzd_h = cost_per_hour(ar["total"])
        row = {
            "Floor":             fn,
            "Apt":               ak,
            "Name":              apt_obj["name"],
            "Rooms":             n_rooms,
            "Area (m²)":         round(ar["total_area"], 1),
            "kWh/yr":            round(ar["total"], 1),
            "EUI (kWh/m²)":      round(ar["total"] / ar["total_area"], 1) if ar["total_area"] > 0 else 0,
            "Cost/yr (USD)":     round(usd_y, 2),
            "Cost/yr (DZD)":     round(dzd_y, 0),
            "Avg Cost/hr (USD)": round(usd_h, 5),
            "Avg Cost/hr (DZD)": round(dzd_h, 4),
        }
        apt_rows.append(row)
    st.dataframe(pd.DataFrame(apt_rows), use_container_width=True, hide_index=True)

    # ── Room breakdown table ──────────────────
    st.subheader("🏠 Energy by Room")
    room_rows = []
    for (fn, ak), ar in sorted(results["apt_results"].items()):
        apt_obj = st.session_state.floors[fn]["apartments"][ak]
        for rk, rr in sorted(ar["room_results"].items()):
            room_name = apt_obj["rooms"][rk]["name"]
            usd_y, dzd_y = cost_per_year(rr["total"])
            usd_h, dzd_h = cost_per_hour(rr["total"])
            room_rows.append({
                "Floor":             fn,
                "Apt":               ak,
                "Room":              rk,
                "Room Name":         room_name,
                "Area (m²)":         round(rr["area_m2"], 1),
                "Cooling (kWh)":     round(rr["wall_load"]+rr["window_load"], 1),
                "Appliances (kWh)":  round(rr["appliance_total"], 1),
                "Plug Load (kWh)":   round(rr["plug_load"], 1),
                "Total (kWh/yr)":    round(rr["total"], 1),
                "Cost/yr (USD)":     round(usd_y, 2),
                "Cost/yr (DZD)":     round(dzd_y, 0),
                "Avg Cost/hr (USD)": round(usd_h, 5),
                "Avg Cost/hr (DZD)": round(dzd_h, 4),
            })
    st.dataframe(pd.DataFrame(room_rows), use_container_width=True, hide_index=True)

    # ── Top 5 appliances ─────────────────────
    st.subheader("🔌 Top 5 Energy-Consuming Appliances (Building-wide)")
    all_appls = []
    for (fn, ak), ar in results["apt_results"].items():
        for rk, rr in ar["room_results"].items():
            for appl in rr["appliance_breakdown"]:
                all_appls.append({
                    "Appliance": appl["name"],
                    "kWh/year":  round(appl["kwh"], 1),
                    "Watts":     appl["watts"],
                    "Hours/day": appl["hours"],
                    "Floor":     fn, "Apt": ak, "Room": rk,
                })
    if all_appls:
        adf = pd.DataFrame(all_appls).sort_values("kWh/year", ascending=False).head(5)
        st.dataframe(adf, use_container_width=True, hide_index=True)
        st.bar_chart(adf.set_index("Appliance")["kWh/year"])
    else:
        st.info("No appliances added to any room yet.")

    st.divider()
    st.subheader("📋 EUI Benchmark Reference")
    st.dataframe(
        pd.DataFrame([
            {"Max EUI (kWh/m²/yr)": t if t < 999 else "999+", "Rating": l}
            for t, l in EUI_BENCHMARKS
        ]),
        use_container_width=True, hide_index=True,
    )

# ─────────────────────────────────────────────
# PAGE: SAVE / LOAD
# ─────────────────────────────────────────────
def page_save_load():
    st.title("💾 Save / Load Project")

    st.subheader("💾 Save")
    pname = st.text_input("Project name", value=st.session_state.building["name"])
    if st.button("💾 Save to Database"):
        if pname.strip():
            save_project_to_db(pname, st.session_state.building, st.session_state.floors)
            st.success(f"Saved: '{pname}'")
        else:
            st.error("Enter a project name.")

    st.divider()
    st.subheader("📂 Load")
    projects = load_all_projects()
    if projects:
        opts = {f"{r[1]}  (saved: {r[2]})": r[0] for r in projects}
        sel  = st.selectbox("Select project", list(opts.keys()))
        lc1, lc2 = st.columns(2)
        with lc1:
            if st.button("📂 Load Project"):
                if load_project_from_db(opts[sel]):
                    st.success("Loaded!")
                    st.rerun()
                else:
                    st.error("Failed to load.")
        with lc2:
            if st.button("🗑️ Delete Project"):
                delete_project_from_db(opts[sel])
                st.success("Deleted.")
                st.rerun()
    else:
        st.info("No saved projects.")

    st.divider()
    st.subheader("📤 Export / Import (JSON)")
    ec1, ec2 = st.columns(2)
    with ec1:
        st.download_button(
            "⬇️ Export Project (JSON)", data=export_to_json(),
            file_name=f"{st.session_state.building['name'].replace(' ','_')}.json",
            mime="application/json",
        )
    with ec2:
        upl = st.file_uploader("⬆️ Import Project (JSON)", type=["json"], key="proj_imp")
        if upl:
            ok, msg = import_from_json(upl.read().decode("utf-8"))
            if ok: st.success(msg); st.rerun()
            else:  st.error(msg)

# ─────────────────────────────────────────────
# PAGE: HELP
# ─────────────────────────────────────────────
def page_help():
    st.title("❓ Help & Instructions")
    st.markdown("""
## Full Hierarchy
```
🏢 Building
└── 📐 Floor 1
    └── 🚪 Apartment A
        ├── ▶ 🏠 Room 1: Living Room   ← selected
        └──   🏠 Room 2: Bedroom
```
Each **Room** has its own wall, window, plug, ceiling, and appliance inputs.
An **Apartment** total = sum of all its rooms.
A **Floor** total = sum of all its apartments.
The **Building** total = sum of all floors.

---

## Sidebar Tree Controls

| Button | Action |
|--------|--------|
| ➕ Apt | Add apartment to that floor |
| 📋 Floor | Duplicate entire floor |
| 🗑️ Del (floor) | Delete floor |
| ➕ Rm | Add room to that apartment |
| 📋 Apt | Duplicate apartment on same floor |
| 🗑️ Apt | Delete apartment |
| 🏠 Room button | Click to load room into Dashboard |
| ➕ Add Floor | Add new empty floor at the bottom |

---

## Cost Display
Every result shows **3 values**:
- **kWh/year** — total energy
- **Cost/year** — `kWh × price` in both USD and DZD
- **Avg Cost/hour** — `Cost/year ÷ 8760` in both USD and DZD

Adjust price and exchange rate in **Building Settings**.

---

## Calculation Summary
- **Wall cooling load** = Wall area × U-value × 15°C × 8h × 120 days / 1000
- **Window cooling load** = Window area × U-value × 15°C × 8h × 120 days / 1000
- **Appliance load** = Watts × hours/day × days/year / 1000
- **Plug load** = plug count × 50W × 8h × 365 / 1000
- **Total** = (all loads) × climate multiplier × location multiplier

## Climate Zones
| Zone | Cooling × | Heating × |
|------|-----------|-----------|
| Coast | 1.1 | 0.8 |
| Desert | 1.4 | 0.6 |
| Mountains | 0.6 | 1.5 |
| City | 1.0 | 1.0 |

## EUI Benchmarks
| EUI (kWh/m²/yr) | Rating |
|-----------------|--------|
| < 50 | Excellent |
| 50–100 | Good |
| 100–150 | Average |
| 150–200 | Below average |
| 200–300 | Poor |
| > 300 | Very poor |

---

## Tips
- **Load Example Building** → instantly see a working 3-floor / 2-apt / 2-room example
- **Save Room as Template** → reuse room configs across any apartment
- **Export JSON** → back up or share your project file
- **Copy Room** → copy a room's config to any floor / apartment / room slot
""")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    init_db()
    init_session_state()
    render_sidebar()

    pg = st.session_state.page
    if   pg == "Dashboard":           page_dashboard()
    elif pg == "Building Settings":   page_building_settings()
    elif pg == "Apartment Templates": page_templates()
    elif pg == "Results & Forecast":  page_results()
    elif pg == "Save/Load Project":   page_save_load()
    elif pg == "Help":                page_help()

if __name__ == "__main__":
    main()
