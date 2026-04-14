"""
Building Energy Forecaster
A Streamlit app to forecast electricity consumption for multi-floor, multi-apartment buildings.
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
    initial_sidebar_state="expanded"
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

DB_PATH = "building_energy.db"

# ─────────────────────────────────────────────
# DATABASE FUNCTIONS
# ─────────────────────────────────────────────
def init_db():
    """Initialize SQLite database and create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            created_date  TEXT    NOT NULL,
            building_json TEXT    NOT NULL,
            floors_json   TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_project_to_db(project_name, building_data, floors_data):
    """Save current project to SQLite."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO projects (name, created_date, building_json, floors_json) VALUES (?, ?, ?, ?)",
        (
            project_name,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            json.dumps(building_data),
            json.dumps(floors_data),
        )
    )
    conn.commit()
    conn.close()

def load_all_projects():
    """Return list of all saved projects as (id, name, date)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, created_date FROM projects ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def load_project_from_db(project_id):
    """Load a project from DB and restore session state."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT building_json, floors_json FROM projects WHERE id = ?", (project_id,))
    row = c.fetchone()
    conn.close()
    if row:
        st.session_state.building = json.loads(row[0])
        raw_floors = json.loads(row[1])
        # JSON keys are strings; convert back to ints
        st.session_state.floors = {int(k): v for k, v in raw_floors.items()}
        return True
    return False

def delete_project_from_db(project_id):
    """Delete a saved project by ID."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────
# DEFAULT DATA FACTORIES
# ─────────────────────────────────────────────
def default_apartment(name="Apartment A"):
    return {
        "name": name,
        "area_m2": 65.0,
        "wall_thickness_cm": 25,
        "wall_material": "Brick",
        "window_count": 4,
        "window_area_per_window_m2": 1.5,
        "window_type": "Single",
        "plug_count": 10,
        "ceiling_height_m": 2.5,
        "appliances": [],
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
    """Return the next available single-letter apartment key."""
    used = set(existing_keys)
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ch not in used:
            return ch
    return str(len(existing_keys) + 1)

def next_floor_num(floors_dict):
    """Return the next floor number (max existing + 1)."""
    return max(floors_dict.keys()) + 1 if floors_dict else 1

# ─────────────────────────────────────────────
# SESSION STATE INITIALIZER
# ─────────────────────────────────────────────
def init_session_state():
    if "building" not in st.session_state:
        st.session_state.building = {
            "name": "My Building",
            "num_floors": 3,
            "climate_zone": "Coast",
            "location_type": "Urban",
            "electricity_price_usd": 0.12,
            "exchange_rate": 135.0,
            "created_date": datetime.now().strftime("%Y-%m-%d"),
        }
    if "floors" not in st.session_state:
        st.session_state.floors = default_floors(3)
    if "templates" not in st.session_state:
        st.session_state.templates = {}
    if "current_floor" not in st.session_state:
        st.session_state.current_floor = 1
    if "current_apartment" not in st.session_state:
        st.session_state.current_apartment = "A"
    if "page" not in st.session_state:
        st.session_state.page = "Dashboard"
    if "show_cost" not in st.session_state:
        st.session_state.show_cost = True

# ─────────────────────────────────────────────
# CALCULATION FUNCTIONS
# ─────────────────────────────────────────────
def calculate_apartment_electricity(apt, climate_zone, location_type):
    """Returns dict with kWh/year breakdown for one apartment."""
    area             = apt.get("area_m2", 65.0)
    wall_u           = WALL_U_VALUES.get(apt.get("wall_material", "Brick"), 1.84)
    window_count     = apt.get("window_count", 4)
    window_area_each = apt.get("window_area_per_window_m2", 1.5)
    window_u         = WINDOW_U_VALUES.get(apt.get("window_type", "Single"), 5.82)
    window_area_total = window_count * window_area_each
    ceiling_h        = apt.get("ceiling_height_m", 2.5)
    plug_count       = apt.get("plug_count", 10)

    # Approximate wall area assuming square floor plan
    side       = math.sqrt(area)
    gross_wall = 4 * side * ceiling_h
    wall_area  = max(gross_wall - window_area_total, 0)

    delta_t      = 15   # assumed ΔT (°C) for cooling season
    cooling_days = 120
    cooling_hrs  = 8

    # Step 1 — Wall cooling load (kWh/year)
    wall_load = wall_area * wall_u * delta_t * cooling_hrs * cooling_days / 1000

    # Step 2 — Window cooling load (kWh/year)
    window_load = window_area_total * window_u * delta_t * cooling_hrs * cooling_days / 1000

    # Step 3 — Appliance energy (kWh/year)
    appliance_total     = 0.0
    appliance_breakdown = []
    for appl in apt.get("appliances", []):
        w         = appl.get("watts", 0)
        h         = appl.get("hours", 0)
        n         = appl.get("name", "Unknown")
        name_low  = n.lower()
        if "ac" in name_low or "air con" in name_low:
            days = 120
        elif "fridge" in name_low or "refrigerator" in name_low:
            days = 365
        else:
            days = 365
        kwh = (w * h * days) / 1000
        appliance_total += kwh
        appliance_breakdown.append({"name": n, "kwh": kwh, "watts": w, "hours": h})

    # Step 4 — Plug load (kWh/year)
    plug_load = plug_count * 50 * 8 * 365 / 1000

    # Step 5 — Climate & location multipliers
    climate_mult = CLIMATE_ZONES.get(climate_zone, {}).get("cooling", 1.0)
    loc_mult     = LOCATION_MULTIPLIERS.get(location_type, 1.0)
    multiplier   = climate_mult * loc_mult

    total = (wall_load + window_load + appliance_total + plug_load) * multiplier

    return {
        "wall_load":           wall_load   * multiplier,
        "window_load":         window_load * multiplier,
        "appliance_total":     appliance_total * multiplier,
        "plug_load":           plug_load   * multiplier,
        "total":               total,
        "appliance_breakdown": appliance_breakdown,
        "area_m2":             area,
    }

def calculate_building_total(floors_data, climate_zone, location_type):
    """Returns full building energy summary."""
    building_total = 0.0
    total_area     = 0.0
    floor_totals   = {}
    apt_results    = {}

    for floor_num, floor_data in floors_data.items():
        floor_total = 0.0
        for apt_key, apt_data in floor_data.get("apartments", {}).items():
            res = calculate_apartment_electricity(apt_data, climate_zone, location_type)
            floor_total    += res["total"]
            building_total += res["total"]
            total_area     += res["area_m2"]
            apt_results[(floor_num, apt_key)] = res
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
# JSON EXPORT / IMPORT
# ─────────────────────────────────────────────
def export_to_json():
    data = {
        "building":  st.session_state.building,
        "floors":    {str(k): v for k, v in st.session_state.floors.items()},
        "templates": st.session_state.templates,
    }
    return json.dumps(data, indent=2)

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
# SAMPLE DATA LOADER
# ─────────────────────────────────────────────
def load_example_building():
    st.session_state.building = {
        "name": "Example Building",
        "num_floors": 3,
        "climate_zone": "Coast",
        "location_type": "Urban",
        "electricity_price_usd": 0.12,
        "exchange_rate": 135.0,
        "created_date": datetime.now().strftime("%Y-%m-%d"),
    }
    sample_appliances = [
        {"name": "Refrigerator", "watts": 150,  "hours": 24},
        {"name": "AC (split)",   "watts": 800,  "hours": 8},
        {"name": "TV (LED)",     "watts": 100,  "hours": 5},
    ]
    def make_apt(name, area):
        apt = default_apartment(name)
        apt["area_m2"]    = area
        apt["appliances"] = [dict(a) for a in sample_appliances]
        return apt
    st.session_state.floors = {
        1: {"apartments": {"A": make_apt("Apartment 1A", 65), "B": make_apt("Apartment 1B", 50)}},
        2: {"apartments": {"A": make_apt("Apartment 2A", 65), "B": make_apt("Apartment 2B", 50)}},
        3: {"apartments": {"A": make_apt("Apartment 3A", 65)}},
    }
    st.session_state.current_floor     = 1
    st.session_state.current_apartment = "A"

# ─────────────────────────────────────────────
# CURRENCY HELPER
# ─────────────────────────────────────────────
def fmt_currency(kwh):
    price_usd = st.session_state.building.get("electricity_price_usd", 0.12)
    rate      = st.session_state.building.get("exchange_rate", 135.0)
    usd = kwh * price_usd
    dzd = usd * rate
    return f"${usd:,.2f} USD  /  {dzd:,.0f} DZD"

# ─────────────────────────────────────────────
# SIDEBAR — HIERARCHICAL TREE NAVIGATION
# ─────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:

        # ── App header ───────────────────────
        st.markdown("## 🏢 Energy Forecaster")
        st.caption("Master Year Project")
        st.divider()

        # ── Top-level page navigation ────────
        nav_pages = [
            ("📈", "Results & Forecast"),
            ("⚙️", "Building Settings"),
            ("📁", "Apartment Templates"),
            ("💾", "Save/Load Project"),
            ("❓", "Help"),
        ]
        for icon, pg in nav_pages:
            is_active = st.session_state.page == pg
            if st.button(
                f"{icon} {pg}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
                key=f"nav_{pg}"
            ):
                st.session_state.page = pg
                st.rerun()

        st.divider()

        # ── Building label (click → settings) ─
        b = st.session_state.building
        if st.button(
            f"🏢  {b['name']}",
            use_container_width=True,
            key="nav_building_label"
        ):
            st.session_state.page = "Building Settings"
            st.rerun()
        st.caption(f"Zone: {b['climate_zone']}  |  {b['location_type']}")

        # ── Floor / Apartment tree ───────────
        floors     = st.session_state.floors
        floor_nums = sorted(floors.keys())

        for floor_num in floor_nums:
            apts           = floors[floor_num]["apartments"]
            apt_keys       = sorted(apts.keys())
            is_active_floor = (floor_num == st.session_state.current_floor)

            expander_label = (
                f"📐 Floor {floor_num}  "
                f"({'  '.join(apt_keys)})"
            )

            # Keep the active floor's expander open by default
            with st.expander(expander_label, expanded=is_active_floor):

                # Each apartment as a selectable button
                for apt_key in apt_keys:
                    apt_name    = apts[apt_key]["name"]
                    is_selected = (
                        floor_num == st.session_state.current_floor
                        and apt_key == st.session_state.current_apartment
                    )
                    prefix   = "▶ " if is_selected else "     "
                    btn_type = "primary" if is_selected else "secondary"

                    if st.button(
                        f"{prefix}🚪 {apt_key}: {apt_name}",
                        key=f"tree_apt_{floor_num}_{apt_key}",
                        use_container_width=True,
                        type=btn_type,
                    ):
                        st.session_state.current_floor     = floor_num
                        st.session_state.current_apartment = apt_key
                        st.session_state.page              = "Dashboard"
                        st.rerun()

                st.markdown("---")

                # Per-floor action row: Add Apt | Copy Floor | Delete Floor
                fc1, fc2, fc3 = st.columns(3)

                with fc1:
                    if st.button(
                        "➕ Apt",
                        key=f"tree_add_apt_{floor_num}",
                        help=f"Add apartment to Floor {floor_num}",
                        use_container_width=True,
                    ):
                        new_key = next_apt_key(list(apts.keys()))
                        floors[floor_num]["apartments"][new_key] = default_apartment(
                            f"Apartment {new_key}"
                        )
                        st.session_state.current_floor     = floor_num
                        st.session_state.current_apartment = new_key
                        st.session_state.page              = "Dashboard"
                        st.rerun()

                with fc2:
                    if st.button(
                        "📋 Copy",
                        key=f"tree_copy_floor_{floor_num}",
                        help=f"Duplicate Floor {floor_num}",
                        use_container_width=True,
                    ):
                        new_f = next_floor_num(floors)
                        floors[new_f] = copy.deepcopy(floors[floor_num])
                        st.session_state.building["num_floors"] = len(floors)
                        st.session_state.current_floor          = new_f
                        st.session_state.current_apartment      = sorted(
                            floors[new_f]["apartments"].keys()
                        )[0]
                        st.session_state.page = "Dashboard"
                        st.rerun()

                with fc3:
                    # Only allow deletion if more than one floor exists
                    if len(floor_nums) > 1:
                        if st.button(
                            "🗑️ Del",
                            key=f"tree_del_floor_{floor_num}",
                            help=f"Delete Floor {floor_num}",
                            use_container_width=True,
                        ):
                            del floors[floor_num]
                            st.session_state.building["num_floors"] = len(floors)
                            remaining = sorted(floors.keys())
                            st.session_state.current_floor = remaining[0]
                            st.session_state.current_apartment = sorted(
                                floors[remaining[0]]["apartments"].keys()
                            )[0]
                            st.rerun()

        st.divider()

        # ── Add Floor button at bottom of tree ─
        if st.button("➕ Add Floor", use_container_width=True, key="tree_add_floor"):
            new_f = next_floor_num(floors)
            floors[new_f] = {"apartments": {"A": default_apartment("Apartment A")}}
            st.session_state.building["num_floors"] = len(floors)
            st.session_state.current_floor          = new_f
            st.session_state.current_apartment      = "A"
            st.session_state.page                   = "Dashboard"
            st.rerun()

        st.divider()

        # ── Utility buttons ──────────────────
        if st.button("🏗️ Load Example Building", use_container_width=True):
            load_example_building()
            st.session_state.page = "Dashboard"
            st.rerun()

        if st.button("🔄 New / Reset Project", use_container_width=True):
            for key in ["building", "floors", "templates", "current_floor", "current_apartment"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

# ─────────────────────────────────────────────
# PAGE: DASHBOARD — Apartment Property Panel
# ─────────────────────────────────────────────
def page_dashboard():
    floors = st.session_state.floors
    cf     = st.session_state.current_floor
    ca     = st.session_state.current_apartment

    # Guard: ensure selection is still valid after deletions
    if cf not in floors:
        cf = sorted(floors.keys())[0]
        st.session_state.current_floor = cf
    if ca not in floors[cf]["apartments"]:
        ca = sorted(floors[cf]["apartments"].keys())[0]
        st.session_state.current_apartment = ca

    apt = floors[cf]["apartments"][ca]

    # ── Page header ──────────────────────────
    st.title("🏢 Building Energy Forecaster")
    st.markdown(
        f"**Editing:** Floor {cf}  ›  Apartment {ca}  —  *{apt['name']}*  |  "
        f"Climate: **{st.session_state.building['climate_zone']}**  |  "
        f"Location: **{st.session_state.building['location_type']}**"
    )
    st.divider()

    # ── Template toolbar ─────────────────────
    tb1, tb2, tb3, tb4 = st.columns([2, 2, 2, 2])
    with tb1:
        tpl_save_name = st.text_input(
            "Template name", value=f"Floor{cf}_{ca}",
            placeholder="Template name…",
            label_visibility="collapsed",
            key="tpl_save_name_input"
        )
    with tb2:
        if st.button("💾 Save as Template", use_container_width=True):
            if tpl_save_name.strip():
                st.session_state.templates[tpl_save_name] = copy.deepcopy(apt)
                st.success(f"Saved template: **{tpl_save_name}**")
            else:
                st.error("Enter a template name first.")
    with tb3:
        if st.session_state.templates:
            tpl_sel = st.selectbox(
                "Load template",
                ["— select —"] + list(st.session_state.templates.keys()),
                key="tpl_load_sel",
                label_visibility="collapsed",
            )
        else:
            tpl_sel = "— select —"
            st.caption("No templates saved yet.")
    with tb4:
        if tpl_sel != "— select —":
            if st.button("⬇️ Apply Template", use_container_width=True):
                apt.update(copy.deepcopy(st.session_state.templates[tpl_sel]))
                st.success(f"Template **{tpl_sel}** applied.")
                st.rerun()

    st.divider()

    # ── Main two-column layout ────────────────
    left_col, right_col = st.columns([1, 1], gap="large")

    # ════════════════════════════════════════
    # LEFT — Building Shell inputs
    # ════════════════════════════════════════
    with left_col:
        st.subheader("🧱 Building Shell")

        apt["name"] = st.text_input(
            "Apartment name", value=apt["name"],
            key=f"apt_name_{cf}_{ca}"
        )

        c1, c2 = st.columns(2)
        with c1:
            apt["area_m2"] = st.number_input(
                "Floor area (m²)", 20.0, 200.0, float(apt["area_m2"]),
                step=0.5, key=f"area_{cf}_{ca}"
            )
            apt["wall_thickness_cm"] = st.slider(
                "Wall thickness (cm)", 10, 50, int(apt["wall_thickness_cm"]),
                key=f"wt_{cf}_{ca}"
            )
            apt["wall_material"] = st.selectbox(
                "Wall material", list(WALL_U_VALUES.keys()),
                index=list(WALL_U_VALUES.keys()).index(apt.get("wall_material", "Brick")),
                key=f"wm_{cf}_{ca}"
            )
            u_val = WALL_U_VALUES[apt["wall_material"]]
            st.caption(f"U-value: {u_val} W/m²K")

        with c2:
            apt["ceiling_height_m"] = st.number_input(
                "Ceiling height (m)", 2.0, 4.0, float(apt["ceiling_height_m"]),
                step=0.1, key=f"ch_{cf}_{ca}"
            )
            apt["window_count"] = st.number_input(
                "Window count", 0, 20, int(apt["window_count"]),
                key=f"wc_{cf}_{ca}"
            )
            apt["window_area_per_window_m2"] = st.number_input(
                "Window area each (m²)", 0.5, 5.0,
                float(apt["window_area_per_window_m2"]),
                step=0.1, key=f"wa_{cf}_{ca}"
            )
            apt["window_type"] = st.selectbox(
                "Window type", ["Single", "Double"],
                index=["Single", "Double"].index(apt.get("window_type", "Single")),
                key=f"wtype_{cf}_{ca}"
            )
            wu_val = WINDOW_U_VALUES[apt["window_type"]]
            st.caption(f"Window U-value: {wu_val} W/m²K")
            apt["plug_count"] = st.number_input(
                "Plug/outlet count", 0, 30, int(apt["plug_count"]),
                key=f"pc_{cf}_{ca}"
            )

        # ── Copy apartment ────────────────────
        st.subheader("📋 Copy Apartment To…")
        cp1, cp2, cp3 = st.columns([2, 2, 1])
        with cp1:
            target_floor = st.selectbox(
                "Floor", sorted(floors.keys()), key=f"cpf_{cf}_{ca}"
            )
        with cp2:
            target_apt_opts = sorted(floors[target_floor]["apartments"].keys()) + ["NEW"]
            target_apt = st.selectbox(
                "Apartment", target_apt_opts, key=f"cpa_{cf}_{ca}"
            )
        with cp3:
            st.write("")
            st.write("")
            if st.button("📤 Copy", key=f"do_copy_{cf}_{ca}", use_container_width=True):
                if target_apt == "NEW":
                    new_k = next_apt_key(list(floors[target_floor]["apartments"].keys()))
                    floors[target_floor]["apartments"][new_k] = copy.deepcopy(apt)
                    st.success(f"Copied → Floor {target_floor} / Apt {new_k}")
                else:
                    floors[target_floor]["apartments"][target_apt] = copy.deepcopy(apt)
                    st.success(f"Copied → Floor {target_floor} / Apt {target_apt}")
                st.rerun()

        # ── Delete apartment ──────────────────
        if len(floors[cf]["apartments"]) > 1:
            st.divider()
            if st.button(
                f"🗑️ Delete Apartment {ca}",
                use_container_width=True,
                key=f"del_apt_{cf}_{ca}"
            ):
                del floors[cf]["apartments"][ca]
                st.session_state.current_apartment = sorted(
                    floors[cf]["apartments"].keys()
                )[0]
                st.rerun()

    # ════════════════════════════════════════
    # RIGHT — Appliances + Quick Results
    # ════════════════════════════════════════
    with right_col:
        st.subheader("⚡ Appliances")

        add_c1, add_c2 = st.columns([1, 2])
        with add_c1:
            if st.button(
                "➕ Add Blank",
                use_container_width=True,
                key=f"add_blank_{cf}_{ca}"
            ):
                apt["appliances"].append({"name": "New Appliance", "watts": 100, "hours": 1})
                st.rerun()
        with add_c2:
            preset_names = [t["name"] for t in APPLIANCE_TEMPLATES]
            preset_sel   = st.selectbox(
                "Add preset",
                ["— select preset —"] + preset_names,
                key=f"preset_{cf}_{ca}",
                label_visibility="collapsed",
            )
            if preset_sel != "— select preset —":
                if st.button(
                    "➕ Add Preset",
                    use_container_width=True,
                    key=f"add_preset_{cf}_{ca}"
                ):
                    match = next(
                        (t for t in APPLIANCE_TEMPLATES if t["name"] == preset_sel), None
                    )
                    if match:
                        apt["appliances"].append({
                            "name":  match["name"],
                            "watts": match["watts"],
                            "hours": match["hours"],
                        })
                        st.rerun()

        # Appliance table
        appliances = apt["appliances"]
        if appliances:
            hcols = st.columns([0.35, 2.1, 1.15, 1.15, 0.75])
            for col, lbl in zip(hcols, ["**#**", "**Name**", "**Watts**", "**Hrs/day**", "**Del**"]):
                col.markdown(lbl)

            to_delete = None
            for i, appl in enumerate(appliances):
                row = st.columns([0.35, 2.1, 1.15, 1.15, 0.75])
                row[0].write(i + 1)
                appl["name"] = row[1].text_input(
                    "", value=appl["name"],
                    key=f"aname_{cf}_{ca}_{i}",
                    label_visibility="collapsed"
                )
                appl["watts"] = row[2].number_input(
                    "", 0, 5000, int(appl["watts"]),
                    key=f"awatts_{cf}_{ca}_{i}",
                    label_visibility="collapsed"
                )
                appl["hours"] = row[3].number_input(
                    "", 0.0, 24.0, float(appl["hours"]),
                    step=0.5,
                    key=f"ahours_{cf}_{ca}_{i}",
                    label_visibility="collapsed"
                )
                if row[4].button("🗑️", key=f"adel_{cf}_{ca}_{i}"):
                    to_delete = i

            if to_delete is not None:
                apt["appliances"].pop(to_delete)
                st.rerun()
        else:
            st.info("No appliances yet. Add one using the buttons above.")

        # ── Quick results ─────────────────────
        st.divider()
        st.subheader("📊 Quick Results — This Apartment")

        res = calculate_apartment_electricity(
            apt,
            st.session_state.building["climate_zone"],
            st.session_state.building["location_type"],
        )

        m1, m2 = st.columns(2)
        m1.metric("🌡️ Cooling Load", f"{res['wall_load'] + res['window_load']:,.0f} kWh/yr")
        m2.metric("⚡ Appliances",   f"{res['appliance_total']:,.0f} kWh/yr")
        m3, m4 = st.columns(2)
        m3.metric("🔌 Plug Load", f"{res['plug_load']:,.0f} kWh/yr")
        m4.metric("🏠 Total",     f"{res['total']:,.0f} kWh/yr")

        if st.session_state.show_cost:
            st.info(f"💰 Estimated annual cost: **{fmt_currency(res['total'])}**")

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
            index=list(CLIMATE_ZONES.keys()).index(b.get("climate_zone", "Coast"))
        )
        zone_info = CLIMATE_ZONES[b["climate_zone"]]
        st.caption(
            f"ℹ️ {zone_info['desc']}  |  "
            f"Cooling ×{zone_info['cooling']}  |  Heating ×{zone_info['heating']}"
        )
        b["location_type"] = st.selectbox(
            "Location type", list(LOCATION_MULTIPLIERS.keys()),
            index=list(LOCATION_MULTIPLIERS.keys()).index(b.get("location_type", "Urban"))
        )
        st.caption(f"ℹ️ Location multiplier: ×{LOCATION_MULTIPLIERS[b['location_type']]}")

    with c2:
        st.subheader("💵 Cost Settings")
        b["electricity_price_usd"] = st.number_input(
            "Electricity price (USD/kWh)", 0.01, 1.0,
            float(b.get("electricity_price_usd", 0.12)),
            step=0.001, format="%.3f"
        )
        b["exchange_rate"] = st.number_input(
            "Exchange rate (1 USD = ? DZD)", 1.0, 10000.0,
            float(b.get("exchange_rate", 135.0)), step=1.0
        )
        dzd_equiv = b["electricity_price_usd"] * b["exchange_rate"]
        st.caption(f"≈ {dzd_equiv:.2f} DZD / kWh")
        st.session_state.show_cost = st.checkbox(
            "Show cost estimates in results",
            value=st.session_state.show_cost
        )

    st.divider()

    # ── Copy Floor ───────────────────────────
    st.subheader("📋 Copy Entire Floor")
    floor_nums = sorted(st.session_state.floors.keys())
    if len(floor_nums) >= 2:
        cf1, cf2, cf3 = st.columns([2, 2, 2])
        with cf1:
            src_floor = st.selectbox("Source floor", floor_nums, key="src_floor")
        with cf2:
            tgt_options = [f for f in floor_nums if f != src_floor]
            tgt_floor   = st.selectbox("Target floor", tgt_options, key="tgt_floor")
        with cf3:
            st.write("")
            st.write("")
            if st.button(f"📤 Copy Floor {src_floor} → {tgt_floor}", use_container_width=True):
                st.session_state.floors[tgt_floor] = copy.deepcopy(
                    st.session_state.floors[src_floor]
                )
                st.success(f"Floor {src_floor} copied to Floor {tgt_floor}!")
                st.rerun()
    else:
        st.info("You need at least 2 floors to use the copy floor feature.")

    st.divider()

    # ── Floor overview ────────────────────────
    st.subheader("🏗️ Floor & Apartment Overview")
    for fn in floor_nums:
        apts     = st.session_state.floors[fn]["apartments"]
        apt_list = ", ".join([f"{k}: {v['name']}" for k, v in sorted(apts.items())])
        st.write(f"**Floor {fn}** — {len(apts)} apartment(s): {apt_list}")

    # ── U-value reference ────────────────────
    with st.expander("📖 U-Value Reference Tables"):
        st.markdown("**Wall Materials**")
        wall_df = pd.DataFrame([
            {
                "Material":        k,
                "U-value (W/m²K)": v,
                "Efficiency":      (
                    "Excellent" if v < 0.5 else
                    "Good"      if v < 1.0 else
                    "Average"   if v < 1.7 else
                    "Poor"
                ),
            }
            for k, v in WALL_U_VALUES.items()
        ])
        st.dataframe(wall_df, use_container_width=True, hide_index=True)

        st.markdown("**Window Types**")
        win_df = pd.DataFrame([
            {"Type": k, "U-value (W/m²K)": v}
            for k, v in WINDOW_U_VALUES.items()
        ])
        st.dataframe(win_df, use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────
# PAGE: APARTMENT TEMPLATES
# ─────────────────────────────────────────────
def page_templates():
    st.title("📁 Apartment Templates")

    if not st.session_state.templates:
        st.info(
            "No templates saved yet. "
            "Go to the Dashboard, configure an apartment, and click 'Save as Template'."
        )
        return

    for tpl_name, tpl_data in list(st.session_state.templates.items()):
        with st.expander(f"📄 {tpl_name}"):
            tc1, tc2 = st.columns([3, 1])
            with tc1:
                st.write(
                    f"**Area:** {tpl_data.get('area_m2','—')} m²  |  "
                    f"**Wall:** {tpl_data.get('wall_material','—')}  |  "
                    f"**Windows:** {tpl_data.get('window_count','—')} × "
                    f"{tpl_data.get('window_type','—')} glazing"
                )
                appliances = tpl_data.get("appliances", [])
                if appliances:
                    st.write(
                        f"**Appliances ({len(appliances)}):** "
                        + ", ".join([a["name"] for a in appliances])
                    )
                else:
                    st.write("No appliances in this template.")
            with tc2:
                new_name = st.text_input("Rename", value=tpl_name, key=f"rename_{tpl_name}")
                if new_name != tpl_name:
                    if st.button("💾 Save rename", key=f"savern_{tpl_name}"):
                        st.session_state.templates[new_name] = st.session_state.templates.pop(tpl_name)
                        st.rerun()
                if st.button("🗑️ Delete", key=f"deltpl_{tpl_name}"):
                    del st.session_state.templates[tpl_name]
                    st.rerun()

    st.divider()
    st.subheader("📤 Export / Import Templates")
    ec1, ec2 = st.columns(2)
    with ec1:
        tpl_json = json.dumps(st.session_state.templates, indent=2)
        st.download_button(
            "⬇️ Export Templates (JSON)", data=tpl_json,
            file_name="templates.json", mime="application/json"
        )
    with ec2:
        uploaded = st.file_uploader(
            "⬆️ Import Templates (JSON)", type=["json"], key="tpl_import"
        )
        if uploaded:
            try:
                imported = json.load(uploaded)
                st.session_state.templates.update(imported)
                st.success(f"Imported {len(imported)} template(s).")
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
        st.session_state.floors,
        b["climate_zone"],
        b["location_type"],
    )

    building_total = results["building_total"]
    eui            = results["eui"]
    benchmark      = get_eui_benchmark(eui)

    # KPI row
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🏢 Building Total", f"{building_total:,.0f} kWh/yr")
    k2.metric("📐 EUI",            f"{eui:,.1f} kWh/m²/yr")
    k3.metric("📏 Total Area",     f"{results['total_area']:,.0f} m²")
    k4.metric("🌍 Climate Zone",   b["climate_zone"])

    st.info(f"🔍 EUI Benchmark: **{benchmark}**")

    if st.session_state.show_cost:
        st.success(f"💰 Estimated annual electricity cost: **{fmt_currency(building_total)}**")

    st.divider()

    # Floor bar chart
    st.subheader("📊 Energy by Floor")
    floor_df = pd.DataFrame([
        {"Floor": f"Floor {k}", "kWh/year": round(v, 1)}
        for k, v in sorted(results["floor_totals"].items())
    ])
    st.bar_chart(floor_df.set_index("Floor"))

    # Apartment breakdown table
    st.subheader("🏠 Energy by Apartment")
    apt_rows = []
    for (floor_num, apt_key), res in sorted(results["apt_results"].items()):
        apt_name = st.session_state.floors[floor_num]["apartments"][apt_key]["name"]
        row = {
            "Floor":            floor_num,
            "Apt":              apt_key,
            "Name":             apt_name,
            "Area (m²)":        round(res["area_m2"], 1),
            "Cooling (kWh)":    round(res["wall_load"] + res["window_load"], 1),
            "Appliances (kWh)": round(res["appliance_total"], 1),
            "Plug Load (kWh)":  round(res["plug_load"], 1),
            "Total (kWh/yr)":   round(res["total"], 1),
            "EUI (kWh/m²)":     round(res["total"] / res["area_m2"], 1) if res["area_m2"] > 0 else 0,
        }
        if st.session_state.show_cost:
            price_usd     = b.get("electricity_price_usd", 0.12)
            rate          = b.get("exchange_rate", 135.0)
            row["Cost (USD)"] = round(res["total"] * price_usd, 2)
            row["Cost (DZD)"] = round(res["total"] * price_usd * rate, 0)
        apt_rows.append(row)

    apt_df = pd.DataFrame(apt_rows)
    st.dataframe(apt_df, use_container_width=True, hide_index=True)

    # Top 5 appliances
    st.subheader("🔌 Top 5 Energy-Consuming Appliances (Building-wide)")
    all_appliances = []
    for (floor_num, apt_key), res in results["apt_results"].items():
        for appl in res["appliance_breakdown"]:
            all_appliances.append({
                "Appliance": appl["name"],
                "kWh/year":  round(appl["kwh"], 1),
                "Watts":     appl["watts"],
                "Hours/day": appl["hours"],
                "Floor":     floor_num,
                "Apt":       apt_key,
            })

    if all_appliances:
        appl_df = (
            pd.DataFrame(all_appliances)
            .sort_values("kWh/year", ascending=False)
            .head(5)
        )
        st.dataframe(appl_df, use_container_width=True, hide_index=True)
        st.subheader("📊 Top Appliances Chart")
        st.bar_chart(appl_df.set_index("Appliance")["kWh/year"])
    else:
        st.info("No appliances added to any apartment yet.")

    st.divider()

    # EUI benchmark reference
    st.subheader("📋 EUI Benchmark Reference")
    bench_df = pd.DataFrame([
        {"Max EUI (kWh/m²/yr)": t if t < 999 else "999+", "Rating": l}
        for t, l in EUI_BENCHMARKS
    ])
    st.dataframe(bench_df, use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────
# PAGE: SAVE / LOAD PROJECT
# ─────────────────────────────────────────────
def page_save_load():
    st.title("💾 Save / Load Project")

    # Save
    st.subheader("💾 Save Current Project")
    project_name = st.text_input(
        "Project name", value=st.session_state.building["name"]
    )
    if st.button("💾 Save to Database"):
        if project_name.strip():
            save_project_to_db(
                project_name,
                st.session_state.building,
                st.session_state.floors
            )
            st.success(f"Project '{project_name}' saved!")
        else:
            st.error("Please enter a project name.")

    st.divider()

    # Load
    st.subheader("📂 Load Saved Project")
    projects = load_all_projects()
    if projects:
        proj_options = {
            f"{row[1]}  (saved: {row[2]})": row[0] for row in projects
        }
        sel_proj = st.selectbox("Select project", list(proj_options.keys()))
        lc1, lc2 = st.columns(2)
        with lc1:
            if st.button("📂 Load Project"):
                pid = proj_options[sel_proj]
                if load_project_from_db(pid):
                    st.success("Project loaded!")
                    st.rerun()
                else:
                    st.error("Failed to load project.")
        with lc2:
            if st.button("🗑️ Delete Project"):
                pid = proj_options[sel_proj]
                delete_project_from_db(pid)
                st.success("Project deleted.")
                st.rerun()
    else:
        st.info("No saved projects found.")

    st.divider()

    # JSON export / import
    st.subheader("📤 Export / Import (JSON)")
    ec1, ec2 = st.columns(2)
    with ec1:
        json_data = export_to_json()
        st.download_button(
            "⬇️ Export Project (JSON)", data=json_data,
            file_name=f"{st.session_state.building['name'].replace(' ', '_')}.json",
            mime="application/json"
        )
    with ec2:
        uploaded = st.file_uploader(
            "⬆️ Import Project (JSON)", type=["json"], key="proj_import"
        )
        if uploaded:
            content = uploaded.read().decode("utf-8")
            ok, msg = import_from_json(content)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

# ─────────────────────────────────────────────
# PAGE: HELP
# ─────────────────────────────────────────────
def page_help():
    st.title("❓ Help & Instructions")
    st.markdown("""
## Getting Started

1. **Dashboard** — The main editing panel. Click any apartment in the left sidebar tree to load it here.
2. **Building Settings** — Set climate zone, location type, electricity price (USD + DZD), and exchange rate.
3. **Results & Forecast** — Full energy breakdown: EUI, floor chart, apartment table, top appliances, cost estimate.
4. **Apartment Templates** — Save and reuse apartment configurations.
5. **Save/Load Project** — Persist to SQLite or export/import as JSON.

---

## Sidebar Tree Navigation

```
🏢 My Building
├── 📐 Floor 1  [➕ Apt]  [📋 Copy]  [🗑️ Del]
│   ├── ▶ 🚪 A: Apartment A    ← selected (highlighted blue)
│   └──   🚪 B: Apartment B
├── 📐 Floor 2  [➕ Apt]  [📋 Copy]  [🗑️ Del]
│   └──   🚪 A: Apartment A
└── ➕ Add Floor
```

- **Click any apartment** → loads its properties into the Dashboard instantly
- **➕ Apt** → adds a new apartment to that floor
- **📋 Copy** → duplicates the entire floor as a new floor
- **🗑️ Del** → deletes that floor (disabled if only 1 floor remains)
- **➕ Add Floor** → adds a new empty floor at the bottom

---

## Calculation Summary

- **Wall cooling load** = Wall area × U-value × 15°C × 8h × 120 days / 1000
- **Window cooling load** = Window area × U-value × 15°C × 8h × 120 days / 1000
- **Appliance load** = Watts × hours/day × days/year / 1000
- **Plug load** = plug count × 50W × 8h × 365 days / 1000
- **Total** = (all loads) × climate multiplier × location multiplier

## Climate Zones

| Zone | Description | Cooling × | Heating × |
|------|-------------|-----------|-----------|
| Coast | Humid, moderate | 1.1 | 0.8 |
| Desert | Hot days, cool nights | 1.4 | 0.6 |
| Mountains | Cold winters | 0.6 | 1.5 |
| City | Urban baseline | 1.0 | 1.0 |

## EUI Benchmarks

| Max EUI (kWh/m²/yr) | Rating |
|----------------------|--------|
| < 50 | Excellent — Passive/Net-Zero |
| 50–100 | Good — Energy-efficient |
| 100–150 | Average — Standard construction |
| 150–200 | Below average |
| 200–300 | Poor |
| > 300 | Very poor |

## Currency
Costs shown in both **USD** and **DZD**. Adjust price and exchange rate in **Building Settings**.

---

## Tips
- Click **🏗️ Load Example Building** to instantly see a working 3-floor example
- Use **📋 Copy** on a floor to quickly duplicate standard floor plans
- Save apartment templates to reuse configurations across different projects
- Export as JSON to back up or share your project
""")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    init_db()
    init_session_state()
    render_sidebar()

    page = st.session_state.page
    if page == "Dashboard":
        page_dashboard()
    elif page == "Building Settings":
        page_building_settings()
    elif page == "Apartment Templates":
        page_templates()
    elif page == "Results & Forecast":
        page_results()
    elif page == "Save/Load Project":
        page_save_load()
    elif page == "Help":
        page_help()


if __name__ == "__main__":
    main()