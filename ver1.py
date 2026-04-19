"""
Building Energy Forecaster — Electricity + Gas
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
    "Coast":     {"elec_cooling": 1.1, "elec_heating": 0.8, "gas_heating": 0.8,  "desc": "Humid, moderate temperatures"},
    "Desert":    {"elec_cooling": 1.4, "elec_heating": 0.6, "gas_heating": 0.6,  "desc": "Hot days, cool nights, high solar"},
    "Mountains": {"elec_cooling": 0.6, "elec_heating": 1.5, "gas_heating": 1.5,  "desc": "Cold winters, mild summers"},
    "City":      {"elec_cooling": 1.0, "elec_heating": 1.0, "gas_heating": 1.0,  "desc": "Urban heat island, baseline"},
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

ROOF_U_VALUE = 0.75   # W/m²K — standard uninsulated flat roof

ELEC_APPLIANCE_TEMPLATES = [
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

GAS_APPLIANCE_TEMPLATES = [
    {"name": "Gas Heater",       "kwh_per_hour": 2.5,  "hours": 4},
    {"name": "Gas Water Heater", "kwh_per_hour": 3.0,  "hours": 1},
    {"name": "Gas Oven",         "kwh_per_hour": 2.0,  "hours": 1},
    {"name": "Gas Stove",        "kwh_per_hour": 1.5,  "hours": 1},
    {"name": "Gas Dryer",        "kwh_per_hour": 2.5,  "hours": 1},
    {"name": "Gas Fireplace",    "kwh_per_hour": 5.0,  "hours": 3},
]

EUI_BENCHMARKS = [
    (50,  "Excellent — Passive/Net-Zero standard"),
    (100, "Good — Energy-efficient building"),
    (150, "Average — Standard construction"),
    (200, "Below average — Needs improvement"),
    (300, "Poor — Major upgrade recommended"),
    (999, "Very poor — Urgent action required"),
]

DB_PATH       = "building_energy.db"
HOURS_IN_YEAR = 8760
DEGREE_DAYS   = 1136   # standard heating degree-days
HEATER_EFF    = 0.65   # gas heater efficiency

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
        (project_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         json.dumps(building_data), json.dumps(floors_data)),
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
        "appliances":                [],      # electric appliances
    }

def default_apartment(name="Apartment A"):
    return {
        "name":       name,
        "occupants":  4,                      # for gas hot water calculation
        "cooking_kwh": 1825.0,                # default cooking gas kWh/year
        "gas_appliances": [],                 # gas appliances list
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
    for i in range(1, 200):
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
            "gas_price_usd":         0.02,
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

# ─────────────────────────────────────────────
# ELECTRICITY CALCULATIONS
# ─────────────────────────────────────────────
def calculate_room_electricity(room, climate_zone, location_type):
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

    wall_load   = wall_area        * wall_u   * delta_t * cooling_hrs * cooling_days / 1000
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

    elec_mult = CLIMATE_ZONES.get(climate_zone, {}).get("elec_cooling", 1.0)
    loc_mult  = LOCATION_MULTIPLIERS.get(location_type, 1.0)
    mult      = elec_mult * loc_mult

    total = (wall_load + window_load + appliance_total + plug_load) * mult

    return {
        "wall_load":           wall_load   * mult,
        "window_load":         window_load * mult,
        "appliance_total":     appliance_total * mult,
        "plug_load":           plug_load   * mult,
        "total":               total,
        "appliance_breakdown": appliance_breakdown,
        "area_m2":             area,
        "ceiling_h":           ceiling_h,
        # raw (pre-multiplier) losses for gas heating reuse
        "raw_wall_loss":   wall_load,
        "raw_window_loss": window_load,
    }

def calculate_apartment_electricity(apt, climate_zone, location_type):
    total = 0.0; total_area = 0.0; room_results = {}
    for rk, rd in apt.get("rooms", {}).items():
        res = calculate_room_electricity(rd, climate_zone, location_type)
        total += res["total"]; total_area += res["area_m2"]
        room_results[rk] = res
    return {"total": total, "total_area": total_area, "room_results": room_results}

# ─────────────────────────────────────────────
# GAS CALCULATIONS
# ─────────────────────────────────────────────
def calculate_room_gas_heating(room, climate_zone):
    """
    Gas heating for one room.
    Formula: (wall_loss + window_loss + roof_loss) × degree_days × volume × climate_factor / heater_eff
    We normalise the envelope losses to a per-degree-day basis then scale.
    """
    area      = room.get("area_m2", 20.0)
    ceiling_h = room.get("ceiling_height_m", 2.5)
    volume    = area * ceiling_h  # m³

    wall_u           = WALL_U_VALUES.get(room.get("wall_material", "Brick"), 1.84)
    window_count     = room.get("window_count", 2)
    window_area_each = room.get("window_area_per_window_m2", 1.5)
    window_u         = WINDOW_U_VALUES.get(room.get("window_type", "Single"), 5.82)
    window_area_total = window_count * window_area_each

    side       = math.sqrt(max(area, 1))
    gross_wall = 4 * side * ceiling_h
    wall_area  = max(gross_wall - window_area_total, 0)
    roof_area  = area  # floor area = roof area (simpler model)

    # U-value × area for each element (W/K)
    wall_ua   = wall_area        * wall_u
    window_ua = window_area_total * window_u
    roof_ua   = roof_area        * ROOF_U_VALUE
    total_ua  = wall_ua + window_ua + roof_ua   # W/K

    gas_climate = CLIMATE_ZONES.get(climate_zone, {}).get("gas_heating", 1.0)

    # kWh/year = UA (W/K) × degree_days (K·days) × 24h/day / 1000W/kW / heater_eff × climate_factor
    heating_kwh = (total_ua * DEGREE_DAYS * 24 / 1000) * gas_climate / HEATER_EFF

    return heating_kwh

def calculate_apartment_gas(apt, climate_zone):
    """
    Total gas kWh/year for an apartment.
    Components: heating (sum of rooms) + hot water + cooking + gas appliances.
    """
    # 1. Heating — sum across rooms
    heating_total = 0.0
    for room_data in apt.get("rooms", {}).values():
        heating_total += calculate_room_gas_heating(room_data, climate_zone)

    # 2. Hot water
    occupants = apt.get("occupants", 4)
    hot_water_kwh = occupants * 1095.0

    # 3. Cooking
    cooking_kwh = apt.get("cooking_kwh", 1825.0)

    # 4. Gas appliances
    gas_appliance_total = 0.0
    gas_appl_breakdown  = []
    for appl in apt.get("gas_appliances", []):
        n   = appl.get("name", "Unknown")
        kph = appl.get("kwh_per_hour", 0.0)
        h   = appl.get("hours", 0.0)
        kwh = kph * h * 365
        gas_appliance_total += kwh
        gas_appl_breakdown.append({"name": n, "kwh": kwh, "kwh_per_hour": kph, "hours": h})

    total_gas = heating_total + hot_water_kwh + cooking_kwh + gas_appliance_total

    return {
        "heating":            heating_total,
        "hot_water":          hot_water_kwh,
        "cooking":            cooking_kwh,
        "gas_appliances":     gas_appliance_total,
        "total":              total_gas,
        "gas_appl_breakdown": gas_appl_breakdown,
    }

# ─────────────────────────────────────────────
# BUILDING TOTALS
# ─────────────────────────────────────────────
def calculate_building_total(floors_data, climate_zone, location_type):
    building_elec = 0.0; building_gas = 0.0
    total_area    = 0.0
    floor_elec    = {}; floor_gas = {}
    apt_results   = {}

    for fn, fd in floors_data.items():
        fe = 0.0; fg = 0.0
        for ak, ad in fd.get("apartments", {}).items():
            er = calculate_apartment_electricity(ad, climate_zone, location_type)
            gr = calculate_apartment_gas(ad, climate_zone)
            fe += er["total"]; fg += gr["total"]
            building_elec += er["total"]; building_gas += gr["total"]
            total_area    += er["total_area"]
            apt_results[(fn, ak)] = {"elec": er, "gas": gr}
        floor_elec[fn] = fe; floor_gas[fn] = fg

    total_energy = building_elec + building_gas
    eui_elec = building_elec / total_area if total_area > 0 else 0
    eui_total = total_energy  / total_area if total_area > 0 else 0

    return {
        "building_elec":  building_elec,
        "building_gas":   building_gas,
        "total_energy":   total_energy,
        "total_area":     total_area,
        "eui_elec":       eui_elec,
        "eui_total":      eui_total,
        "floor_elec":     floor_elec,
        "floor_gas":      floor_gas,
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
def elec_cost(kwh):
    p = st.session_state.building.get("electricity_price_usd", 0.12)
    r = st.session_state.building.get("exchange_rate", 135.0)
    return kwh * p, kwh * p * r

def gas_cost(kwh):
    p = st.session_state.building.get("gas_price_usd", 0.02)
    r = st.session_state.building.get("exchange_rate", 135.0)
    return kwh * p, kwh * p * r

def fmt_usd_dzd(usd, dzd):
    return f"${usd:,.2f} USD  /  {dzd:,.0f} DZD"

def fmt_usd_dzd_hr(usd, dzd):
    return f"${usd/HOURS_IN_YEAR:,.5f} USD/hr  /  {dzd/HOURS_IN_YEAR:,.4f} DZD/hr"

def render_energy_cost_block(label, kwh, cost_fn, icon="⚡"):
    usd, dzd = cost_fn(kwh)
    c1, c2, c3 = st.columns(3)
    c1.metric(f"{icon} {label} kWh/yr",    f"{kwh:,.1f}")
    c2.metric("💵 Cost / Year",            fmt_usd_dzd(usd, dzd))
    c3.metric("⏱️ Avg Cost / Hour",        fmt_usd_dzd_hr(usd, dzd))

# ─────────────────────────────────────────────
# JSON EXPORT / IMPORT
# ─────────────────────────────────────────────
def export_to_json():
    return json.dumps({
        "building":  st.session_state.building,
        "floors":    {str(k): v for k, v in st.session_state.floors.items()},
        "templates": st.session_state.templates,
    }, indent=2)

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
        "name": "Example Building", "num_floors": 3,
        "climate_zone": "Coast", "location_type": "Urban",
        "electricity_price_usd": 0.12, "gas_price_usd": 0.02,
        "exchange_rate": 135.0,
        "created_date": datetime.now().strftime("%Y-%m-%d"),
    }
    elec_appls = [
        {"name": "Refrigerator", "watts": 150,  "hours": 24},
        {"name": "AC (split)",   "watts": 800,  "hours": 8},
        {"name": "TV (LED)",     "watts": 100,  "hours": 5},
    ]
    gas_appls = [
        {"name": "Gas Stove",        "kwh_per_hour": 1.5, "hours": 1},
        {"name": "Gas Water Heater", "kwh_per_hour": 3.0, "hours": 1},
    ]
    def make_room(name, area, appliances):
        r = default_room(name); r["area_m2"] = area
        r["appliances"] = [dict(a) for a in appliances]; return r
    def make_apt(aname, al, ab):
        return {
            "name": aname, "occupants": 4, "cooking_kwh": 1825.0,
            "gas_appliances": [dict(a) for a in gas_appls],
            "rooms": {
                "1": make_room("Living Room", al, elec_appls),
                "2": make_room("Bedroom",     ab, [{"name":"TV (LED)","watts":100,"hours":5}]),
            },
        }
    st.session_state.floors = {
        1: {"apartments": {"A": make_apt("Apartment 1A",35,20), "B": make_apt("Apartment 1B",30,18)}},
        2: {"apartments": {"A": make_apt("Apartment 2A",35,20), "B": make_apt("Apartment 2B",30,18)}},
        3: {"apartments": {"A": make_apt("Apartment 3A",35,20)}},
    }
    st.session_state.current_floor = 1
    st.session_state.current_apartment = "A"
    st.session_state.current_room = "1"

# ─────────────────────────────────────────────
# SIDEBAR — 4-LEVEL TREE
# ─────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.markdown("## 🏢 Energy Forecaster")
        st.caption("Master Year Project")
        st.divider()

        for icon, pg in [
            ("📈", "Results & Forecast"),
            ("⚙️", "Building Settings"),
            ("📁", "Room Templates"),
            ("💾", "Save/Load Project"),
            ("❓", "Help"),
        ]:
            if st.button(f"{icon} {pg}", use_container_width=True,
                         type="primary" if st.session_state.page == pg else "secondary",
                         key=f"nav_{pg}"):
                st.session_state.page = pg; st.rerun()

        st.divider()
        b = st.session_state.building
        if st.button(f"🏢  {b['name']}", use_container_width=True, key="nav_bld"):
            st.session_state.page = "Building Settings"; st.rerun()
        st.caption(f"Zone: {b['climate_zone']}  |  {b['location_type']}")

        floors     = st.session_state.floors
        floor_nums = sorted(floors.keys())
        cf = st.session_state.current_floor
        ca = st.session_state.current_apartment
        cr = st.session_state.current_room

        for floor_num in floor_nums:
            apts            = floors[floor_num]["apartments"]
            is_active_floor = floor_num == cf

            with st.expander(
                f"📐 Floor {floor_num}  ({len(apts)} apt{'s' if len(apts)!=1 else ''})",
                expanded=is_active_floor,
            ):
                fc1, fc2, fc3 = st.columns(3)
                with fc1:
                    if st.button("➕ Apt", key=f"add_apt_{floor_num}", use_container_width=True):
                        nk = next_apt_key(list(apts.keys()))
                        apts[nk] = default_apartment(f"Apartment {nk}")
                        st.session_state.current_floor     = floor_num
                        st.session_state.current_apartment = nk
                        st.session_state.current_room      = "1"
                        st.session_state.page = "Dashboard"; st.rerun()
                with fc2:
                    if st.button("📋 Floor", key=f"copy_fl_{floor_num}", use_container_width=True):
                        nf = next_floor_num(floors)
                        floors[nf] = copy.deepcopy(floors[floor_num])
                        st.session_state.building["num_floors"] = len(floors)
                        fa = sorted(floors[nf]["apartments"].keys())[0]
                        fr = sorted(floors[nf]["apartments"][fa]["rooms"].keys())[0]
                        st.session_state.current_floor = nf
                        st.session_state.current_apartment = fa
                        st.session_state.current_room = fr
                        st.session_state.page = "Dashboard"; st.rerun()
                with fc3:
                    if len(floor_nums) > 1:
                        if st.button("🗑️ Del", key=f"del_fl_{floor_num}", use_container_width=True):
                            del floors[floor_num]
                            st.session_state.building["num_floors"] = len(floors)
                            rem  = sorted(floors.keys())
                            fa   = sorted(floors[rem[0]]["apartments"].keys())[0]
                            fr   = sorted(floors[rem[0]]["apartments"][fa]["rooms"].keys())[0]
                            st.session_state.current_floor = rem[0]
                            st.session_state.current_apartment = fa
                            st.session_state.current_room = fr; st.rerun()

                for apt_key in sorted(apts.keys()):
                    apt_obj       = apts[apt_key]
                    rooms         = apt_obj["rooms"]
                    is_active_apt = (floor_num == cf and apt_key == ca)

                    with st.expander(
                        f"{'▶ ' if is_active_apt else ''}🚪 {apt_key}: {apt_obj['name']}  ({len(rooms)} rm{'s' if len(rooms)!=1 else ''})",
                        expanded=is_active_apt,
                    ):
                        ac1, ac2, ac3 = st.columns(3)
                        with ac1:
                            if st.button("➕ Rm", key=f"add_rm_{floor_num}_{apt_key}", use_container_width=True):
                                nrk = next_room_key(list(rooms.keys()))
                                rooms[nrk] = default_room(f"Room {nrk}")
                                st.session_state.current_floor = floor_num
                                st.session_state.current_apartment = apt_key
                                st.session_state.current_room = nrk
                                st.session_state.page = "Dashboard"; st.rerun()
                        with ac2:
                            if st.button("📋 Apt", key=f"copy_apt_{floor_num}_{apt_key}", use_container_width=True):
                                nk = next_apt_key(list(apts.keys()))
                                apts[nk] = copy.deepcopy(apt_obj)
                                apts[nk]["name"] = f"Apartment {nk}"
                                st.session_state.current_floor = floor_num
                                st.session_state.current_apartment = nk
                                st.session_state.current_room = sorted(apts[nk]["rooms"].keys())[0]
                                st.session_state.page = "Dashboard"; st.rerun()
                        with ac3:
                            if len(apts) > 1:
                                if st.button("🗑️ Apt", key=f"del_apt_{floor_num}_{apt_key}", use_container_width=True):
                                    del apts[apt_key]
                                    ra  = sorted(apts.keys())[0]
                                    rr  = sorted(apts[ra]["rooms"].keys())[0]
                                    st.session_state.current_floor = floor_num
                                    st.session_state.current_apartment = ra
                                    st.session_state.current_room = rr; st.rerun()

                        for room_key in sorted(rooms.keys(), key=lambda x: int(x) if x.isdigit() else ord(x[0])):
                            is_sel = (floor_num == cf and apt_key == ca and room_key == cr)
                            if st.button(
                                f"{'▶ ' if is_sel else '     '}🏠 {room_key}: {rooms[room_key]['name']}",
                                key=f"rm_btn_{floor_num}_{apt_key}_{room_key}",
                                use_container_width=True,
                                type="primary" if is_sel else "secondary",
                            ):
                                st.session_state.current_floor = floor_num
                                st.session_state.current_apartment = apt_key
                                st.session_state.current_room = room_key
                                st.session_state.page = "Dashboard"; st.rerun()

        st.divider()
        if st.button("➕ Add Floor", use_container_width=True, key="add_floor_btn"):
            nf = next_floor_num(floors)
            floors[nf] = {"apartments": {"A": default_apartment("Apartment A")}}
            st.session_state.building["num_floors"] = len(floors)
            st.session_state.current_floor = nf
            st.session_state.current_apartment = "A"
            st.session_state.current_room = "1"
            st.session_state.page = "Dashboard"; st.rerun()
        st.divider()
        if st.button("🏗️ Load Example Building", use_container_width=True):
            load_example_building(); st.session_state.page = "Dashboard"; st.rerun()
        if st.button("🔄 New / Reset Project", use_container_width=True):
            for k in ["building","floors","templates","current_floor","current_apartment","current_room"]:
                st.session_state.pop(k, None)
            st.rerun()

# ─────────────────────────────────────────────
# ELECTRIC APPLIANCE TABLE  (room level)
# ─────────────────────────────────────────────
def render_elec_appliance_table(room, kp):
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("➕ Add Blank", use_container_width=True, key=f"eblank_{kp}"):
            room["appliances"].append({"name": "New Appliance", "watts": 100, "hours": 1})
            st.rerun()
    with c2:
        psel = st.selectbox("Preset", ["— select —"] + [t["name"] for t in ELEC_APPLIANCE_TEMPLATES],
                             key=f"epsel_{kp}", label_visibility="collapsed")
        if psel != "— select —":
            if st.button("➕ Add Preset", use_container_width=True, key=f"eaddp_{kp}"):
                m = next((t for t in ELEC_APPLIANCE_TEMPLATES if t["name"] == psel), None)
                if m:
                    room["appliances"].append({"name": m["name"], "watts": m["watts"], "hours": m["hours"]})
                    st.rerun()
    appls = room["appliances"]
    if appls:
        hcols = st.columns([0.35, 2.1, 1.15, 1.15, 0.75])
        for col, lbl in zip(hcols, ["**#**","**Name**","**Watts**","**Hrs/day**","**Del**"]):
            col.markdown(lbl)
        to_del = None
        for i, a in enumerate(appls):
            row = st.columns([0.35, 2.1, 1.15, 1.15, 0.75])
            row[0].write(i+1)
            a["name"]  = row[1].text_input("",  value=a["name"],  key=f"ean_{kp}_{i}", label_visibility="collapsed")
            a["watts"] = row[2].number_input("", 0, 5000, int(a["watts"]),  key=f"eaw_{kp}_{i}", label_visibility="collapsed")
            a["hours"] = row[3].number_input("", 0.0, 24.0, float(a["hours"]), step=0.5, key=f"eah_{kp}_{i}", label_visibility="collapsed")
            if row[4].button("🗑️", key=f"ead_{kp}_{i}"): to_del = i
        if to_del is not None:
            room["appliances"].pop(to_del); st.rerun()
    else:
        st.info("No electric appliances yet.")

# ─────────────────────────────────────────────
# GAS APPLIANCE TABLE  (apartment level)
# ─────────────────────────────────────────────
def render_gas_appliance_table(apt, kp):
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("➕ Add Blank", use_container_width=True, key=f"gblank_{kp}"):
            apt["gas_appliances"].append({"name": "New Gas Appliance", "kwh_per_hour": 1.0, "hours": 1.0})
            st.rerun()
    with c2:
        gsel = st.selectbox("Preset", ["— select —"] + [t["name"] for t in GAS_APPLIANCE_TEMPLATES],
                             key=f"gpsel_{kp}", label_visibility="collapsed")
        if gsel != "— select —":
            if st.button("➕ Add Preset", use_container_width=True, key=f"gaddp_{kp}"):
                m = next((t for t in GAS_APPLIANCE_TEMPLATES if t["name"] == gsel), None)
                if m:
                    apt["gas_appliances"].append({"name": m["name"], "kwh_per_hour": m["kwh_per_hour"], "hours": m["hours"]})
                    st.rerun()
    gappls = apt["gas_appliances"]
    if gappls:
        hcols = st.columns([0.35, 2.1, 1.4, 1.15, 0.75])
        for col, lbl in zip(hcols, ["**#**","**Name**","**kWh/hr**","**Hrs/day**","**Del**"]):
            col.markdown(lbl)
        to_del = None
        for i, a in enumerate(gappls):
            row = st.columns([0.35, 2.1, 1.4, 1.15, 0.75])
            row[0].write(i+1)
            a["name"]         = row[1].text_input("",  value=a["name"],          key=f"gan_{kp}_{i}", label_visibility="collapsed")
            a["kwh_per_hour"] = row[2].number_input("", 0.0, 50.0, float(a["kwh_per_hour"]), step=0.1, key=f"gakph_{kp}_{i}", label_visibility="collapsed")
            a["hours"]        = row[3].number_input("", 0.0, 24.0, float(a["hours"]),        step=0.5, key=f"gah_{kp}_{i}",   label_visibility="collapsed")
            if row[4].button("🗑️", key=f"gad_{kp}_{i}"): to_del = i
        if to_del is not None:
            apt["gas_appliances"].pop(to_del); st.rerun()
    else:
        st.info("No gas appliances yet.")

# ─────────────────────────────────────────────
# PAGE: DASHBOARD
# ─────────────────────────────────────────────
def page_dashboard():
    floors = st.session_state.floors
    cf = st.session_state.current_floor
    ca = st.session_state.current_apartment
    cr = st.session_state.current_room

    # Safety guards
    if cf not in floors: cf = sorted(floors.keys())[0]; st.session_state.current_floor = cf
    if ca not in floors[cf]["apartments"]:
        ca = sorted(floors[cf]["apartments"].keys())[0]; st.session_state.current_apartment = ca
    if cr not in floors[cf]["apartments"][ca]["rooms"]:
        cr = sorted(floors[cf]["apartments"][ca]["rooms"].keys())[0]; st.session_state.current_room = cr

    apt  = floors[cf]["apartments"][ca]
    room = apt["rooms"][cr]
    kp   = f"{cf}_{ca}_{cr}"   # unique key prefix

    st.title("🏢 Building Energy Forecaster")
    st.markdown(
        f"**Editing:** Floor {cf} › Apt {ca} (*{apt['name']}*) › Room {cr} (*{room['name']}*)  |  "
        f"Climate: **{st.session_state.building['climate_zone']}**  |  "
        f"Location: **{st.session_state.building['location_type']}**"
    )
    st.divider()

    # Template toolbar
    tb1, tb2, tb3, tb4 = st.columns(4)
    with tb1:
        tpl_inp = st.text_input("", value=f"F{cf}_Apt{ca}_Rm{cr}", placeholder="Template name…",
                                 label_visibility="collapsed", key="tpl_name_inp")
    with tb2:
        if st.button("💾 Save Room as Template", use_container_width=True):
            if tpl_inp.strip():
                st.session_state.templates[tpl_inp] = copy.deepcopy(room)
                st.success(f"Saved: **{tpl_inp}**")
            else: st.error("Enter a template name first.")
    with tb3:
        tpl_sel = st.selectbox("", ["— select —"] + list(st.session_state.templates.keys()),
                                key="tpl_sel", label_visibility="collapsed") if st.session_state.templates else "— select —"
        if not st.session_state.templates: st.caption("No templates yet.")
    with tb4:
        if tpl_sel != "— select —":
            if st.button("⬇️ Apply Template", use_container_width=True):
                room.update(copy.deepcopy(st.session_state.templates[tpl_sel]))
                st.success(f"Applied: **{tpl_sel}**"); st.rerun()

    st.divider()

    # ── Tabs: Electricity | Gas ──────────────
    tab_elec, tab_gas = st.tabs(["⚡ Electricity", "🔥 Gas"])

    # ════════════ ELECTRICITY TAB ════════════
    with tab_elec:
        room["name"] = st.text_input("Room name", value=room["name"], key=f"rname_{kp}")
        left, right = st.columns([1, 1], gap="large")

        with left:
            st.subheader("🧱 Room Shell")
            c1, c2 = st.columns(2)
            with c1:
                room["area_m2"] = st.number_input("Floor area (m²)", 5.0, 100.0, float(room["area_m2"]), step=0.5, key=f"area_{kp}")
                room["wall_thickness_cm"] = st.slider("Wall thickness (cm)", 10, 50, int(room["wall_thickness_cm"]), key=f"wt_{kp}")
                room["wall_material"] = st.selectbox("Wall material", list(WALL_U_VALUES.keys()),
                    index=list(WALL_U_VALUES.keys()).index(room.get("wall_material","Brick")), key=f"wm_{kp}")
                st.caption(f"U-value: {WALL_U_VALUES[room['wall_material']]} W/m²K")
            with c2:
                room["ceiling_height_m"] = st.number_input("Ceiling height (m)", 2.0, 4.0, float(room["ceiling_height_m"]), step=0.1, key=f"ch_{kp}")
                room["window_count"] = st.number_input("Window count", 0, 20, int(room["window_count"]), key=f"wc_{kp}")
                room["window_area_per_window_m2"] = st.number_input("Window area each (m²)", 0.5, 5.0, float(room["window_area_per_window_m2"]), step=0.1, key=f"wa_{kp}")
                room["window_type"] = st.selectbox("Window type", ["Single","Double"],
                    index=["Single","Double"].index(room.get("window_type","Single")), key=f"wtype_{kp}")
                st.caption(f"Window U-value: {WINDOW_U_VALUES[room['window_type']]} W/m²K")
                room["plug_count"] = st.number_input("Plug/outlet count", 0, 30, int(room["plug_count"]), key=f"pc_{kp}")

            # Copy room
            st.subheader("📋 Copy Room To…")
            cp1, cp2, cp3, cp4 = st.columns([2,2,2,1])
            with cp1: tgt_fl = st.selectbox("Floor", sorted(floors.keys()), key=f"cpf_{kp}")
            with cp2:
                tgt_apt_opts = sorted(floors[tgt_fl]["apartments"].keys())
                tgt_apt = st.selectbox("Apt", tgt_apt_opts, key=f"cpa_{kp}")
            with cp3:
                tgt_rm_opts = sorted(floors[tgt_fl]["apartments"][tgt_apt]["rooms"].keys()) + ["NEW"]
                tgt_rm = st.selectbox("Room", tgt_rm_opts, key=f"cpr_{kp}")
            with cp4:
                st.write(""); st.write("")
                if st.button("📤", key=f"docopy_{kp}", use_container_width=True):
                    dest = floors[tgt_fl]["apartments"][tgt_apt]["rooms"]
                    nrk  = next_room_key(list(dest.keys())) if tgt_rm == "NEW" else tgt_rm
                    dest[nrk] = copy.deepcopy(room)
                    st.success(f"Copied → Fl {tgt_fl} / Apt {tgt_apt} / Rm {nrk}"); st.rerun()

            if len(apt["rooms"]) > 1:
                st.divider()
                if st.button(f"🗑️ Delete Room {cr}: {room['name']}", use_container_width=True, key=f"delrm_{kp}"):
                    del apt["rooms"][cr]
                    st.session_state.current_room = sorted(apt["rooms"].keys())[0]; st.rerun()

        with right:
            st.subheader("⚡ Electric Appliances")
            render_elec_appliance_table(room, kp)
            st.divider()
            st.subheader("📊 Quick Results — Electricity")
            er = calculate_room_electricity(room, st.session_state.building["climate_zone"], st.session_state.building["location_type"])
            render_energy_cost_block("Room Elec", er["total"], elec_cost, "⚡")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("🌡️ Cooling",    f"{er['wall_load']+er['window_load']:,.0f} kWh")
            m2.metric("⚡ Appliances", f"{er['appliance_total']:,.0f} kWh")
            m3.metric("🔌 Plug Load",  f"{er['plug_load']:,.0f} kWh")
            m4.metric("📐 Area",       f"{er['area_m2']:.0f} m²")
            st.divider()
            apt_er = calculate_apartment_electricity(apt, st.session_state.building["climate_zone"], st.session_state.building["location_type"])
            st.markdown(f"**🚪 Apartment {ca} — Electricity Total ({len(apt['rooms'])} rooms)**")
            render_energy_cost_block("Apt Elec", apt_er["total"], elec_cost, "⚡")

    # ════════════ GAS TAB ════════════════════
    with tab_gas:
        st.subheader(f"🔥 Gas Settings — Apartment {ca}: {apt['name']}")
        st.caption("Gas inputs are set at the apartment level (heating is summed from all rooms).")

        g1, g2 = st.columns(2)
        with g1:
            apt["occupants"] = st.number_input(
                "Number of occupants", 1, 20, int(apt.get("occupants", 4)),
                help="Used for hot water gas calculation (occupants × 1,095 kWh/year)",
                key=f"occ_{cf}_{ca}"
            )
            apt["cooking_kwh"] = st.number_input(
                "Cooking gas (kWh/year)", 0.0, 10000.0, float(apt.get("cooking_kwh", 1825.0)),
                step=50.0,
                help="Default 1,825 kWh/year for a family of 4",
                key=f"cook_{cf}_{ca}"
            )
        with g2:
            st.info(
                f"🌡️ **Gas heating climate factor:**  "
                f"{CLIMATE_ZONES[st.session_state.building['climate_zone']]['gas_heating']}  "
                f"(Zone: {st.session_state.building['climate_zone']})\n\n"
                f"🏠 **Heater efficiency:** {HEATER_EFF*100:.0f}%\n\n"
                f"📅 **Degree-days:** {DEGREE_DAYS}"
            )

        st.subheader("🔥 Gas Appliances")
        render_gas_appliance_table(apt, f"{cf}_{ca}")

        st.divider()
        st.subheader("📊 Quick Results — Gas")
        gr = calculate_apartment_gas(apt, st.session_state.building["climate_zone"])

        render_energy_cost_block("Apt Gas Total", gr["total"], gas_cost, "🔥")

        gm1, gm2, gm3, gm4 = st.columns(4)
        gm1.metric("🌡️ Heating",    f"{gr['heating']:,.0f} kWh/yr")
        gm2.metric("🚿 Hot Water",  f"{gr['hot_water']:,.0f} kWh/yr")
        gm3.metric("🍳 Cooking",    f"{gr['cooking']:,.0f} kWh/yr")
        gm4.metric("🔥 Gas Appls",  f"{gr['gas_appliances']:,.0f} kWh/yr")

        st.divider()
        st.subheader("⚡🔥 Combined — This Apartment")
        apt_er2 = calculate_apartment_electricity(apt, st.session_state.building["climate_zone"], st.session_state.building["location_type"])
        combined = apt_er2["total"] + gr["total"]
        elec_pct = (apt_er2["total"] / combined * 100) if combined > 0 else 0
        gas_pct  = (gr["total"]      / combined * 100) if combined > 0 else 0

        cm1, cm2, cm3 = st.columns(3)
        cm1.metric("⚡ Electricity",  f"{apt_er2['total']:,.0f} kWh/yr  ({elec_pct:.1f}%)")
        cm2.metric("🔥 Gas",          f"{gr['total']:,.0f} kWh/yr  ({gas_pct:.1f}%)")
        cm3.metric("🏠 Combined",     f"{combined:,.0f} kWh/yr")



# ─────────────────────────────────────────────
# PAGE: BUILDING SETTINGS
# ─────────────────────────────────────────────
def page_building_settings():
    st.title("⚙️ Building Settings")
    b = st.session_state.building

    c1, c2 = st.columns(2)
    with c1:
        b["name"] = st.text_input("Building name", value=b["name"])
        b["climate_zone"] = st.selectbox("Climate zone", list(CLIMATE_ZONES.keys()),
            index=list(CLIMATE_ZONES.keys()).index(b.get("climate_zone","Coast")))
        zi = CLIMATE_ZONES[b["climate_zone"]]
        st.caption(f"ℹ️ {zi['desc']}  |  Elec cooling ×{zi['elec_cooling']}  |  Gas heating ×{zi['gas_heating']}")
        b["location_type"] = st.selectbox("Location type", list(LOCATION_MULTIPLIERS.keys()),
            index=list(LOCATION_MULTIPLIERS.keys()).index(b.get("location_type","Urban")))
        st.caption(f"ℹ️ Location multiplier: ×{LOCATION_MULTIPLIERS[b['location_type']]}")

    with c2:
        st.subheader("💵 Energy Prices")
        b["electricity_price_usd"] = st.number_input(
            "Electricity price (USD/kWh)", 0.001, 2.0,
            float(b.get("electricity_price_usd", 0.12)), step=0.001, format="%.3f")
        b["gas_price_usd"] = st.number_input(
            "Gas price (USD/kWh)", 0.001, 1.0,
            float(b.get("gas_price_usd", 0.02)), step=0.001, format="%.3f",
            help="Default $0.02/kWh — Algeria subsidized rate")
        b["exchange_rate"] = st.number_input(
            "Exchange rate (1 USD = ? DZD)", 1.0, 10000.0,
            float(b.get("exchange_rate", 135.0)), step=1.0)
        dzd_e = b["electricity_price_usd"] * b["exchange_rate"]
        dzd_g = b["gas_price_usd"]         * b["exchange_rate"]
        st.caption(f"Electricity: ≈ {dzd_e:.2f} DZD/kWh  |  Gas: ≈ {dzd_g:.2f} DZD/kWh")
        st.session_state.show_cost = st.checkbox("Show cost estimates in results", value=st.session_state.show_cost)

    st.divider()
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
                st.success(f"Floor {src} copied to Floor {tgt}!"); st.rerun()
    else:
        st.info("Need at least 2 floors to copy.")

    st.divider()
    st.subheader("🏗️ Building Overview")
    for fn in fnums:
        for ak, av in sorted(st.session_state.floors[fn]["apartments"].items()):
            rooms     = av["rooms"]
            room_list = ", ".join([f"{rk}:{rv['name']}" for rk,rv in sorted(rooms.items())])
            st.write(f"  Floor **{fn}** › Apt **{ak}** ({av['name']}, {av.get('occupants',4)} occupants) — {len(rooms)} room(s): {room_list}")

    with st.expander("📖 U-Value & Multiplier Reference"):
        st.markdown("**Wall Materials**")
        st.dataframe(pd.DataFrame([
            {"Material": k, "U-value (W/m²K)": v,
             "Rating": "Excellent" if v<0.5 else "Good" if v<1.0 else "Average" if v<1.7 else "Poor"}
            for k, v in WALL_U_VALUES.items()
        ]), use_container_width=True, hide_index=True)
        st.markdown("**Climate Zone Multipliers**")
        st.dataframe(pd.DataFrame([
            {"Zone": k, "Elec Cooling ×": v["elec_cooling"],
             "Elec Heating ×": v["elec_heating"], "Gas Heating ×": v["gas_heating"], "Description": v["desc"]}
            for k, v in CLIMATE_ZONES.items()
        ]), use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────
# PAGE: ROOM TEMPLATES
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
                st.write(f"**Area:** {tpl_data.get('area_m2','—')} m²  |  **Wall:** {tpl_data.get('wall_material','—')}  |  "
                         f"**Windows:** {tpl_data.get('window_count','—')} × {tpl_data.get('window_type','—')}")
                appls = tpl_data.get("appliances", [])
                if appls: st.write(f"**Appliances ({len(appls)}):** " + ", ".join([a["name"] for a in appls]))
            with tc2:
                nn = st.text_input("Rename", value=tpl_name, key=f"ren_{tpl_name}")
                if nn != tpl_name and st.button("💾", key=f"savren_{tpl_name}"):
                    st.session_state.templates[nn] = st.session_state.templates.pop(tpl_name); st.rerun()
                if st.button("🗑️ Delete", key=f"dtpl_{tpl_name}"):
                    del st.session_state.templates[tpl_name]; st.rerun()
    st.divider()
    ec1, ec2 = st.columns(2)
    with ec1:
        st.download_button("⬇️ Export Templates (JSON)",
            data=json.dumps(st.session_state.templates, indent=2),
            file_name="templates.json", mime="application/json")
    with ec2:
        upl = st.file_uploader("⬆️ Import Templates (JSON)", type=["json"], key="tpl_imp")
        if upl:
            try:
                imp = json.load(upl)
                st.session_state.templates.update(imp)
                st.success(f"Imported {len(imp)} template(s)."); st.rerun()
            except Exception as e: st.error(f"Import failed: {e}")

# ─────────────────────────────────────────────
# PAGE: RESULTS & FORECAST
# ─────────────────────────────────────────────
def page_results():
    st.title("📈 Results & Forecast")
    b       = st.session_state.building
    results = calculate_building_total(st.session_state.floors, b["climate_zone"], b["location_type"])

    elec_kwh  = results["building_elec"]
    gas_kwh   = results["building_gas"]
    total_kwh = results["total_energy"]
    eui_e     = results["eui_elec"]
    eui_t     = results["eui_total"]
    bench     = get_eui_benchmark(eui_e)

    elec_pct = (elec_kwh / total_kwh * 100) if total_kwh > 0 else 0
    gas_pct  = (gas_kwh  / total_kwh * 100) if total_kwh > 0 else 0

    # ── Building summary KPIs ─────────────────
    st.subheader("🏢 Building Summary")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("⚡ Electricity",   f"{elec_kwh:,.0f} kWh/yr  ({elec_pct:.1f}%)")
    k2.metric("🔥 Gas",           f"{gas_kwh:,.0f} kWh/yr  ({gas_pct:.1f}%)")
    k3.metric("🏠 Total Energy",  f"{total_kwh:,.0f} kWh/yr")
    k4.metric("📐 EUI (Elec)",    f"{eui_e:,.1f} kWh/m²/yr")
    st.info(f"🔍 Electricity EUI Benchmark: **{bench}**  |  Total EUI: **{eui_t:,.1f} kWh/m²/yr**")

    # Building costs
    eu, ed = elec_cost(elec_kwh); gu, gd = gas_cost(gas_kwh)
    tu, td = eu + gu, ed + gd
    bc1, bc2, bc3 = st.columns(3)
    bc1.metric("⚡ Electricity Cost/yr", fmt_usd_dzd(eu, ed))
    bc2.metric("🔥 Gas Cost/yr",        fmt_usd_dzd(gu, gd))
    bc3.metric("🏠 Total Cost/yr",      fmt_usd_dzd(tu, td))
    bh1, bh2, bh3 = st.columns(3)
    bh1.metric("⚡ Elec Avg/hr",  fmt_usd_dzd_hr(eu, ed))
    bh2.metric("🔥 Gas Avg/hr",   fmt_usd_dzd_hr(gu, gd))
    bh3.metric("🏠 Total Avg/hr", fmt_usd_dzd_hr(tu, td))

    st.divider()

    # ── Floor charts ──────────────────────────
    st.subheader("📊 Energy by Floor")
    floor_df = pd.DataFrame([
        {"Floor": f"Floor {k}", "Electricity (kWh)": round(results["floor_elec"][k], 1),
         "Gas (kWh)": round(results["floor_gas"][k], 1)}
        for k in sorted(results["floor_elec"].keys())
    ])
    st.bar_chart(floor_df.set_index("Floor"))

    # ── Apartment table ───────────────────────
    st.subheader("🚪 Energy by Apartment")
    apt_rows = []
    for (fn, ak), ar in sorted(results["apt_results"].items()):
        apt_obj = st.session_state.floors[fn]["apartments"][ak]
        ek = ar["elec"]["total"]; gk = ar["gas"]["total"]; tk = ek + gk
        eu2, ed2 = elec_cost(ek); gu2, gd2 = gas_cost(gk); tcu, tcd = eu2+gu2, ed2+gd2
        apt_rows.append({
            "Floor": fn, "Apt": ak, "Name": apt_obj["name"],
            "Rooms": len(apt_obj["rooms"]),
            "Occupants": apt_obj.get("occupants", 4),
            "Elec (kWh/yr)": round(ek, 1),
            "Gas (kWh/yr)": round(gk, 1),
            "Total (kWh/yr)": round(tk, 1),
            "Elec %": round(ek/tk*100, 1) if tk > 0 else 0,
            "Gas %": round(gk/tk*100, 1) if tk > 0 else 0,
            "Elec Cost/yr (USD)": round(eu2, 2),
            "Gas Cost/yr (USD)": round(gu2, 2),
            "Total Cost/yr (USD)": round(tcu, 2),
            "Total Cost/yr (DZD)": round(tcd, 0),
            "Avg Cost/hr (USD)": round(tcu/HOURS_IN_YEAR, 5),
        })
    st.dataframe(pd.DataFrame(apt_rows), use_container_width=True, hide_index=True)

    # ── Room table ────────────────────────────
    st.subheader("🏠 Electricity by Room")
    room_rows = []
    for (fn, ak), ar in sorted(results["apt_results"].items()):
        apt_obj = st.session_state.floors[fn]["apartments"][ak]
        for rk, rr in sorted(ar["elec"]["room_results"].items()):
            rname = apt_obj["rooms"][rk]["name"]
            eu3, ed3 = elec_cost(rr["total"])
            room_rows.append({
                "Floor": fn, "Apt": ak, "Room": rk, "Name": rname,
                "Area (m²)": round(rr["area_m2"], 1),
                "Cooling (kWh)": round(rr["wall_load"]+rr["window_load"], 1),
                "Appliances (kWh)": round(rr["appliance_total"], 1),
                "Plug Load (kWh)": round(rr["plug_load"], 1),
                "Total (kWh/yr)": round(rr["total"], 1),
                "Cost/yr (USD)": round(eu3, 2),
                "Cost/yr (DZD)": round(ed3, 0),
                "Avg Cost/hr (USD)": round(eu3/HOURS_IN_YEAR, 5),
            })
    st.dataframe(pd.DataFrame(room_rows), use_container_width=True, hide_index=True)

    # ── Gas breakdown table ───────────────────
    st.subheader("🔥 Gas Breakdown by Apartment")
    gas_rows = []
    for (fn, ak), ar in sorted(results["apt_results"].items()):
        apt_obj = st.session_state.floors[fn]["apartments"][ak]
        gr2 = ar["gas"]
        gu3, gd3 = gas_cost(gr2["total"])
        gas_rows.append({
            "Floor": fn, "Apt": ak, "Name": apt_obj["name"],
            "Heating (kWh)": round(gr2["heating"], 1),
            "Hot Water (kWh)": round(gr2["hot_water"], 1),
            "Cooking (kWh)": round(gr2["cooking"], 1),
            "Gas Appliances (kWh)": round(gr2["gas_appliances"], 1),
            "Total Gas (kWh/yr)": round(gr2["total"], 1),
            "Gas Cost/yr (USD)": round(gu3, 2),
            "Gas Cost/yr (DZD)": round(gd3, 0),
            "Avg Gas Cost/hr (USD)": round(gu3/HOURS_IN_YEAR, 5),
        })
    st.dataframe(pd.DataFrame(gas_rows), use_container_width=True, hide_index=True)

    # ── Top 5 electric appliances ─────────────
    st.subheader("🔌 Top 5 Electric Appliances (Building-wide)")
    all_appls = []
    for (fn, ak), ar in results["apt_results"].items():
        for rk, rr in ar["elec"]["room_results"].items():
            for appl in rr["appliance_breakdown"]:
                all_appls.append({"Appliance": appl["name"], "kWh/yr": round(appl["kwh"],1),
                                   "Watts": appl["watts"], "Hrs/day": appl["hours"],
                                   "Floor": fn, "Apt": ak, "Room": rk})
    if all_appls:
        adf = pd.DataFrame(all_appls).sort_values("kWh/yr", ascending=False).head(5)
        st.dataframe(adf, use_container_width=True, hide_index=True)
        st.bar_chart(adf.set_index("Appliance")["kWh/yr"])
    else:
        st.info("No electric appliances added yet.")

    # ── EUI reference ─────────────────────────
    st.divider()
    st.subheader("📋 EUI Benchmark Reference")
    st.dataframe(pd.DataFrame([
        {"Max EUI (kWh/m²/yr)": t if t<999 else "999+", "Rating": l}
        for t, l in EUI_BENCHMARKS
    ]), use_container_width=True, hide_index=True)

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
        else: st.error("Enter a project name.")

    st.divider()
    st.subheader("📂 Load")
    projects = load_all_projects()
    if projects:
        opts = {f"{r[1]}  (saved: {r[2]})": r[0] for r in projects}
        sel  = st.selectbox("Select project", list(opts.keys()))
        lc1, lc2 = st.columns(2)
        with lc1:
            if st.button("📂 Load Project"):
                if load_project_from_db(opts[sel]): st.success("Loaded!"); st.rerun()
                else: st.error("Failed to load.")
        with lc2:
            if st.button("🗑️ Delete Project"):
                delete_project_from_db(opts[sel]); st.success("Deleted."); st.rerun()
    else:
        st.info("No saved projects.")

    st.divider()
    st.subheader("📤 Export / Import (JSON)")
    ec1, ec2 = st.columns(2)
    with ec1:
        st.download_button("⬇️ Export Project (JSON)", data=export_to_json(),
            file_name=f"{st.session_state.building['name'].replace(' ','_')}.json", mime="application/json")
    with ec2:
        upl = st.file_uploader("⬆️ Import Project (JSON)", type=["json"], key="proj_imp")
        if upl:
            ok, msg = import_from_json(upl.read().decode("utf-8"))
            if ok: st.success(msg); st.rerun()
            else: st.error(msg)

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
    └── 🚪 Apartment A  (occupants, cooking gas, gas appliances)
        ├── ▶ 🏠 Room 1: Living Room  (walls, windows, electric appliances)
        └──   🏠 Room 2: Bedroom
```

---

## Dashboard Tabs
- **⚡ Electricity tab** — Room-level inputs: walls, windows, plug count, electric appliances
- **🔥 Gas tab** — Apartment-level inputs: occupants, cooking kWh, gas appliances

---

## Gas Calculation Formula
```
Heating gas (kWh/yr) =
  (wall_UA + window_UA + roof_UA) × degree_days × 24h × climate_factor
  ÷ heater_efficiency ÷ 1000

Hot water gas (kWh/yr) = occupants × 1,095

Cooking gas (kWh/yr)   = user input (default 1,825 for family of 4)

Gas appliances (kWh/yr) = kWh/hr × hours/day × 365
```

**Constants:** Degree-days = 1,136  |  Heater efficiency = 65%  |  Roof U-value = 0.75 W/m²K

---

## Climate Multipliers
| Zone | Elec Cooling × | Gas Heating × |
|------|----------------|---------------|
| Coast | 1.1 | 0.8 |
| Desert | 1.4 | 0.6 |
| Mountains | 0.6 | 1.5 |
| City | 1.0 | 1.0 |

---

## Cost Display (3 values everywhere)
- **kWh/year** — total energy
- **Cost/year** — in USD and DZD
- **Avg cost/hour** — cost/year ÷ 8,760 hours

Default prices: **Electricity $0.12/kWh** | **Gas $0.02/kWh** (Algeria subsidized)

---

## Tips
- Click **🏗️ Load Example Building** to see a working 3-floor example
- Gas inputs are per-apartment; heating is automatically summed from all rooms
- Adjust gas price in **Building Settings** (default = $0.02 Algeria subsidized rate)
- Export as JSON to back up or share your project
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
    elif pg == "Room Templates":      page_templates()
    elif pg == "Results & Forecast":  page_results()
    elif pg == "Save/Load Project":   page_save_load()
    elif pg == "Help":                page_help()

if __name__ == "__main__":
    main()
