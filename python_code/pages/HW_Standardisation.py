import streamlit as st
import struct
import math
import os
import threading
import pandas as pd
from datetime import datetime
from pathlib import Path


from pymodbus.client import ModbusSerialClient

# MQTT (CAMERA + GYRO ONLY)
from mqtt_client import start_mqtt, latest_data, lock

# =====================================================
# STREAMLIT CONFIG
# =====================================================
# =====================================================
# COMPACT HEADER + VIEWPORT CONTROL
# =====================================================
st.markdown("""
<style>
.block-container {
    padding-top: 2rem;
    max-width: 1400px;
}

/* Headings */
h1, h2, h3, h4 {
    margin-bottom: 0.7rem !important;
}

/* Kill Streamlit spacing */
div[data-testid="stVerticalBlock"],
div[data-testid="stHorizontalBlock"],
div[data-testid="column"] {
    padding: 0 !important;
    margin: 0 !important;
}

/* Kill internal flex gaps */
.st-emotion-cache-wfksaw,
.st-emotion-cache-1r6slb0 {
    gap: 0 !important;
}

/* Remove top margin */
div[data-testid="stVerticalBlock"]:first-child {
    margin-top: 0 !important;
}

/* Quad container */
.quad {
    padding: 0 !important;
    margin: 0 !important;
    line-height: 1.2;
}

/* Metrics */
.metric-row {
    margin-bottom: 1rem;
}

/* Compact alarm */
.alarm {
    background: #ffe6e6;
    color: #a10000;
    padding: 0.4rem 0.8rem;
    border-radius: 6px;
    font-weight: 600;
    margin-bottom: 0.5rem;
}
/* Camera box */
.camera-box {
    width: 100%;
    max-width: 520px;        /* 👈 control WIDTH */
    height: 320px;           /* 👈 control HEIGHT */
    margin: 0 auto;
    border-radius: 8px;
    overflow: hidden;
    background: #000;
    display: flex;
    align-items: center;
    justify-content: center;
}

.camera-box img {
    width: 100%;
    height: 100%;
    object-fit: cover;       /* 👈 cover | contain | fill */
}

</style>
""", unsafe_allow_html=True)




# st.markdown(
#     "<div class='dashboard-title'>🔥 Radar-Based Ladle Pouring Dashboard</div>",
#     unsafe_allow_html=True,
# )


# =====================================================
# BASIC CONFIG
# =====================================================
DEFAULT_PORT = "COM14"
BAUDRATE = 9600
SLAVE_ID = 1
ENGINEER_PASSWORD = "0000"

# =====================================================
# LADLE GEOMETRY PROFILES (EDIT/EXTEND AS NEEDED)
# Conical frustum approximation: bottom_diameter -> top_diameter over height
# =====================================================
LADLE_PROFILES = {
    "LADLE_150T": {
        "height_m": 3.2,
        "top_diameter_m": 3.72,
        "bottom_diameter_m": 2.20,
        "capacity_tons": 150,
    },
    "LADLE_100T": {
        "height_m": 2.8,
        "top_diameter_m": 3.20,
        "bottom_diameter_m": 2.00,
        "capacity_tons": 100,
    },
}

# Alarm if > (1 + OVERFILL_TOL) * capacity
OVERFILL_TOL = 0.02

# =====================================================
# RS485 MODBUS REGISTERS (from your manual)
# =====================================================
# Monitoring (FC=0x03)
REG_SPACE_HEIGHT_F     = 4096  # 0x1000 (m, float)
REG_MATERIAL_HEIGHT_F  = 4098  # 0x1002 (m, float)
REG_MATERIAL_PCT_F     = 4100  # 0x1004 (0..100, float)
REG_CURRENT_F          = 4102  # 0x1006 (mA, float)
REG_TEMPERATURE_F      = 4110  # 0x100E (°C, float)

# Engineering (FC=0x03/0x10)
REG_LOW_ADJ_F          = 8192  # 0x2000 (m, float)
REG_HIGH_ADJ_F         = 8196  # 0x2004 (m, float)
REG_DAMPING_I32        = 8200  # 0x2008 (sec, int32 -> 2 regs)
REG_RANGE_F            = 8202  # 0x200A (m, float)
REG_BLIND_F            = 8204  # 0x200C (m, float)

# Alarm config (FC=0x03/0x10)
REG_ALARM_TYPE_I16     = 4372  # 0x1114 (1 single, 2 dual)
REG_ALARM_LEVEL_I16    = 4373  # 0x1115 (0 low, 1 high)
REG_SINGLE_ALARM_F     = 4374  # 0x1116 (m, float)
REG_SINGLE_RANGE_I16   = 4376  # 0x1118 (0 upper, 1 lower)
REG_DUAL_UPPER_F       = 4377  # 0x1119 (m, float)
REG_DUAL_LOWER_F       = 4379  # 0x111B (m, float)
REG_RETURN_DIFF_F      = 4381  # 0x111D (m, float)

# Relay status (FC=0x03; not polled by the live dashboard)
REG_RELAY1_STATUS_I16  = 4383  # 0x111F (0/1)
REG_RELAY2_STATUS_I16  = 4384  # 0x1120 (0/1)

# Restart + slave address
REG_RESTART_I32        = 4370   # 0x1112 (int32 with WORD-SWAP write)
REG_SLAVE_ADDR_I16     = 16390  # 0x4006 (FC=0x06)

# =====================================================
# CSV STORAGE (CSV ONLY, as requested)
# =====================================================
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

HISTORY_FILE = os.path.join(DATA_DIR, "pour_history.csv")
PROFILE_FILE = os.path.join(DATA_DIR, "ladle_profiles.csv")

if not os.path.exists(HISTORY_FILE):
    pd.DataFrame(columns=[
        "ladle_id", "ladle_type", "track_no", "pour_no",
        "operator", "shift",
        "pour_start_time", "pour_end_time",
        "start_height_m", "end_height_m",
        "start_tons", "end_tons",
        "empty_distance_m",
        "calibration_source"  # "saved_profile" | "manual_set" | "none"
    ]).to_csv(HISTORY_FILE, index=False)

if not os.path.exists(PROFILE_FILE):
    pd.DataFrame(columns=["ladle_id", "empty_distance_m", "updated_at"]).to_csv(PROFILE_FILE, index=False)


def file_mtime_ns(path: str) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


@st.cache_data(show_spinner=False)
def read_csv_cached(path: str, mtime_ns: int) -> pd.DataFrame:
    """Parse a CSV only when its path or modification time changes."""
    return pd.read_csv(path)


def load_profiles() -> dict:
    try:
        df = read_csv_cached(PROFILE_FILE, file_mtime_ns(PROFILE_FILE))
        out = {}
        for _, r in df.iterrows():
            lid = str(r.get("ladle_id", "")).strip()
            if lid:
                out[lid] = float(r["empty_distance_m"])
        return out
    except Exception:
        return {}

def save_profile(ladle_id: str, empty_distance_m: float):
    ladle_id = (ladle_id or "").strip()
    if not ladle_id:
        return
    now_ts = datetime.now().isoformat(timespec="seconds")
    try:
        df = read_csv_cached(PROFILE_FILE, file_mtime_ns(PROFILE_FILE))
    except Exception:
        df = pd.DataFrame(columns=["ladle_id", "empty_distance_m", "updated_at"])

    df = df[df["ladle_id"] != ladle_id]
    df.loc[len(df)] = [ladle_id, float(empty_distance_m), now_ts]
    df.to_csv(PROFILE_FILE, index=False)

def delete_profile(ladle_id: str):
    ladle_id = (ladle_id or "").strip()
    if not ladle_id:
        return
    try:
        df = read_csv_cached(PROFILE_FILE, file_mtime_ns(PROFILE_FILE))
        df = df[df["ladle_id"] != ladle_id]
        df.to_csv(PROFILE_FILE, index=False)
    except Exception:
        pass

# =====================================================
# MODBUS HELPERS
# =====================================================
@st.cache_resource(show_spinner=False)
def modbus_resource(port: str):
    client = ModbusSerialClient(
        port=port,
        baudrate=BAUDRATE,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=0.3,
        retries=0,
    )
    client.unit_id = SLAVE_ID
    return client, threading.Lock()


def _close_failed_client(client: ModbusSerialClient):
    try:
        client.close()
    except Exception:
        pass


def _read_regs(port: str, start_reg: int, count: int):
    client, client_lock = modbus_resource(port)
    with client_lock:
        try:
            if not client.connected and not client.connect():
                return None
            response = client.read_holding_registers(
                start_reg,
                count=count,
                device_id=SLAVE_ID,
            )
            if response is None or response.isError():
                _close_failed_client(client)
                return None
            return response.registers
        except Exception:
            _close_failed_client(client)
            return None


def _decode_f32_be(registers, offset: int):
    if registers is None or len(registers) < offset + 2:
        return None
    r0, r1 = registers[offset:offset + 2]
    raw = bytes([(r0 >> 8) & 0xFF, r0 & 0xFF, (r1 >> 8) & 0xFF, r1 & 0xFF])
    return struct.unpack(">f", raw)[0]

def read_f32_be(port: str, start_reg: int):
    regs = _read_regs(port, start_reg, 2)
    return _decode_f32_be(regs, 0)

def read_i32_be(port: str, start_reg: int):
    regs = _read_regs(port, start_reg, 2)
    if not regs:
        return None
    r0, r1 = regs
    raw = bytes([(r0 >> 8) & 0xFF, r0 & 0xFF, (r1 >> 8) & 0xFF, r1 & 0xFF])
    return struct.unpack(">i", raw)[0]

def read_i16(port: str, reg: int):
    regs = _read_regs(port, reg, 1)
    if not regs:
        return None
    v = regs[0]
    return v - 0x10000 if v >= 0x8000 else v


def read_monitoring_block(port: str) -> dict:
    registers = _read_regs(port, REG_SPACE_HEIGHT_F, 16)
    return {
        "distance_m": _decode_f32_be(registers, REG_SPACE_HEIGHT_F - REG_SPACE_HEIGHT_F),
        "material_height_m": _decode_f32_be(registers, REG_MATERIAL_HEIGHT_F - REG_SPACE_HEIGHT_F),
        "material_pct": _decode_f32_be(registers, REG_MATERIAL_PCT_F - REG_SPACE_HEIGHT_F),
        "current_ma": _decode_f32_be(registers, REG_CURRENT_F - REG_SPACE_HEIGHT_F),
        "temperature_c": _decode_f32_be(registers, REG_TEMPERATURE_F - REG_SPACE_HEIGHT_F),
    }


def _perform_write(port: str, operation, success_message="Written"):
    client, client_lock = modbus_resource(port)
    with client_lock:
        try:
            if not client.connected and not client.connect():
                return False, "Connection failed"
            response = operation(client)
            if response is not None and not response.isError():
                return True, success_message
            _close_failed_client(client)
            return False, "Write failed"
        except Exception as exc:
            _close_failed_client(client)
            return False, str(exc)


def write_f32_fc10_be(port: str, start_reg: int, value: float):
    raw = struct.pack(">f", float(value))
    regs = [(raw[0] << 8) | raw[1], (raw[2] << 8) | raw[3]]
    return _perform_write(
        port,
        lambda client: client.write_registers(start_reg, regs, device_id=SLAVE_ID),  # FC=0x10
    )

def write_i32_fc10_be(port: str, start_reg: int, value: int):
    raw = struct.pack(">i", int(value))
    regs = [(raw[0] << 8) | raw[1], (raw[2] << 8) | raw[3]]
    return _perform_write(
        port,
        lambda client: client.write_registers(start_reg, regs, device_id=SLAVE_ID),  # FC=0x10
    )

def write_i16_fc10(port: str, reg: int, value: int):
    return _perform_write(
        port,
        lambda client: client.write_registers(
            reg,
            [int(value) & 0xFFFF],
            device_id=SLAVE_ID,
        ),  # FC=0x10 (1 reg)
    )

def write_slave_addr_fc06(port: str, reg: int, value: int):
    return _perform_write(
        port,
        lambda client: client.write_register(
            reg,
            int(value) & 0xFFFF,
            device_id=SLAVE_ID,
        ),  # FC=0x06
    )

def write_restart_word_swap_fc10(port: str):
    """
    Manual: write 32-bit int to 0x1112 to restart, with word-swapped order.
    For value=1: send [0x0001, 0x0000]
    """
    return _perform_write(
        port,
        lambda client: client.write_registers(
            REG_RESTART_I32,
            [0x0001, 0x0000],
            device_id=SLAVE_ID,
        ),  # FC=0x10
        success_message="Restart command sent",
    )

# =====================================================
# POUR CALLBACKS
# =====================================================
def start_pouring():
    state = st.session_state
    state.pouring = True
    state.pour_start_time = datetime.now()
    state.start_height = None
    state.start_tons = None


def stop_pouring(ladle_id, ladle_type, track_no, pour_no, operator, shift):
    state = st.session_state
    state.pouring = False

    if state.pour_start_time is not None and state.start_height is not None:
        live = state.get("live", {})
        history = read_csv_cached(HISTORY_FILE, file_mtime_ns(HISTORY_FILE))
        history.loc[len(history)] = [
            ladle_id,
            ladle_type,
            track_no,
            pour_no,
            operator,
            shift,
            state.pour_start_time,
            datetime.now(),
            state.start_height,
            live.get("material_height_m"),
            state.start_tons,
            live.get("ladle_tons"),
            state.empty_distance,
            state.calibration_source,
        ]
        history.to_csv(HISTORY_FILE, index=False)

    state.pour_start_time = None
    state.start_height = None
    state.start_tons = None


# =====================================================
# START MQTT (CAMERA + GYRO ONLY)
# =====================================================
start_mqtt()

# =====================================================
# SESSION STATE
# =====================================================
ss = st.session_state
ss.setdefault("engineer_mode", False)

ss.setdefault("profiles", load_profiles())
ss.setdefault("last_loaded_ladle", None)
ss.setdefault("empty_distance", None)
ss.setdefault("calibration_source", "none")  # saved_profile/manual_set/none

ss.setdefault("pouring", False)
ss.setdefault("pour_start_time", None)
ss.setdefault("start_height", None)
ss.setdefault("start_tons", None)
ss.setdefault("live", {
    "distance_m": None,
    "material_height_m": None,
    "material_pct": None,
    "ladle_tons": None,
    "current_ma": None,
    "temperature_c": None,
})

# =====================================================
# SIDEBAR: Operator + Ladle + Pour controls + Calibration + Engineer login
# =====================================================
st.sidebar.header("👷 Operator Details")
operator = st.sidebar.text_input("Operator Name")
shift = st.sidebar.selectbox("Shift", ["A", "B", "C", "Night"])
port = st.sidebar.text_input("COM Port", DEFAULT_PORT)

st.sidebar.markdown("---")
st.sidebar.subheader("🪣 Ladle Profile")
ladle_id = st.sidebar.text_input("Ladle ID", value="")
ladle_type = st.sidebar.selectbox("Ladle Type", list(LADLE_PROFILES.keys()))
track_no = st.sidebar.selectbox("Track / Line", ["Track-1", "Track-2", "Track-3"])
pour_no = st.sidebar.selectbox("Pour Count", [1, 2])

# Auto-load empty distance when ladle changes
if ladle_id and ss.last_loaded_ladle != ladle_id:
    ss.profiles = load_profiles()
    if ladle_id in ss.profiles:
        ss.empty_distance = float(ss.profiles[ladle_id])
        ss.calibration_source = "saved_profile"
    else:
        ss.empty_distance = None
        ss.calibration_source = "none"
    ss.last_loaded_ladle = ladle_id

st.sidebar.markdown("---")
st.sidebar.subheader("Pour Control (Manual)")
if not ss.pouring:
    st.sidebar.button("▶ Start Pouring", on_click=start_pouring)
else:
    st.sidebar.button(
        "⏹ Stop Pouring",
        on_click=stop_pouring,
        args=(ladle_id, ladle_type, track_no, pour_no, operator, shift),
    )

st.sidebar.markdown("---")
st.sidebar.subheader("🧭 Calibration")

if ss.empty_distance is not None:
    st.sidebar.caption(f"Empty distance (m): {ss.empty_distance:.3f}  [{ss.calibration_source}]")
else:
    st.sidebar.caption("Empty distance not set")

calib_locked = ss.pouring
if calib_locked:
    st.sidebar.warning("Calibration locked during POURING")

if st.sidebar.button("Set Current Distance as EMPTY", disabled=calib_locked):
    distance_m = ss.live.get("distance_m")
    if not ladle_id:
        st.sidebar.error("Enter Ladle ID first")
    elif distance_m is None:
        st.sidebar.error("Distance not available")
    else:
        ss.empty_distance = float(distance_m)
        save_profile(ladle_id, ss.empty_distance)
        ss.profiles = load_profiles()
        ss.calibration_source = "manual_set"
        st.sidebar.success(f"Saved EMPTY for {ladle_id}: {ss.empty_distance:.3f} m")

if st.sidebar.button("Clear Calibration for This Ladle", disabled=calib_locked):
    if not ladle_id:
        st.sidebar.error("Enter Ladle ID first")
    else:
        delete_profile(ladle_id)
        ss.profiles = load_profiles()
        ss.empty_distance = None
        ss.calibration_source = "none"
        st.sidebar.success("Calibration cleared")

st.sidebar.markdown("---")
st.sidebar.subheader("⚙ Engineer Mode")
enable_engineer = st.sidebar.checkbox("Enable Engineer Mode", value=ss.engineer_mode)
if enable_engineer:
    pwd = st.sidebar.text_input("Password", type="password")
    if pwd == ENGINEER_PASSWORD:
        ss.engineer_mode = True
        st.sidebar.success("Engineer access granted")
    else:
        ss.engineer_mode = False
        if pwd:
            st.sidebar.error("Invalid password")
else:
    ss.engineer_mode = False


def metric_row(label, value):
    st.markdown(
        f"""
        <div class="metric-row">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


GYRO_FIELDS = [
    ("Gyro X", "gyro_x"),
    ("Gyro Y", "gyro_y"),
    ("Gyro Z", "gyro_z"),
    ("Accel X", "accel_x"),
    ("Accel Y", "accel_y"),
    ("Accel Z", "accel_z"),
]

LADLE_IMAGE_DIR = Path(__file__).parent.parent / "LadleImages"
AVAILABLE_LADLE_IMAGES = {}
for image_path in LADLE_IMAGE_DIR.glob("Ladle_Image_*.png"):
    try:
        AVAILABLE_LADLE_IMAGES[int(image_path.stem.rsplit("_", 1)[1])] = image_path
    except ValueError:
        pass


def snapped_ladle_image(material_pct):
    requested_fill = int(round(material_pct)) if material_pct is not None else 0
    requested_fill = max(0, min(requested_fill, 100))
    requested_fill = int(round(requested_fill / 5) * 5)
    if not AVAILABLE_LADLE_IMAGES:
        return requested_fill, LADLE_IMAGE_DIR / f"Ladle_Image_{requested_fill}.png"
    fill_pct = min(AVAILABLE_LADLE_IMAGES, key=lambda level: abs(level - requested_fill))
    return fill_pct, AVAILABLE_LADLE_IMAGES[fill_pct]


@st.fragment(run_every="1s")
def live_dashboard(port: str, ladle_type: str, pouring: bool):
    readings = read_monitoring_block(port)
    distance_m = readings["distance_m"]
    material_height_m = readings["material_height_m"]
    material_pct_dev = readings["material_pct"]
    current_ma = readings["current_ma"]
    temperature_c = readings["temperature_c"]

    # Geometry-based tons and fill fraction (unchanged calculation).
    geom = LADLE_PROFILES[ladle_type]
    ladle_tons = None
    fill_frac = None
    overfill = False

    if material_height_m is not None:
        H = geom["height_m"]
        r_bottom = geom["bottom_diameter_m"] / 2.0
        r_top = geom["top_diameter_m"] / 2.0

        h = max(0.0, min(material_height_m, H))
        r_h = r_bottom + (r_top - r_bottom) * (h / H if H > 0 else 0.0)

        volume_m3 = (math.pi * h / 3.0) * (r_bottom**2 + r_bottom * r_h + r_h**2)
        ladle_tons = (volume_m3 * 7000.0) / 1000.0
        fill_frac = ladle_tons / float(geom["capacity_tons"]) if geom["capacity_tons"] else None

        if fill_frac is not None and fill_frac > (1.0 + OVERFILL_TOL):
            overfill = True

    st.session_state.live = {
        "distance_m": distance_m,
        "material_height_m": material_height_m,
        "material_pct": material_pct_dev,
        "ladle_tons": ladle_tons,
        "fill_frac": fill_frac,
        "current_ma": current_ma,
        "temperature_c": temperature_c,
    }

    # Capture the first valid reading after pouring begins.
    if pouring and st.session_state.start_height is None and material_height_m is not None:
        st.session_state.start_height = material_height_m
        st.session_state.start_tons = ladle_tons

    with lock:
        frame = latest_data.get("frame")
        gyro = dict(latest_data.get("gyro") or {})

    # Fixed-height containers keep the live fragment from shifting surrounding UI.
    with st.container(height=50, border=False):
        if overfill:
            st.markdown(
                '<div class="alarm">🚨 OVERFILL ALARM: Fill exceeds ladle capacity!</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown("&nbsp;", unsafe_allow_html=True)

    with st.container(height=265, border=False):
        q1, q2, q_status = st.columns([4, 4, 2], gap="medium")

        with q1:
            st.markdown("<div class='quad'>", unsafe_allow_html=True)
            st.markdown("### 📡 Radar Data")
            a, b, c = st.columns(3)
            with a:
                metric_row("Distance (m)", f"{distance_m:.3f}" if distance_m is not None else "—")
                metric_row("Material Height (m)", f"{material_height_m:.3f}" if material_height_m is not None else "—")
            with b:
                metric_row("Device Fill %", f"{material_pct_dev:.1f}%" if material_pct_dev is not None else "—")
                metric_row("Calculated Tons", f"{ladle_tons:.2f}" if ladle_tons is not None else "—")
            with c:
                metric_row("Current (mA)", f"{current_ma:.1f}" if current_ma is not None else "—")
                metric_row("Temperature (°C)", f"{temperature_c:.1f}" if temperature_c is not None else "—")
            st.markdown("</div>", unsafe_allow_html=True)

        with q2:
            st.markdown("<div class='quad'>", unsafe_allow_html=True)
            st.markdown("### 🧭 Gyro")
            g1, g2, g3 = st.columns(3)
            gyro_columns = [g1, g2, g3]
            if gyro:
                for index, (label, key) in enumerate(GYRO_FIELDS):
                    value = "—"
                    if key in gyro and gyro[key] is not None:
                        try:
                            value = f"{float(gyro[key]):.2f}"
                        except Exception:
                            value = str(gyro[key])
                    with gyro_columns[index % 3]:
                        metric_row(label, value)
            else:
                metric_row("STATUS", "Waiting for gyro data...")
            st.markdown("</div>", unsafe_allow_html=True)

        with q_status:
            st.markdown(
                f"""
                <div style="
                    height: var(--quadH);
                    display: flex;
                    align-items: flex-start;
                    justify-content: flex-start;
                    font-size: 1.6em;
                    font-weight: 700;
                    white-space: nowrap;
                    padding-top: 1rem;
                ">
                    {'🟢 POURING' if pouring else '🟡 READY'}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with st.container(height=410, border=False):
        q3, q4 = st.columns(2, gap="medium")

        with q3:
            st.markdown("<div class='quad'>", unsafe_allow_html=True)
            st.markdown("### 🪣 Bucket Schematic")
            fill_pct, ladle_img_path = snapped_ladle_image(material_pct_dev)
            if ladle_img_path.exists():
                st.image(str(ladle_img_path), width=300, caption=f"Fill Level: {fill_pct}%")
            else:
                st.warning(f"Missing image: {ladle_img_path.name}")
            st.markdown("</div>", unsafe_allow_html=True)

        with q4:
            st.markdown("<div class='quad'>", unsafe_allow_html=True)
            st.markdown("### 📷 Camera")
            if frame is not None:
                st.image(frame, width=400)
            else:
                st.info("Waiting for camera stream...")
            st.markdown("</div>", unsafe_allow_html=True)


live_dashboard(port, ladle_type, ss.pouring)



# =====================================================
# ENGINEERING MODE – ALL PARAMETERS FROM MANUAL
# =====================================================
if ss.engineer_mode:
    st.markdown("---")
    st.subheader("⚙ Engineering Parameters (Modbus)")

    # Read current values
    cur_low_adj = read_f32_be(port, REG_LOW_ADJ_F)
    cur_high_adj = read_f32_be(port, REG_HIGH_ADJ_F)
    cur_range = read_f32_be(port, REG_RANGE_F)
    cur_blind = read_f32_be(port, REG_BLIND_F)
    cur_damp = read_i32_be(port, REG_DAMPING_I32)

    st.markdown("#### Calibration / Compensation (0x2000..0x200C)")
    e1, e2, e3 = st.columns(3)

    with e1:
        low_adj = st.number_input("Low Adjust (m) 0x2000", value=float(cur_low_adj) if cur_low_adj is not None else 0.0)
        high_adj = st.number_input("High Adjust (m) 0x2004", value=float(cur_high_adj) if cur_high_adj is not None else 0.0)

    with e2:
        meas_range = st.number_input("Measuring Range (m) 0x200A", value=float(cur_range) if cur_range is not None else 18.0, min_value=0.5, max_value=120.0)
        blind_zone = st.number_input("Blind Zone (m) 0x200C", value=float(cur_blind) if cur_blind is not None else 0.25, min_value=0.05, max_value=2.0)

    with e3:
        damping_s = st.number_input("Damping Time (s) 0x2008", value=int(cur_damp) if cur_damp is not None else 1, min_value=0, max_value=120, step=1)

    if st.button("Write Calibration Parameters"):
        results = []
        results.append(write_f32_fc10_be(port, REG_LOW_ADJ_F, low_adj))
        results.append(write_f32_fc10_be(port, REG_HIGH_ADJ_F, high_adj))
        results.append(write_i32_fc10_be(port, REG_DAMPING_I32, int(damping_s)))
        results.append(write_f32_fc10_be(port, REG_RANGE_F, meas_range))
        results.append(write_f32_fc10_be(port, REG_BLIND_F, blind_zone))
        if all(ok for ok, _ in results):
            st.success("Calibration parameters written")
        else:
            st.error("Some writes failed: " + " | ".join(msg for ok, msg in results if not ok))

    # Alarm configuration
    st.markdown("#### Alarm Configuration (0x1114..0x111D)")
    cur_alarm_type = read_i16(port, REG_ALARM_TYPE_I16)
    cur_alarm_level = read_i16(port, REG_ALARM_LEVEL_I16)
    cur_single_val = read_f32_be(port, REG_SINGLE_ALARM_F)
    cur_single_rng = read_i16(port, REG_SINGLE_RANGE_I16)
    cur_dual_up = read_f32_be(port, REG_DUAL_UPPER_F)
    cur_dual_lo = read_f32_be(port, REG_DUAL_LOWER_F)
    cur_ret = read_f32_be(port, REG_RETURN_DIFF_F)

    a1, a2, a3 = st.columns(3)
    with a1:
        alarm_type = st.selectbox(
            "Alarm Type 0x1114",
            options=[1, 2],
            index=0 if cur_alarm_type == 1 else 1,
            format_func=lambda x: "Single Point" if x == 1 else "Dual Point"
        )
        alarm_level = st.selectbox(
            "Alarm Level 0x1115",
            options=[0, 1],
            index=0 if cur_alarm_level == 0 else 1,
            format_func=lambda x: "Low Level" if x == 0 else "High Level"
        )

    with a2:
        single_alarm_val = st.number_input(
            "Single Point Alarm Value (m) 0x1116",
            value=float(cur_single_val) if cur_single_val is not None else 0.0
        )
        single_alarm_dir = st.selectbox(
            "Single Alarm Range 0x1118",
            options=[0, 1],
            index=0 if cur_single_rng == 0 else 1,
            format_func=lambda x: "Upper Limit" if x == 0 else "Lower Limit"
        )

    with a3:
        dual_upper = st.number_input(
            "Dual Upper Limit (m) 0x1119",
            value=float(cur_dual_up) if cur_dual_up is not None else 0.0
        )
        dual_lower = st.number_input(
            "Dual Lower Limit (m) 0x111B",
            value=float(cur_dual_lo) if cur_dual_lo is not None else 0.0
        )
        ret_diff = st.number_input(
            "Alarm Return Difference (m) 0x111D",
            value=float(cur_ret) if cur_ret is not None else 0.10,
            min_value=0.0
        )

    if st.button("Write Alarm Configuration"):
        results = []
        results.append(write_i16_fc10(port, REG_ALARM_TYPE_I16, int(alarm_type)))
        results.append(write_i16_fc10(port, REG_ALARM_LEVEL_I16, int(alarm_level)))
        results.append(write_f32_fc10_be(port, REG_SINGLE_ALARM_F, float(single_alarm_val)))
        results.append(write_i16_fc10(port, REG_SINGLE_RANGE_I16, int(single_alarm_dir)))
        results.append(write_f32_fc10_be(port, REG_DUAL_UPPER_F, float(dual_upper)))
        results.append(write_f32_fc10_be(port, REG_DUAL_LOWER_F, float(dual_lower)))
        results.append(write_f32_fc10_be(port, REG_RETURN_DIFF_F, float(ret_diff)))

        if all(ok for ok, _ in results):
            st.success("Alarm configuration written")
        else:
            st.error("Some writes failed: " + " | ".join(msg for ok, msg in results if not ok))

    # Device control
    st.markdown("#### Device Control (Commissioning Only)")
    st.warning("Restart / slave address changes should be performed only by trained personnel.")

    dc1, dc2 = st.columns(2)
    with dc1:
        if st.button("Restart Device (0x1112)"):
            ok, msg = write_restart_word_swap_fc10(port)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    with dc2:
        cur_slave = read_i16(port, REG_SLAVE_ADDR_I16)
        new_slave = st.number_input(
            "Slave Address 0x4006",
            value=int(cur_slave) if cur_slave is not None else SLAVE_ID,
            min_value=1,
            max_value=99,
            step=1
        )
        confirm_slave = st.checkbox("Confirm slave address change")
        if st.button("Write Slave Address (FC=0x06)"):
            if not confirm_slave:
                st.error("Please confirm slave address change")
            else:
                ok, msg = write_slave_addr_fc06(port, REG_SLAVE_ADDR_I16, int(new_slave))
                if ok:
                    st.success("Slave address updated (reconnect may be needed)")
                else:
                    st.error(msg)

# =====================================================
# HISTORY + CALIBRATION MEMORY TABLES (CSV ONLY)
# =====================================================
@st.fragment(run_every="10s")
def history_tables():
    st.markdown("---")
    st.subheader("📜 Pour History (CSV)")
    history = read_csv_cached(HISTORY_FILE, file_mtime_ns(HISTORY_FILE))
    st.dataframe(history, width="stretch")

    st.markdown("---")
    st.subheader("🧠 Ladle Calibration Memory (CSV)")
    profiles = read_csv_cached(PROFILE_FILE, file_mtime_ns(PROFILE_FILE))
    st.dataframe(profiles, width="stretch")


history_tables()
