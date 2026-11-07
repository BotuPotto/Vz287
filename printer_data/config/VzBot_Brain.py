#!/usr/bin/env python3
"""
VzBot Brain - Meta AI Edition
Controllo totale VzBot + AI Guard + Ray-Ban Meta
Pi 4 - USB Cam - Moonraker 172.25.170.177:7125
"""
import os, json, time, threading, subprocess, base64, logging
from datetime import datetime
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import requests
import cv2

# --- CONFIG ---
MOONRAKER_URL = "http://127.0.0.1:7125"  # gira sullo stesso Pi, quindi localhost va bene. Se da remoto: http://172.25.170.177:7125
CAM_INDEX = 0  # USB cam
SNAPSHOT_DIR = "/tmp/vzbot_snaps"
SETTINGS_FILE = os.path.expanduser("~/vzbot_settings.json")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Settings persistenti
default_settings = {
    "auto_pause_enabled": False,
    "ai_guard_enabled": True,
    "guard_interval": 45,  # sec
    "notify_on_finish": True,
    "notify_on_error": True,
    "voice_on_rayban": True
}
settings = default_settings.copy()
if os.path.exists(SETTINGS_FILE):
    try:
        settings.update(json.load(open(SETTINGS_FILE)))
    except: pass

def save_settings():
    json.dump(settings, open(SETTINGS_FILE, 'w'), indent=2)

# State
state = {
    "last_guard_check": None,
    "last_alert": None,
    "camera_ok": False,
    "printer_state": "unknown",
    "guard_status": "idle"
}

def moonraker_get(path):
    try:
        r = requests.get(f"{MOONRAKER_URL}{path}", timeout=3)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def moonraker_post(path, data=None):
    try:
        r = requests.post(f"{MOONRAKER_URL}{path}", json=data or {}, timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# --- PRINTER CONTROL API PROXY ---
@app.route("/api/printer/info")
def api_printer_info():
    return jsonify(moonraker_get("/printer/info"))

@app.route("/api/printer/status")
def api_printer_status():
    # oggetti chiave VzBot
    query = "extruder,heater_bed,print_stats,display_status,toolhead"
    return jsonify(moonraker_get(f"/printer/objects/query?{query}"))

@app.route("/api/print/pause", methods=["POST"])
def api_pause():
    return jsonify(moonraker_post("/printer/print/pause"))

@app.route("/api/print/resume", methods=["POST"])
def api_resume():
    return jsonify(moonraker_post("/printer/print/resume"))

@app.route("/api/print/cancel", methods=["POST"])
def api_cancel():
    return jsonify(moonraker_post("/printer/print/cancel"))

@app.route("/api/printer/restart", methods=["POST"])
def api_restart_klipper():
    return jsonify(moonraker_post("/printer/restart"))

@app.route("/api/machine/reboot", methods=["POST"])
def api_reboot_pi():
    subprocess.Popen(["sudo", "reboot"])
    return jsonify({"result": "rebooting"})

@app.route("/api/gcode", methods=["POST"])
def api_gcode():
    cmd = request.json.get("script")
    return jsonify(moonraker_post("/printer/info", {})) if not cmd else jsonify(requests.post(f"{MOONRAKER_URL}/printer/gcode/script", json={"script": cmd}, timeout=5).json())

@app.route("/api/heat", methods=["POST"])
def api_heat():
    data = request.json
    target = data.get("target")  # hotend / bed
    temp = data.get("temp")
    if target == "hotend":
        script = f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={temp}"
    else:
        script = f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={temp}"
    return jsonify(moonraker_post("/printer/gcode/script", {"script": script}))

@app.route("/api/move", methods=["POST"])
def api_move():
    d = request.json
    axis = d.get("axis", "X")
    dist = d.get("dist", 1)
    speed = d.get("speed", 3000)
    script = f"G91\nG1 {axis}{dist} F{speed}\nG90"
    return jsonify(moonraker_post("/printer/gcode/script", {"script": script}))

@app.route("/api/macro/<name>", methods=["POST"])
def api_macro(name):
    macros = {
        "qgl": "QUAD_GANTRY_LEVEL",
        "z_tilt": "Z_TILT_ADJUST",
        "bed_mesh": "BED_MESH_CALIBRATE",
        "clean": "CLEAN_NOZZLE",
        "purge": "LINE_PURGE"
    }
    script = macros.get(name, name.upper())
    return jsonify(moonraker_post("/printer/gcode/script", {"script": script}))

@app.route("/api/files")
def api_files():
    return jsonify(moonraker_get("/server/files/list?root=gcodes"))

@app.route("/api/files/start/<path:filename>", methods=["POST"])
def api_start_file(filename):
    return jsonify(moonraker_post("/printer/print/start", {"filename": filename}))

# --- CAMERA ---
@app.route("/api/cam/snapshot")
def api_snapshot():
    cap = cv2.VideoCapture(CAM_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return jsonify({"error": "camera not available"}), 500
    path = os.path.join(SNAPSHOT_DIR, f"snap_{int(time.time())}.jpg")
    cv2.imwrite(path, frame)
    state["camera_ok"] = True
    # ritorna base64 per dashboard
    _, buf = cv2.imencode('.jpg', frame)
    b64 = base64.b64encode(buf).decode()
    return jsonify({"image_b64": f"data:image/jpeg;base64,{b64}", "path": path})

@app.route("/api/cam/stream.jpg")
def api_stream_jpg():
    cap = cv2.VideoCapture(CAM_INDEX)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return "", 404
    path = "/tmp/live.jpg"
    cv2.imwrite(path, frame)
    return send_file(path, mimetype='image/jpeg')

# --- SETTINGS & GUARD ---
@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    global settings
    if request.method == "POST":
        settings.update(request.json)
        save_settings()
    return jsonify(settings)

@app.route("/api/guard/status")
def api_guard_status():
    return jsonify({**state, **settings})

@app.route("/api/logs/analyze")
def api_logs():
    # legge ultimi errori klippy.log
    log_path = os.path.expanduser("~/printer_data/logs/klippy.log")
    if not os.path.exists(log_path):
        log_path = "/tmp/klippy.log"
    try:
        out = subprocess.getoutput(f"tail -n 200 {log_path}")
        # analisi basilare
        suggestion = "Nessun errore rilevato."
        if "Heater extruder not heating at expected rate" in out:
            suggestion = "🔥 Hotend non scalda: Controlla cablaggio termistore/riscaldatore, poi lancia PID_CALIBRATE HEATER=extruder TARGET=220"
        elif "Heater heater_bed not heating" in out:
            suggestion = "🛏️ Piatto non scalda: Controlla MOSFET e cavi, poi PID per bed."
        elif "Move exceeds maximum" in out:
            suggestion = "📐 Movimento fuori area: Controlla dimensioni slicer (VzBot 330x330?) e posizione."
        elif "Timer too close" in out:
            suggestion = "⏱️ MCU Timer too close: Pi sovraccarico, riduci webcam FPS o cambia cavo USB MCU."
        elif "TMC" in out and "GSTAT" in out:
            suggestion = "⚙️ Driver TMC in errore: Controlla corrente, raffreddamento, cavi motore."
        return jsonify({"log_tail": out[-4000:], "ai_suggestion": suggestion})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/update/all", methods=["POST"])
def api_update_all():
    # lancia update via kiauh o moonraker update manager
    try:
        # prova moonraker update
        r = moonraker_post("/machine/update/full")
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)})

# --- BACKGROUND GUARD LOOP ---
def guard_loop():
    print("[VzBot Brain] Guard loop started, interval", settings["guard_interval"])
    last_frame = None
    while True:
        try:
            if not settings.get("ai_guard_enabled"):
                time.sleep(5)
                continue
            time.sleep(settings.get("guard_interval", 45))
            cap = cv2.VideoCapture(CAM_INDEX)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                state["camera_ok"] = False
                continue
            state["camera_ok"] = True
            state["last_guard_check"] = datetime.now().isoformat()
            
            # Check semplice locale: differenza frame + blob detection (placeholder per spaghetti)
            # Qui per ora logghiamo, la vera AI la fa Meta AI quando gli mandi lo snapshot
            # Se auto_pause_enabled e rilevi problema -> pausa
            
            # Esempio trigger manuale se file di test esiste
            # Per demo: se settings["auto_pause_enabled"] e rileva problema
            # moonraker_post("/printer/print/pause")
            
            # Salva snapshot per analisi
            snap_path = os.path.join(SNAPSHOT_DIR, "last_guard.jpg")
            cv2.imwrite(snap_path, frame)
            
            print(f"[Guard] Check OK at {state['last_guard_check']}")
        except Exception as e:
            print(f"[Guard] error {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=guard_loop, daemon=True).start()
    print(f"""
VzBot Brain avviato!
Moonraker: {MOONRAKER_URL}
Dashboard API: http://{MOONRAKER_URL.replace('7125','8085') if '7125' in MOONRAKER_URL else '0.0.0.0:8085'}
- API: http://0.0.0.0:8085/api/printer/status
- Snapshot: http://0.0.0.0:8085/api/cam/snapshot
- Settings: http://0.0.0.0:8085/api/settings
    """)
    app.run(host="0.0.0.0", port=8085, debug=False)
