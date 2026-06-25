"""
Building Energy Forecaster — DTR C 3-2 Compliant
Hierarchy: Building → Floors → Apartments → Rooms
Algerian Official Thermal Regulation (DTR C 3-2)
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
    page_title="Building Energy Forecaster — DTR",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# DTR CONSTANTS
# ─────────────────────────────────────────────

# Surface resistances (m²·K/W)  DTR Chap 1 Table 1.2
R_SI = 0.13   # interior surface resistance (wall)
R_SE = 0.04   # exterior surface resistance (wall)

# Interior design temperature (°C)
T_BI = 20.0

# Hours in a year
HOURS_IN_YEAR = 8760

# DTR Annex 2 — Algerian materials (name, lambda W/m·K, display label)
DTR_MATERIALS = [
    ("Plaster (enduit plâtre)",            0.35,  "Plaster"),
    ("Mortar (enduit mortier)",            1.15,  "Mortar"),
    ("Brick hollow 10cm (brique creuse)",  0.48,  "Brick hollow"),
    ("Brick full (brique pleine)",         0.70,  "Brick full"),
    ("Brick perforated (brique perforée)", 0.60,  "Brick perf."),
    ("Concrete standard (béton courant)",  1.75,  "Concrete std"),
    ("Concrete light (béton léger)",       1.05,  "Concrete light"),
    ("Concrete heavy (béton lourd)",       1.80,  "Concrete heavy"),
    ("Expanded polystyrene low (PSE bas)", 0.038, "EPS low"),
    ("Expanded polystyrene high (PSE haut)",0.046,"EPS high"),
    ("Rock wool low (laine roche basse)",  0.038, "Rock wool low"),
    ("Rock wool high (laine roche haute)", 0.047, "Rock wool high"),
    ("Air gap (lame d'air)",              None,  "Air gap"),   # fixed R=0.16
    ("Tile (carrelage)",                   1.00,  "Tile"),
    ("Wood (bois)",                        0.15,  "Wood"),
    ("Glass (verre)",                      1.00,  "Glass"),
]
AIR_GAP_R = 0.16  # fixed R for air gap

# DTR zones — winter design temperature t_be by zone + altitude
# Format: {zone: [(max_altitude, t_be), ...]}  last entry = ≥ that altitude
DTR_ZONES = {
    "A":  [(300, 6),  (500, 3),  (1000, 1),  (9999, -1)],
    "B":  [(500, 2),  (1000, 0), (9999, -2)],
    "B'": [(500, 1),  (1000,-1), (9999, -3)],
    "C":  [(500,-1),  (1000,-3), (9999, -5)],
    "D":  [(500,-3),  (9999,-5)],
    "D'": [(9999,-5)],
}

# Default heating days per zone
DEFAULT_HEATING_DAYS = {"A": 60, "B": 90, "B'": 100, "C": 120, "D": 150, "D'": 180}

# DTR gas heating climate factor (replaces old gas multiplier)
# Colder zones → more heating needed
GAS_CLIMATE_FACTOR = {"A": 0.8, "B": 0.9, "B'": 1.0, "C": 1.1, "D": 0.6, "D'": 0.6}

# DTR Table 3.2 — Window U-values
WINDOW_TYPES = {
    "Single glazed — wood frame":          5.0,
    "Single glazed — metal frame":         5.8,
    "Double glazed 5-7mm — wood frame":    3.3,
    "Double glazed 5-7mm — metal frame":   4.0,
    "Double glazed 12-13mm — wood frame":  2.9,
    "Double glazed 12-13mm — metal frame": 3.7,
}

# DTR Table 3.3 — Door U-values
DOOR_TYPES = {
    "Wood — opaque":          3.5,
    "Wood — <30% glass":      4.0,
    "Wood — 30-60% glass":    4.5,
    "Metal — opaque":         5.8,
    "Metal — with glass":     5.8,
}

# DTR Table 6.4 — Tau coefficients for stairwells
# Key: (stairwell_type, apt_wall_insulated)
TAU_TABLE = {
    ("Open",        False): 0.55,
    ("Open",        True):  0.30,
    ("Closed",      False): 0.40,
    ("Closed",      True):  0.20,
    ("Central",     False): 0.25,
    ("Central",     True):  0.10,
    ("Smoke vents", False): 0.90,
    ("Smoke vents", True):  0.90,
}

# DTR Table 2.1 — D_ref coefficients by zone and building type
# D_ref = a*S1 + b*S2 + c*S3 + d*S4 + e*S5
# S1=roof, S2=ground floor, S3=ext walls, S4=doors, S5=windows
DREF_COEFFS = {
    # Zone: {Individual: (a,b,c,d,e), Collective: (a,b,c,d,e)}
    "A":  {"Individual": (1.10, 2.40, 1.40, 3.50, 4.50),
           "Collective": (1.10, 2.40, 1.20, 3.50, 4.50)},
    "B":  {"Individual": (1.20, 2.50, 1.50, 3.60, 4.60),
           "Collective": (1.20, 2.50, 1.30, 3.60, 4.60)},
    "B'": {"Individual": (1.25, 2.55, 1.55, 3.65, 4.65),
           "Collective": (1.25, 2.55, 1.35, 3.65, 4.65)},
    "C":  {"Individual": (1.30, 2.60, 1.60, 3.70, 4.70),
           "Collective": (1.30, 2.60, 1.40, 3.70, 4.70)},
    "D":  {"Individual": (1.40, 2.70, 1.70, 3.80, 4.80),
           "Collective": (1.40, 2.70, 1.50, 3.80, 4.80)},
    "D'": {"Individual": (1.40, 2.70, 1.70, 3.80, 4.80),
           "Collective": (1.40, 2.70, 1.50, 3.80, 4.80)},
}

# EUI benchmarks
EUI_BENCHMARKS = [
    (50,  "Excellent — Passive/Net-Zero standard"),
    (100, "Good — Energy-efficient building"),
    (150, "Average — Standard construction"),
    (200, "Below average — Needs improvement"),
    (300, "Poor — Major upgrade recommended"),
    (999, "Very poor — Urgent action required"),
]

# Gas constants
DEGREE_DAYS  = 1136
HEATER_EFF   = 0.65
ROOF_U_VALUE = 0.75

# DTR Annex 2 — electric appliance presets (unchanged)
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

DB_PATH = "building_energy.db"

# ─────────────────────────────────────────────
# DTR HELPER FUNCTIONS
# ─────────────────────────────────────────────
def get_t_be(zone, altitude):
    """Return winter design temperature t_be for a DTR zone + altitude."""
    entries = DTR_ZONES.get(zone, [(9999, 0)])
    for max_alt, t_be in entries:
        if altitude < max_alt:
            return t_be
    return entries[-1][1]

def get_delta_t(zone, altitude):
    """Return ΔT = T_BI - t_be."""
    return T_BI - get_t_be(zone, altitude)

def calc_layer_r(material_name, thickness_cm):
    """Calculate thermal resistance for a single wall layer."""
    if material_name == "Air gap (lame d'air)":
        return AIR_GAP_R
    mat = next((m for m in DTR_MATERIALS if m[0] == material_name), None)
    if mat is None or mat[1] is None:
        return 0.0
    lam = mat[1]
    return (thickness_cm / 100.0) / lam

def calc_wall_u(layers):
    """
    Calculate wall U-value from list of layers.
    layers = [{"material": str, "thickness_cm": float}, ...]
    Returns (R_total, U_value)
    """
    r_layers = sum(calc_layer_r(l["material"], l["thickness_cm"]) for l in layers)
    r_total  = R_SI + r_layers + R_SE
    u_value  = 1.0 / r_total if r_total > 0 else 0.0
    return r_total, u_value

def get_tau(stairwell_type, apt_wall_insulated):
    return TAU_TABLE.get((stairwell_type, apt_wall_insulated), 0.30)

def calc_d_ref(zone, building_type_label, s1, s2, s3, s4, s5):
    """Calculate reference heat loss D_ref from DTR Table 2.1."""
    coeffs = DREF_COEFFS.get(zone, {}).get(building_type_label, (1.1, 2.4, 1.2, 3.5, 4.5))
    a, b, c, d, e = coeffs
    return a*s1 + b*s2 + c*s3 + d*s4 + e*s5

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
def default_wall_layers():
    """Default multi-layer wall: mortar 2cm + brick 10cm + air gap + brick 10cm + plaster 2cm"""
    return [
        {"material": "Mortar (enduit mortier)",            "thickness_cm": 2.0},
        {"material": "Brick hollow 10cm (brique creuse)",  "thickness_cm": 10.0},
        {"material": "Air gap (lame d'air)",               "thickness_cm": 4.0},
        {"material": "Brick hollow 10cm (brique creuse)",  "thickness_cm": 10.0},
        {"material": "Plaster (enduit plâtre)",            "thickness_cm": 2.0},
    ]

def default_room(name="Room 1"):
    return {
        "name":                      name,
        "area_m2":                   20.0,
        "wall_layers":               default_wall_layers(),
        "window_count":              2,
        "window_area_per_window_m2": 1.5,
        "window_type":               "Single glazed — wood frame",
        "door_count":                1,
        "door_area_per_door_m2":     2.0,
        "door_type":                 "Wood — opaque",
        "plug_count":                4,
        "ceiling_height_m":          2.5,
        "adjacent_stairwell":        False,   # wall touches stairwell?
        "appliances":                [],
    }

def default_apartment(name="Apartment A"):
    return {
        "name":           name,
        "occupants":      4,
        "cooking_kwh":    1825.0,
        "gas_appliances": [],
        "rooms": {
            "1": default_room("Living Room"),
            "2": default_room("Bedroom"),
        },
    }

def default_stairwell():
    return {
        "type":                  "Closed",
        "stairwell_wall_ins":    False,
        "apt_wall_ins":          False,
        "lighting_bulbs":        4,
        "lighting_watts":        20,
        "lighting_hours":        6,
        "outlet_count":          2,
        "outlet_watts":          50,
        "outlet_hours":          2,
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
            "building_type":         "Apartment Building",
            "num_floors":            3,
            "dtr_zone":              "B",
            "altitude":              200,
            "heating_days":          dict(DEFAULT_HEATING_DAYS),
            "electricity_price_usd": 0.12,
            "gas_price_usd":         0.02,
            "exchange_rate":         135.0,
            "created_date":          datetime.now().strftime("%Y-%m-%d"),
            # S1-S5 for regulatory check
            "s1_roof":               120.0,
            "s2_ground":             120.0,
            "s3_ext_walls":          200.0,
            "s4_doors":              10.0,
            "s5_windows":            30.0,
            "stairwell":             default_stairwell(),
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
# ELECTRICITY CALCULATIONS  (DTR-based)
# ─────────────────────────────────────────────
def calculate_room_electricity(room, zone, altitude, heating_days_override=None):
    """kWh/year for one room using DTR formulas."""
    area      = room.get("area_m2", 20.0)
    ceiling_h = room.get("ceiling_height_m", 2.5)

    # Wall U-value from multi-layer builder
    layers  = room.get("wall_layers", default_wall_layers())
    _, u_wall = calc_wall_u(layers)

    # Window
    window_count     = room.get("window_count", 2)
    window_area_each = room.get("window_area_per_window_m2", 1.5)
    window_u         = WINDOW_TYPES.get(room.get("window_type", "Single glazed — wood frame"), 5.0)
    window_area_total = window_count * window_area_each

    # Door
    door_count     = room.get("door_count", 1)
    door_area_each = room.get("door_area_per_door_m2", 2.0)
    door_u         = DOOR_TYPES.get(room.get("door_type", "Wood — opaque"), 3.5)
    door_area_total = door_count * door_area_each

    # Wall area
    side       = math.sqrt(max(area, 1))
    gross_wall = 4 * side * ceiling_h
    wall_area  = max(gross_wall - window_area_total - door_area_total, 0)

    # DTR ΔT and heating days
    delta_t = get_delta_t(zone, altitude)
    hdg     = heating_days_override if heating_days_override else DEFAULT_HEATING_DAYS.get(zone, 90)
    plug_count = room.get("plug_count", 4)

    # DTR formulas: loss = Area × U × ΔT × 24h × heating_days / 1000
    wall_loss   = wall_area        * u_wall   * delta_t * 24 * hdg / 1000
    window_loss = window_area_total * window_u * delta_t * 24 * hdg / 1000
    door_loss   = door_area_total   * door_u   * delta_t * 24 * hdg / 1000

    # Appliance energy
    appliance_total     = 0.0
    appliance_breakdown = []
    for appl in room.get("appliances", []):
        w  = appl.get("watts", 0)
        h  = appl.get("hours", 0)
        n  = appl.get("name", "Unknown")
        nl = n.lower()
        days = 120 if ("ac" in nl or "air con" in nl) else 365
        kwh  = (w * h * days) / 1000
        appliance_total += kwh
        appliance_breakdown.append({"name": n, "kwh": kwh, "watts": w, "hours": h})

    # Plug load
    plug_load = plug_count * 50 * 8 * 365 / 1000

    total = wall_loss + window_loss + door_loss + appliance_total + plug_load

    return {
        "wall_loss":           wall_loss,
        "window_loss":         window_loss,
        "door_loss":           door_loss,
        "appliance_total":     appliance_total,
        "plug_load":           plug_load,
        "total":               total,
        "appliance_breakdown": appliance_breakdown,
        "area_m2":             area,
        "u_wall":              u_wall,
        "delta_t":             delta_t,
    }

def calculate_apartment_electricity(apt, zone, altitude, heating_days_override=None):
    total = 0.0; total_area = 0.0; room_results = {}
    for rk, rd in apt.get("rooms", {}).items():
        res = calculate_room_electricity(rd, zone, altitude, heating_days_override)
        total += res["total"]; total_area += res["area_m2"]
        room_results[rk] = res
    return {"total": total, "total_area": total_area, "room_results": room_results}

def calculate_stairwell_electricity(sw):
    """Annual kWh for stairwell lighting + outlets."""
    lighting_kwh = (sw.get("lighting_bulbs",4) * sw.get("lighting_watts",20) *
                    sw.get("lighting_hours",6) * 365) / 1000
    outlet_kwh   = (sw.get("outlet_count",2) * sw.get("outlet_watts",50) *
                    sw.get("outlet_hours",2) * 365) / 1000
    return lighting_kwh, outlet_kwh

# ─────────────────────────────────────────────
# GAS CALCULATIONS  (updated ΔT from DTR)
# ─────────────────────────────────────────────
def calculate_room_gas_heating(room, zone, altitude):
    """Gas heating kWh/year for one room using DTR ΔT."""
    area      = room.get("area_m2", 20.0)
    ceiling_h = room.get("ceiling_height_m", 2.5)
    volume    = area * ceiling_h

    layers  = room.get("wall_layers", default_wall_layers())
    _, u_wall = calc_wall_u(layers)

    window_count     = room.get("window_count", 2)
    window_area_each = room.get("window_area_per_window_m2", 1.5)
    window_u         = WINDOW_TYPES.get(room.get("window_type","Single glazed — wood frame"), 5.0)
    window_area_total = window_count * window_area_each

    door_count     = room.get("door_count", 1)
    door_area_each = room.get("door_area_per_door_m2", 2.0)
    door_u         = DOOR_TYPES.get(room.get("door_type","Wood — opaque"), 3.5)
    door_area_total = door_count * door_area_each

    side       = math.sqrt(max(area, 1))
    gross_wall = 4 * side * ceiling_h
    wall_area  = max(gross_wall - window_area_total - door_area_total, 0)
    roof_area  = area

    wall_ua   = wall_area         * u_wall
    window_ua = window_area_total  * window_u
    door_ua   = door_area_total    * door_u
    roof_ua   = roof_area          * ROOF_U_VALUE
    total_ua  = wall_ua + window_ua + door_ua + roof_ua

    delta_t        = get_delta_t(zone, altitude)
    gas_climate    = GAS_CLIMATE_FACTOR.get(zone, 1.0)
    heating_kwh    = (total_ua * DEGREE_DAYS * 24 / 1000) * gas_climate / HEATER_EFF

    return heating_kwh

def calculate_apartment_gas(apt, zone, altitude):
    heating_total = 0.0
    for rd in apt.get("rooms", {}).values():
        heating_total += calculate_room_gas_heating(rd, zone, altitude)

    occupants    = apt.get("occupants", 4)
    hot_water    = occupants * 1095.0
    cooking      = apt.get("cooking_kwh", 1825.0)

    gas_appl_total = 0.0
    gas_appl_bd    = []
    for appl in apt.get("gas_appliances", []):
        n   = appl.get("name","Unknown")
        kph = appl.get("kwh_per_hour", 0.0)
        h   = appl.get("hours", 0.0)
        kwh = kph * h * 365
        gas_appl_total += kwh
        gas_appl_bd.append({"name": n, "kwh": kwh, "kwh_per_hour": kph, "hours": h})

    total = heating_total + hot_water + cooking + gas_appl_total
    return {
        "heating":            heating_total,
        "hot_water":          hot_water,
        "cooking":            cooking,
        "gas_appliances":     gas_appl_total,
        "total":              total,
        "gas_appl_breakdown": gas_appl_bd,
    }

# ─────────────────────────────────────────────
# BUILDING TOTALS
# ─────────────────────────────────────────────
def calculate_building_total(floors_data, building):
    zone     = building.get("dtr_zone","B")
    altitude = building.get("altitude", 200)
    hdg      = building.get("heating_days", DEFAULT_HEATING_DAYS).get(zone, 90)

    building_elec = 0.0; building_gas = 0.0
    total_area    = 0.0
    floor_elec    = {}; floor_gas = {}
    apt_results   = {}

    for fn, fd in floors_data.items():
        fe = 0.0; fg = 0.0
        for ak, ad in fd.get("apartments", {}).items():
            er = calculate_apartment_electricity(ad, zone, altitude, hdg)
            gr = calculate_apartment_gas(ad, zone, altitude)
            fe += er["total"]; fg += gr["total"]
            building_elec += er["total"]; building_gas += gr["total"]
            total_area    += er["total_area"]
            apt_results[(fn, ak)] = {"elec": er, "gas": gr}
        floor_elec[fn] = fe; floor_gas[fn] = fg

    # Stairwell electricity (apartment buildings only)
    stairwell_kwh = 0.0
    if building.get("building_type") == "Apartment Building":
        sw = building.get("stairwell", default_stairwell())
        l_kwh, o_kwh = calculate_stairwell_electricity(sw)
        stairwell_kwh = l_kwh + o_kwh
        building_elec += stairwell_kwh

    total_energy = building_elec + building_gas
    eui_elec  = building_elec / total_area if total_area > 0 else 0
    eui_total = total_energy  / total_area if total_area > 0 else 0

    return {
        "building_elec":  building_elec,
        "building_gas":   building_gas,
        "stairwell_kwh":  stairwell_kwh,
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
# REGULATORY COMPLIANCE  (DTR Table 2.1)
# ─────────────────────────────────────────────
def check_compliance(building, d_t):
    """
    d_t = actual total heat loss coefficient (W/°C) entered by user or computed.
    Returns (d_ref, d_t_max, passed, label)
    """
    zone  = building.get("dtr_zone","B")
    btype = "Collective" if building.get("building_type") == "Apartment Building" else "Individual"
    s1 = building.get("s1_roof",    120.0)
    s2 = building.get("s2_ground",  120.0)
    s3 = building.get("s3_ext_walls",200.0)
    s4 = building.get("s4_doors",   10.0)
    s5 = building.get("s5_windows", 30.0)
    d_ref  = calc_d_ref(zone, btype, s1, s2, s3, s4, s5)
    d_max  = 1.05 * d_ref
    passed = d_t <= d_max
    return d_ref, d_max, passed

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

def fmt_hr(usd, dzd):
    return f"${usd/HOURS_IN_YEAR:,.5f} USD/hr  /  {dzd/HOURS_IN_YEAR:,.4f} DZD/hr"

def render_cost_block(label, kwh, cost_fn, icon="⚡"):
    usd, dzd = cost_fn(kwh)
    c1, c2, c3 = st.columns(3)
    c1.metric(f"{icon} {label} — kWh/yr", f"{kwh:,.1f}")
    c2.metric("💵 Cost / Year",           fmt_usd_dzd(usd, dzd))
    c3.metric("⏱️ Avg Cost / Hour",       fmt_hr(usd, dzd))

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
        "name": "Example Building", "building_type": "Apartment Building",
        "num_floors": 3, "dtr_zone": "B", "altitude": 300,
        "heating_days": dict(DEFAULT_HEATING_DAYS),
        "electricity_price_usd": 0.12, "gas_price_usd": 0.02,
        "exchange_rate": 135.0, "created_date": datetime.now().strftime("%Y-%m-%d"),
        "s1_roof": 120.0, "s2_ground": 120.0, "s3_ext_walls": 200.0,
        "s4_doors": 10.0, "s5_windows": 30.0,
        "stairwell": default_stairwell(),
    }
    elec_appls = [
        {"name": "Refrigerator", "watts": 150, "hours": 24},
        {"name": "AC (split)",   "watts": 800, "hours": 8},
        {"name": "TV (LED)",     "watts": 100, "hours": 5},
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
                "2": make_room("Bedroom", ab, [{"name":"TV (LED)","watts":100,"hours":5}]),
            },
        }
    st.session_state.floors = {
        1: {"apartments": {"A": make_apt("Apt 1A",35,20), "B": make_apt("Apt 1B",30,18)}},
        2: {"apartments": {"A": make_apt("Apt 2A",35,20), "B": make_apt("Apt 2B",30,18)}},
        3: {"apartments": {"A": make_apt("Apt 3A",35,20)}},
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
        st.caption("DTR C 3-2 Compliant  |  Master Year Project")
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
        t_be = get_t_be(b.get("dtr_zone","B"), b.get("altitude",200))
        st.caption(f"Zone {b.get('dtr_zone','B')} | Alt {b.get('altitude',200)}m | t_be={t_be}°C")

        floors     = st.session_state.floors
        floor_nums = sorted(floors.keys())
        cf = st.session_state.current_floor
        ca = st.session_state.current_apartment
        cr = st.session_state.current_room

        # Stairwell node (apartment buildings only)
        if b.get("building_type") == "Apartment Building":
            sw = b.get("stairwell", default_stairwell())
            tau = get_tau(sw["type"], sw["apt_wall_ins"])
            with st.expander("🪜 Stairwell", expanded=st.session_state.page == "Stairwell"):
                st.caption(f"Type: {sw['type']} | τ = {tau}")
                if st.button("⚙️ Edit Stairwell", use_container_width=True, key="edit_sw"):
                    st.session_state.page = "Stairwell"; st.rerun()

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
                        st.session_state.current_floor = floor_num
                        st.session_state.current_apartment = nk
                        st.session_state.current_room = "1"
                        st.session_state.page = "Dashboard"; st.rerun()
                with fc2:
                    if st.button("📋 Floor", key=f"copy_fl_{floor_num}", use_container_width=True):
                        nf = next_floor_num(floors)
                        floors[nf] = copy.deepcopy(floors[floor_num])
                        b["num_floors"] = len(floors)
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
                            b["num_floors"] = len(floors)
                            rem = sorted(floors.keys())
                            fa  = sorted(floors[rem[0]]["apartments"].keys())[0]
                            fr  = sorted(floors[rem[0]]["apartments"][fa]["rooms"].keys())[0]
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
                                    ra = sorted(apts.keys())[0]
                                    rr = sorted(apts[ra]["rooms"].keys())[0]
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
            b["num_floors"] = len(floors)
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
# WALL LAYER BUILDER  (DTR multi-layer)
# ─────────────────────────────────────────────
def render_wall_layer_builder(room, kp):
    """Renders the multi-layer DTR wall builder for a room."""
    st.subheader("🧱 Multi-Layer Wall Builder (DTR Annex 2)")
    layers = room.setdefault("wall_layers", default_wall_layers())

    mat_names = [m[0] for m in DTR_MATERIALS]

    # Header
    h1, h2, h3, h4 = st.columns([3, 1.5, 1.5, 0.7])
    h1.markdown("**Material**"); h2.markdown("**Thickness (cm)**")
    h3.markdown("**R (m²·K/W)**"); h4.markdown("**Del**")

    to_del = None
    for i, layer in enumerate(layers):
        c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 0.7])
        # Material selector
        cur_idx = mat_names.index(layer["material"]) if layer["material"] in mat_names else 0
        layer["material"] = c1.selectbox("", mat_names, index=cur_idx,
                                          key=f"lmat_{kp}_{i}", label_visibility="collapsed")
        # Thickness
        if layer["material"] == "Air gap (lame d'air)":
            c2.markdown(f"**—** *(fixed)*")
            layer["thickness_cm"] = 4.0
        else:
            layer["thickness_cm"] = c2.number_input("", 0.5, 100.0,
                                                      float(layer.get("thickness_cm", 5.0)),
                                                      step=0.5, key=f"lthk_{kp}_{i}",
                                                      label_visibility="collapsed")
        r_layer = calc_layer_r(layer["material"], layer["thickness_cm"])
        c3.markdown(f"`{r_layer:.3f}`")
        if c4.button("🗑️", key=f"ldel_{kp}_{i}"): to_del = i

    if to_del is not None:
        layers.pop(to_del); st.rerun()

    # Add layer button
    if st.button("➕ Add Layer", key=f"ladd_{kp}"):
        layers.append({"material": mat_names[0], "thickness_cm": 5.0}); st.rerun()

    # Summary
    r_total, u_val = calc_wall_u(layers)
    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("R_total (m²·K/W)", f"{r_total:.3f}")
    sc2.metric("U-value (W/m²·K)", f"{u_val:.3f}")
    quality = "✅ Well insulated" if u_val < 1.0 else ("⚠️ Moderate" if u_val < 2.0 else "❌ Poor insulation")
    sc3.metric("Insulation Quality", quality)

# ─────────────────────────────────────────────
# ELECTRIC APPLIANCE TABLE
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
# GAS APPLIANCE TABLE
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
            a["hours"]        = row[3].number_input("", 0.0, 24.0, float(a["hours"]),         step=0.5, key=f"gah_{kp}_{i}",   label_visibility="collapsed")
            if row[4].button("🗑️", key=f"gad_{kp}_{i}"): to_del = i
        if to_del is not None:
            apt["gas_appliances"].pop(to_del); st.rerun()
    else:
        st.info("No gas appliances yet.")

# ─────────────────────────────────────────────
# PAGE: STAIRWELL EDITOR
# ─────────────────────────────────────────────
def page_stairwell():
    st.title("🪜 Stairwell Settings")
    st.caption("Applicable to Apartment Buildings only — DTR Table 6.4 (Tau coefficient)")
    b  = st.session_state.building
    sw = b.setdefault("stairwell", default_stairwell())

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🏗️ Stairwell Configuration")
        sw["type"] = st.selectbox("Stairwell type",
                                   ["Open", "Closed", "Central", "Smoke vents"],
                                   index=["Open","Closed","Central","Smoke vents"].index(sw.get("type","Closed")))
        sw["stairwell_wall_ins"] = st.checkbox("Stairwell wall insulated", value=sw.get("stairwell_wall_ins", False))
        sw["apt_wall_ins"]       = st.checkbox("Apartment-facing wall insulated", value=sw.get("apt_wall_ins", False))

        tau = get_tau(sw["type"], sw["apt_wall_ins"])
        st.info(f"**Tau (τ) coefficient:** {tau}  \nHeat loss through stairwell walls is multiplied by τ.")

    with c2:
        st.subheader("💡 Stairwell Energy Loads")
        sw["lighting_bulbs"] = st.number_input("Lighting — bulb count",   1, 50, int(sw.get("lighting_bulbs",4)))
        sw["lighting_watts"] = st.number_input("Lighting — watts/bulb",   1, 500, int(sw.get("lighting_watts",20)))
        sw["lighting_hours"] = st.number_input("Lighting — hours/day",    0, 24, int(sw.get("lighting_hours",6)))
        sw["outlet_count"]   = st.number_input("Outlets — count",         0, 20, int(sw.get("outlet_count",2)))
        sw["outlet_watts"]   = st.number_input("Outlets — watts each",    0, 2000, int(sw.get("outlet_watts",50)))
        sw["outlet_hours"]   = st.number_input("Outlets — hours/day",     0, 24, int(sw.get("outlet_hours",2)))

        l_kwh, o_kwh = calculate_stairwell_electricity(sw)
        st.divider()
        m1, m2, m3 = st.columns(3)
        m1.metric("💡 Lighting kWh/yr", f"{l_kwh:,.1f}")
        m2.metric("🔌 Outlet kWh/yr",   f"{o_kwh:,.1f}")
        m3.metric("🏠 Total kWh/yr",    f"{l_kwh+o_kwh:,.1f}")

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
    kp   = f"{cf}_{ca}_{cr}"
    b    = st.session_state.building
    zone = b.get("dtr_zone", "B")
    alt  = b.get("altitude", 200)
    t_be = get_t_be(zone, alt)
    delta_t = T_BI - t_be
    hdg  = b.get("heating_days", DEFAULT_HEATING_DAYS).get(zone, 90)

    st.title("🏢 Building Energy Forecaster — DTR C 3-2")
    st.markdown(
        f"**Editing:** Floor {cf} › Apt {ca} (*{apt['name']}*) › Room {cr} (*{room['name']}*)  |  "
        f"**Zone {zone}** | Alt {alt}m | t_be = **{t_be}°C** | ΔT = **{delta_t}°C** | Heating days = **{hdg}**"
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
        tpl_sel = "— select —"
        if st.session_state.templates:
            tpl_sel = st.selectbox("", ["— select —"] + list(st.session_state.templates.keys()),
                                    key="tpl_sel", label_visibility="collapsed")
        else:
            st.caption("No templates yet.")
    with tb4:
        if tpl_sel != "— select —":
            if st.button("⬇️ Apply Template", use_container_width=True):
                room.update(copy.deepcopy(st.session_state.templates[tpl_sel]))
                st.success(f"Applied: **{tpl_sel}**"); st.rerun()

    st.divider()

    # Tabs
    tab_elec, tab_gas = st.tabs(["⚡ Electricity", "🔥 Gas"])

    # ════════════ ELECTRICITY TAB ════════════
    with tab_elec:
        room["name"] = st.text_input("Room name", value=room["name"], key=f"rname_{kp}")
        left, right = st.columns([1, 1], gap="large")

        with left:
            # Multi-layer wall builder
            render_wall_layer_builder(room, kp)

            st.subheader("🪟 Openings")
            oc1, oc2 = st.columns(2)
            with oc1:
                st.markdown("**Windows**")
                room["window_count"] = st.number_input("Window count", 0, 20,
                    int(room.get("window_count",2)), key=f"wc_{kp}")
                room["window_area_per_window_m2"] = st.number_input("Area each (m²)", 0.2, 10.0,
                    float(room.get("window_area_per_window_m2",1.5)), step=0.1, key=f"wa_{kp}")
                room["window_type"] = st.selectbox("Window type", list(WINDOW_TYPES.keys()),
                    index=list(WINDOW_TYPES.keys()).index(room.get("window_type","Single glazed — wood frame")),
                    key=f"wtype_{kp}")
                st.caption(f"U = {WINDOW_TYPES[room['window_type']]} W/m²·K")
            with oc2:
                st.markdown("**Doors**")
                room["door_count"] = st.number_input("Door count", 0, 10,
                    int(room.get("door_count",1)), key=f"dc_{kp}")
                room["door_area_per_door_m2"] = st.number_input("Area each (m²)", 0.5, 5.0,
                    float(room.get("door_area_per_door_m2",2.0)), step=0.1, key=f"da_{kp}")
                room["door_type"] = st.selectbox("Door type", list(DOOR_TYPES.keys()),
                    index=list(DOOR_TYPES.keys()).index(room.get("door_type","Wood — opaque")),
                    key=f"dtype_{kp}")
                st.caption(f"U = {DOOR_TYPES[room['door_type']]} W/m²·K")

            st.subheader("📐 Room Geometry")
            gc1, gc2 = st.columns(2)
            with gc1:
                room["area_m2"] = st.number_input("Floor area (m²)", 5.0, 200.0,
                    float(room.get("area_m2",20.0)), step=0.5, key=f"area_{kp}")
            with gc2:
                room["ceiling_height_m"] = st.number_input("Ceiling height (m)", 2.0, 5.0,
                    float(room.get("ceiling_height_m",2.5)), step=0.1, key=f"ch_{kp}")
            room["plug_count"] = st.number_input("Plug/outlet count", 0, 30,
                int(room.get("plug_count",4)), key=f"pc_{kp}")

            if b.get("building_type") == "Apartment Building":
                room["adjacent_stairwell"] = st.checkbox(
                    "This room has a wall adjacent to the stairwell",
                    value=room.get("adjacent_stairwell", False), key=f"adj_sw_{kp}")

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
            er = calculate_room_electricity(room, zone, alt, hdg)
            render_cost_block("Room Elec", er["total"], elec_cost, "⚡")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("🏠 Wall Loss",    f"{er['wall_loss']:,.0f} kWh")
            m2.metric("🪟 Window Loss",  f"{er['window_loss']:,.0f} kWh")
            m3.metric("🚪 Door Loss",    f"{er['door_loss']:,.0f} kWh")
            m4.metric("🔌 Plug Load",    f"{er['plug_load']:,.0f} kWh")
            st.caption(f"Wall U = {er['u_wall']:.3f} W/m²·K  |  ΔT = {er['delta_t']:.1f}°C")
            st.divider()
            apt_er = calculate_apartment_electricity(apt, zone, alt, hdg)
            st.markdown(f"**🚪 Apartment {ca} — Electricity Total**")
            render_cost_block("Apt Elec", apt_er["total"], elec_cost, "⚡")

    # ════════════ GAS TAB ════════════════════
    with tab_gas:
        st.subheader(f"🔥 Gas Settings — Apartment {ca}: {apt['name']}")
        st.caption(f"ΔT = 20 - {t_be} = {delta_t}°C  |  Zone {zone}  |  Gas climate factor = {GAS_CLIMATE_FACTOR.get(zone,1.0)}")

        g1, g2 = st.columns(2)
        with g1:
            apt["occupants"]  = st.number_input("Number of occupants", 1, 20, int(apt.get("occupants",4)),
                                                  help="Hot water: occupants × 1,095 kWh/yr", key=f"occ_{cf}_{ca}")
            apt["cooking_kwh"] = st.number_input("Cooking gas (kWh/year)", 0.0, 10000.0,
                                                   float(apt.get("cooking_kwh",1825.0)), step=50.0,
                                                   help="Default 1,825 kWh/yr for family of 4", key=f"cook_{cf}_{ca}")
        with g2:
            st.info(
                f"**Gas heating uses DTR ΔT:**\n"
                f"- t_bi = 20°C (residential)\n"
                f"- t_be = {t_be}°C (Zone {zone}, alt {alt}m)\n"
                f"- ΔT = {delta_t}°C\n"
                f"- Heater efficiency = {HEATER_EFF*100:.0f}%\n"
                f"- Gas climate factor = {GAS_CLIMATE_FACTOR.get(zone,1.0)}"
            )

        st.subheader("🔥 Gas Appliances")
        render_gas_appliance_table(apt, f"{cf}_{ca}")

        st.divider()
        st.subheader("📊 Quick Results — Gas")
        gr = calculate_apartment_gas(apt, zone, alt)
        render_cost_block("Apt Gas Total", gr["total"], gas_cost, "🔥")
        gm1, gm2, gm3, gm4 = st.columns(4)
        gm1.metric("🌡️ Heating",   f"{gr['heating']:,.0f} kWh/yr")
        gm2.metric("🚿 Hot Water", f"{gr['hot_water']:,.0f} kWh/yr")
        gm3.metric("🍳 Cooking",   f"{gr['cooking']:,.0f} kWh/yr")
        gm4.metric("🔥 Gas Appls", f"{gr['gas_appliances']:,.0f} kWh/yr")

        st.divider()
        st.subheader("⚡🔥 Combined — This Apartment")
        apt_er2 = calculate_apartment_electricity(apt, zone, alt, hdg)
        combined = apt_er2["total"] + gr["total"]
        elec_pct = apt_er2["total"] / combined * 100 if combined > 0 else 0
        gas_pct  = gr["total"]      / combined * 100 if combined > 0 else 0
        cm1, cm2, cm3 = st.columns(3)
        cm1.metric("⚡ Electricity", f"{apt_er2['total']:,.0f} kWh/yr ({elec_pct:.1f}%)")
        cm2.metric("🔥 Gas",         f"{gr['total']:,.0f} kWh/yr ({gas_pct:.1f}%)")
        cm3.metric("🏠 Combined",    f"{combined:,.0f} kWh/yr")

# ─────────────────────────────────────────────
# PAGE: BUILDING SETTINGS
# ─────────────────────────────────────────────
def page_building_settings():
    st.title("⚙️ Building Settings")
    b = st.session_state.building

    tab_general, tab_dtr, tab_cost, tab_compliance = st.tabs([
        "🏗️ General", "🌍 DTR Climate", "💵 Costs", "✅ Regulatory"
    ])

    with tab_general:
        st.subheader("Building Information")
        b["name"] = st.text_input("Building name", value=b["name"])
        b["building_type"] = st.selectbox(
            "Building type", ["Apartment Building", "Single-Family House"],
            index=["Apartment Building","Single-Family House"].index(b.get("building_type","Apartment Building")))
        st.info("**Apartment Building** → includes stairwell node with Tau coefficient\n\n"
                "**Single-Family House** → no stairwell, no Tau")

        st.divider()
        st.subheader("📋 Copy Floor")
        fnums = sorted(st.session_state.floors.keys())
        if len(fnums) >= 2:
            cf1, cf2, cf3 = st.columns(3)
            with cf1: src = st.selectbox("Source floor", fnums, key="src_fl")
            with cf2: tgt = st.selectbox("Target floor", [f for f in fnums if f!=src], key="tgt_fl")
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
                st.write(f"  Floor **{fn}** › Apt **{ak}** ({av['name']}, {av.get('occupants',4)} occ.) — {len(rooms)} room(s): {room_list}")

    with tab_dtr:
        st.subheader("🌍 DTR Climate Zone")
        b["dtr_zone"] = st.selectbox("DTR Zone", list(DTR_ZONES.keys()),
            index=list(DTR_ZONES.keys()).index(b.get("dtr_zone","B")))
        b["altitude"] = st.slider("Altitude (m)", 0, 1500, int(b.get("altitude",200)), step=50)

        zone    = b["dtr_zone"]
        alt     = b["altitude"]
        t_be    = get_t_be(zone, alt)
        delta_t = T_BI - t_be
        hdg_def = DEFAULT_HEATING_DAYS.get(zone, 90)

        col1, col2, col3 = st.columns(3)
        col1.metric("Winter t_be", f"{t_be}°C")
        col2.metric("ΔT (t_bi - t_be)", f"{delta_t}°C")
        col3.metric("Default Heating Days", f"{hdg_def} days")

        st.subheader("📅 Heating Days (adjustable)")
        st.caption("Default values from DTR. Adjust if you have local data.")
        hdg_dict = b.setdefault("heating_days", dict(DEFAULT_HEATING_DAYS))
        hd1, hd2, hd3 = st.columns(3)
        hdg_dict["A"]  = hd1.number_input("Zone A",  1, 365, int(hdg_dict.get("A",60)),  key="hd_A")
        hdg_dict["B"]  = hd1.number_input("Zone B",  1, 365, int(hdg_dict.get("B",90)),  key="hd_B")
        hdg_dict["B'"] = hd2.number_input("Zone B'", 1, 365, int(hdg_dict.get("B'",100)),key="hd_Bp")
        hdg_dict["C"]  = hd2.number_input("Zone C",  1, 365, int(hdg_dict.get("C",120)), key="hd_C")
        hdg_dict["D"]  = hd3.number_input("Zone D",  1, 365, int(hdg_dict.get("D",150)), key="hd_D")
        hdg_dict["D'"] = hd3.number_input("Zone D'", 1, 365, int(hdg_dict.get("D'",180)),key="hd_Dp")

        st.subheader("📋 DTR Zone Reference")
        zone_data = []
        for z, entries in DTR_ZONES.items():
            for i, (max_alt, tbe) in enumerate(entries):
                prev_alt = entries[i-1][0] if i>0 else 0
                alt_range = f"< {max_alt}m" if i==0 else f"{prev_alt}–{max_alt}m" if max_alt<9999 else f"≥ {prev_alt}m"
                zone_data.append({"Zone": z, "Altitude": alt_range, "t_be (°C)": tbe,
                                   "Heating Days": DEFAULT_HEATING_DAYS.get(z,"—")})
        st.dataframe(pd.DataFrame(zone_data), use_container_width=True, hide_index=True)

    with tab_cost:
        st.subheader("💵 Energy Prices & Currency")
        b["electricity_price_usd"] = st.number_input(
            "Electricity price (USD/kWh)", 0.001, 2.0,
            float(b.get("electricity_price_usd",0.12)), step=0.001, format="%.3f")
        b["gas_price_usd"] = st.number_input(
            "Gas price (USD/kWh)", 0.001, 1.0,
            float(b.get("gas_price_usd",0.02)), step=0.001, format="%.3f",
            help="Default $0.02/kWh — Algeria subsidized rate")
        b["exchange_rate"] = st.number_input(
            "Exchange rate (1 USD = ? DZD)", 1.0, 10000.0,
            float(b.get("exchange_rate",135.0)), step=1.0)
        dzd_e = b["electricity_price_usd"] * b["exchange_rate"]
        dzd_g = b["gas_price_usd"]         * b["exchange_rate"]
        st.caption(f"Electricity: ≈ {dzd_e:.2f} DZD/kWh  |  Gas: ≈ {dzd_g:.2f} DZD/kWh")
        st.session_state.show_cost = st.checkbox("Show cost estimates in results",
                                                   value=st.session_state.show_cost)

    with tab_compliance:
        st.subheader("✅ Regulatory Compliance — DTR Table 2.1")
        st.caption("Enter total building surface areas for the D_ref calculation.")

        zone  = b.get("dtr_zone","B")
        btype = "Collective" if b.get("building_type") == "Apartment Building" else "Individual"
        coeffs = DREF_COEFFS.get(zone, {}).get(btype, (1.1,2.4,1.2,3.5,4.5))
        a, cc, cv, d, e = coeffs
        st.info(f"**Zone {zone} — {btype}**  |  D_ref = {a}×S₁ + {cc}×S₂ + {cv}×S₃ + {d}×S₄ + {e}×S₅")

        rc1, rc2 = st.columns(2)
        with rc1:
            b["s1_roof"]      = st.number_input("S₁ — Roof / ceiling area (m²)", 0.0, 10000.0, float(b.get("s1_roof",120.0)), step=1.0)
            b["s2_ground"]    = st.number_input("S₂ — Ground floor area (m²)",   0.0, 10000.0, float(b.get("s2_ground",120.0)), step=1.0)
            b["s3_ext_walls"] = st.number_input("S₃ — External wall area (m²)",  0.0, 10000.0, float(b.get("s3_ext_walls",200.0)), step=1.0)
        with rc2:
            b["s4_doors"]   = st.number_input("S₄ — Door area (m²)",   0.0, 1000.0, float(b.get("s4_doors",10.0)), step=0.5)
            b["s5_windows"] = st.number_input("S₅ — Window area (m²)", 0.0, 1000.0, float(b.get("s5_windows",30.0)), step=0.5)
            b["d_t_actual"] = st.number_input("D_t — Actual heat loss coefficient (W/°C)",
                                               0.0, 100000.0, float(b.get("d_t_actual",0.0)), step=1.0,
                                               help="Total measured/calculated transmission heat loss. Enter 0 to auto-estimate.")

        d_ref, d_max, passed = check_compliance(b, b.get("d_t_actual",0.0))
        st.divider()
        rr1, rr2, rr3 = st.columns(3)
        rr1.metric("D_ref (W/°C)",     f"{d_ref:,.1f}")
        rr2.metric("D_t max (1.05 × D_ref)", f"{d_max:,.1f}")
        rr3.metric("D_t actual",        f"{b.get('d_t_actual',0.0):,.1f}")
        if b.get("d_t_actual",0.0) > 0:
            if passed:
                st.success(f"✅ **PASS** — D_t ({b.get('d_t_actual',0.0):,.1f}) ≤ 1.05 × D_ref ({d_max:,.1f})")
            else:
                st.error(f"❌ **FAIL** — D_t ({b.get('d_t_actual',0.0):,.1f}) > 1.05 × D_ref ({d_max:,.1f}). Improve insulation!")
        else:
            st.info("Enter D_t actual above to run the compliance check.")

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
                layers  = tpl_data.get("wall_layers", [])
                r_total, u_val = calc_wall_u(layers)
                st.write(f"**Wall layers:** {len(layers)}  |  U = {u_val:.3f} W/m²·K  |  "
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
    zone    = b.get("dtr_zone","B")
    alt     = b.get("altitude",200)
    t_be    = get_t_be(zone, alt)
    results = calculate_building_total(st.session_state.floors, b)

    elec_kwh  = results["building_elec"]
    gas_kwh   = results["building_gas"]
    sw_kwh    = results["stairwell_kwh"]
    total_kwh = results["total_energy"]
    eui_e     = results["eui_elec"]
    eui_t     = results["eui_total"]
    bench     = get_eui_benchmark(eui_e)
    elec_pct  = elec_kwh / total_kwh * 100 if total_kwh > 0 else 0
    gas_pct   = gas_kwh  / total_kwh * 100 if total_kwh > 0 else 0

    # KPIs
    st.subheader("🏢 Building Summary")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("⚡ Electricity",  f"{elec_kwh:,.0f} kWh/yr")
    k2.metric("🔥 Gas",          f"{gas_kwh:,.0f} kWh/yr")
    k3.metric("🏠 Total Energy", f"{total_kwh:,.0f} kWh/yr")
    k4.metric("📐 EUI (Elec)",   f"{eui_e:,.1f} kWh/m²/yr")
    k5.metric("🌍 DTR Zone",     f"{zone} | t_be={t_be}°C")
    st.info(f"🔍 EUI Benchmark: **{bench}**  |  Total EUI: **{eui_t:,.1f} kWh/m²/yr**")
    if sw_kwh > 0:
        st.caption(f"🪜 Stairwell electricity: {sw_kwh:,.1f} kWh/yr included in building total")

    # Costs
    eu, ed = elec_cost(elec_kwh); gu, gd = gas_cost(gas_kwh)
    tu, td = eu + gu, ed + gd
    bc1, bc2, bc3 = st.columns(3)
    bc1.metric("⚡ Electricity Cost/yr", fmt_usd_dzd(eu, ed))
    bc2.metric("🔥 Gas Cost/yr",        fmt_usd_dzd(gu, gd))
    bc3.metric("🏠 Total Cost/yr",      fmt_usd_dzd(tu, td))
    bh1, bh2, bh3 = st.columns(3)
    bh1.metric("⚡ Elec Avg/hr",  fmt_hr(eu, ed))
    bh2.metric("🔥 Gas Avg/hr",   fmt_hr(gu, gd))
    bh3.metric("🏠 Total Avg/hr", fmt_hr(tu, td))

    st.divider()

    # Regulatory compliance summary
    st.subheader("✅ Regulatory Compliance (DTR Table 2.1)")
    d_ref, d_max, passed = check_compliance(b, b.get("d_t_actual",0.0))
    comp1, comp2, comp3 = st.columns(3)
    comp1.metric("D_ref (W/°C)", f"{d_ref:,.1f}")
    comp2.metric("D_t max",      f"{d_max:,.1f}")
    comp3.metric("D_t actual",   f"{b.get('d_t_actual',0.0):,.1f}")
    if b.get("d_t_actual",0.0) > 0:
        if passed:
            st.success(f"✅ PASS — Building meets DTR C 3-2 thermal requirements")
        else:
            st.error(f"❌ FAIL — D_t exceeds 1.05 × D_ref. See Building Settings → Regulatory tab.")
    else:
        st.info("Enter D_t actual in Building Settings → Regulatory tab to run compliance check.")

    st.divider()

    # Floor chart
    st.subheader("📊 Energy by Floor")
    fdf = pd.DataFrame([
        {"Floor": f"Floor {k}", "Electricity (kWh)": round(results["floor_elec"][k],1),
         "Gas (kWh)": round(results["floor_gas"][k],1)}
        for k in sorted(results["floor_elec"].keys())
    ])
    st.bar_chart(fdf.set_index("Floor"))

    # Apartment table
    st.subheader("🚪 Energy by Apartment")
    apt_rows = []
    for (fn, ak), ar in sorted(results["apt_results"].items()):
        apt_obj = st.session_state.floors[fn]["apartments"][ak]
        ek = ar["elec"]["total"]; gk = ar["gas"]["total"]; tk = ek + gk
        eu2, ed2 = elec_cost(ek); gu2, gd2 = gas_cost(gk); tcu, tcd = eu2+gu2, ed2+gd2
        apt_rows.append({
            "Floor": fn, "Apt": ak, "Name": apt_obj["name"],
            "Rooms": len(apt_obj["rooms"]), "Occupants": apt_obj.get("occupants",4),
            "Elec (kWh/yr)": round(ek,1), "Gas (kWh/yr)": round(gk,1),
            "Total (kWh/yr)": round(tk,1),
            "Elec %": round(ek/tk*100,1) if tk>0 else 0,
            "Gas %": round(gk/tk*100,1) if tk>0 else 0,
            "Elec Cost/yr (USD)": round(eu2,2), "Gas Cost/yr (USD)": round(gu2,2),
            "Total Cost/yr (USD)": round(tcu,2), "Total Cost/yr (DZD)": round(tcd,0),
            "Avg Cost/hr (USD)": round(tcu/HOURS_IN_YEAR,5),
        })
    st.dataframe(pd.DataFrame(apt_rows), use_container_width=True, hide_index=True)

    # Room table
    st.subheader("🏠 Electricity by Room")
    room_rows = []
    for (fn, ak), ar in sorted(results["apt_results"].items()):
        apt_obj = st.session_state.floors[fn]["apartments"][ak]
        for rk, rr in sorted(ar["elec"]["room_results"].items()):
            rname = apt_obj["rooms"][rk]["name"]
            eu3, ed3 = elec_cost(rr["total"])
            room_rows.append({
                "Floor": fn, "Apt": ak, "Room": rk, "Name": rname,
                "Area (m²)": round(rr["area_m2"],1),
                "Wall U (W/m²K)": round(rr["u_wall"],3),
                "Wall Loss (kWh)": round(rr["wall_loss"],1),
                "Window Loss (kWh)": round(rr["window_loss"],1),
                "Door Loss (kWh)": round(rr["door_loss"],1),
                "Appliances (kWh)": round(rr["appliance_total"],1),
                "Plug Load (kWh)": round(rr["plug_load"],1),
                "Total (kWh/yr)": round(rr["total"],1),
                "Cost/yr (USD)": round(eu3,2), "Cost/yr (DZD)": round(ed3,0),
                "Avg Cost/hr (USD)": round(eu3/HOURS_IN_YEAR,5),
            })
    st.dataframe(pd.DataFrame(room_rows), use_container_width=True, hide_index=True)

    # Gas breakdown
    st.subheader("🔥 Gas Breakdown by Apartment")
    gas_rows = []
    for (fn, ak), ar in sorted(results["apt_results"].items()):
        apt_obj = st.session_state.floors[fn]["apartments"][ak]
        gr2 = ar["gas"]; gu3, gd3 = gas_cost(gr2["total"])
        gas_rows.append({
            "Floor": fn, "Apt": ak, "Name": apt_obj["name"],
            "Heating (kWh)": round(gr2["heating"],1),
            "Hot Water (kWh)": round(gr2["hot_water"],1),
            "Cooking (kWh)": round(gr2["cooking"],1),
            "Gas Appliances (kWh)": round(gr2["gas_appliances"],1),
            "Total Gas (kWh/yr)": round(gr2["total"],1),
            "Gas Cost/yr (USD)": round(gu3,2), "Gas Cost/yr (DZD)": round(gd3,0),
            "Avg Gas Cost/hr (USD)": round(gu3/HOURS_IN_YEAR,5),
        })
    st.dataframe(pd.DataFrame(gas_rows), use_container_width=True, hide_index=True)

    # Top 5 appliances
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
            file_name=f"{st.session_state.building['name'].replace(' ','_')}.json",
            mime="application/json")
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
🏢 Building  (type: Apartment / Single-Family)
├── 🪜 Stairwell  (Apartment buildings only — Tau coefficient DTR Table 6.4)
└── 📐 Floor 1
    └── 🚪 Apartment A  (occupants, cooking gas, gas appliances)
        ├── ▶ 🏠 Room 1: Living Room  (walls, windows, doors, electric appliances)
        └──   🏠 Room 2: Bedroom
```

---

## Multi-Layer Wall Builder (DTR Annex 2)
For each room, build the wall layer by layer:
1. Select material from the DTR Annex 2 list
2. Enter thickness in cm
3. App calculates R_layer = thickness(m) ÷ λ
4. Total: R_total = R_si(0.13) + ΣR_layers + R_se(0.04)
5. U-value = 1 ÷ R_total (W/m²·K)

**Air gap** has a fixed R = 0.16 m²·K/W (no thickness needed)

---

## DTR Climate Zones (Algerian Official)
| Zone | Description | Heating Days |
|------|-------------|-------------|
| A | Coastal / mild | 60 |
| B | Inland / moderate | 90 |
| B' | Inland / slightly cold | 100 |
| C | Cold / mountainous | 120 |
| D | Hot desert | 150 |
| D' | Very hot desert | 180 |

Winter design temperature t_be depends on zone + altitude (slider in Building Settings).

---

## Electricity Formulas (DTR-based)
```
ΔT = t_bi(20°C) − t_be (from DTR zone + altitude)

Wall loss (kWh/yr)   = Wall area × U_wall × ΔT × 24 × heating_days / 1000
Window loss (kWh/yr) = Window area × U_win × ΔT × 24 × heating_days / 1000
Door loss (kWh/yr)   = Door area × U_door × ΔT × 24 × heating_days / 1000
Appliance (kWh/yr)   = Watts × hrs/day × days/yr / 1000
Plug load (kWh/yr)   = plug_count × 50W × 8h × 365 / 1000

Room total = wall + window + door + appliances + plug_load
Apartment  = Σ rooms
Building   = Σ apartments + stairwell
```

---

## Gas Formulas (DTR ΔT)
```
Heating gas = (wall_UA + window_UA + door_UA + roof_UA)
              × degree_days(1136) × 24 / 1000
              × gas_climate_factor / heater_efficiency(0.65)

Hot water   = occupants × 1,095 kWh/yr
Cooking     = user input (default 1,825 kWh/yr)
Gas appls   = kWh/hr × hrs/day × 365
```

---

## Stairwell (Apartment Buildings only — DTR Table 6.4)
Tau (τ) reduces heat loss through walls adjacent to stairwell:
| Type | Apt wall insulated | τ |
|------|--------------------|---|
| Open | No | 0.55 |
| Open | Yes | 0.30 |
| Closed | No | 0.40 |
| Closed | Yes | 0.20 |
| Central | No | 0.25 |
| Central | Yes | 0.10 |
| Smoke vents | Any | 0.90 |

---

## Regulatory Compliance (DTR Table 2.1)
D_ref = a×S₁ + b×S₂ + c×S₃ + d×S₄ + e×S₅
- S₁ = Roof area (m²)
- S₂ = Ground floor area (m²)
- S₃ = External wall area (m²)
- S₄ = Door area (m²)
- S₅ = Window area (m²)

**Check: D_t ≤ 1.05 × D_ref → PASS**

Enter surfaces + D_t actual in Building Settings → Regulatory tab.

---

## Window Types (DTR Table 3.2)
| Type | U (W/m²·K) |
|------|-----------|
| Single glazed — wood | 5.0 |
| Single glazed — metal | 5.8 |
| Double glazed 5-7mm — wood | 3.3 |
| Double glazed 5-7mm — metal | 4.0 |
| Double glazed 12-13mm — wood | 2.9 |
| Double glazed 12-13mm — metal | 3.7 |

## Door Types (DTR Table 3.3)
| Type | U (W/m²·K) |
|------|-----------|
| Wood opaque | 3.5 |
| Wood <30% glass | 4.0 |
| Wood 30-60% glass | 4.5 |
| Metal opaque | 5.8 |
| Metal with glass | 5.8 |

---

## Cost Display
Every result shows 3 values: **kWh/year**, **Cost/year (USD + DZD)**, **Avg cost/hour**
Default prices: Electricity $0.12/kWh | Gas $0.02/kWh (Algeria subsidized)
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
    elif pg == "Stairwell":           page_stairwell()
    elif pg == "Help":                page_help()

if __name__ == "__main__":
    main()
