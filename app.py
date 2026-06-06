import os, sqlite3, requests, json, concurrent.futures, ipaddress, time, logging
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from requests.auth import HTTPDigestAuth
from werkzeug.security import generate_password_hash, check_password_hash

# Logging fuer Docker konfigurieren
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'bitte-in-.env-setzen')
DB_PATH = '/app/data/users.db'

# GitHub API URL fuer das offizielle Mongoose Repository
GITHUB_RELEASE_URL = "https://api.github.com/repos/mongoose-os-apps/shelly-homekit/releases/latest"

# Globaler Cache fuer die Firmware-Version
cache = {
    "latest_version": None,
    "last_check": 0
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS devices (ip TEXT PRIMARY KEY, name TEXT, position INTEGER DEFAULT 0)')
    
    cursor = conn.execute('PRAGMA table_info(devices)')
    columns = [column[1] for column in cursor.fetchall()]
    if 'position' not in columns:
        try:
            conn.execute('ALTER TABLE devices ADD COLUMN position INTEGER DEFAULT 0')
        except Exception as e:
            logging.warning(f"ALTER TABLE fehlgeschlagen: {e}")

    if not conn.execute('SELECT * FROM users WHERE username = "admin"').fetchone():
        conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', ('admin', generate_password_hash('admin')))
    
    defaults = {"shelly_password": "", "ip_range": "192.168.11.2-192.168.11.12"}
    for key, value in defaults.items():
        if not conn.execute('SELECT * FROM config WHERE key = ?', (key,)).fetchone():
            conn.execute('INSERT INTO config (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

init_db()

def get_config(key):
    conn = get_db()
    row = conn.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else ""

def format_uptime(seconds):
    """Formatiert Sekunden in XXJ, XXM, XXT, XXStd, XXMin:XXSek."""
    if not seconds or seconds < 0: return "0s"
    
    y = seconds // (3600 * 24 * 365)
    seconds %= (3600 * 24 * 365)
    mo = seconds // (3600 * 24 * 30)
    seconds %= (3600 * 24 * 30)
    d = seconds // (3600 * 24)
    seconds %= (3600 * 24)
    h = seconds // 3600
    seconds %= 3600
    m = seconds // 60
    s = seconds % 60
    
    parts = []
    if y > 0: parts.append(f"{int(y)}J")
    if mo > 0: parts.append(f"{int(mo)}M")
    if d > 0: parts.append(f"{int(d)}T")
    if h > 0: parts.append(f"{int(h)}Std")
    
    time_str = f"{int(m):02d}:{int(s):02d}"
    if parts:
        return ", ".join(parts) + ", " + time_str
    return time_str

def get_latest_version():
    """Holt die Version von GitHub mit Cache und Debug-Option."""
    current_time = time.time()
    ip_range_cfg = get_config("ip_range")
    
    if "DEBUG_UPDATE" in ip_range_cfg:
        return "9.9.9"

    if cache["latest_version"] and (current_time - cache["last_check"] < 3600):
        return cache["latest_version"]

    try:
        headers = {'User-Agent': 'Shelly-Manager-App'}
        res = requests.get(GITHUB_RELEASE_URL, headers=headers, timeout=10)
        if res.status_code == 200:
            version = res.json().get("tag_name")
            if version:
                cache["latest_version"] = version
                cache["last_check"] = current_time
                return version
    except Exception as e:
        logging.error(f"GitHub Fehler: {str(e)}")
    
    return cache["latest_version"]

def get_shelly_status(args):
    ip, db_name, latest_v = args
    pwd = get_config("shelly_password")
    device_data = {"ip": ip, "name": db_name, "status": "Offline", "version": "-", "ison": False, "uptime": 0, "uptime_str": "-", "fw_ok": True}
    try:
        res = requests.get(f"http://{ip}/rpc/Shelly.GetInfoExt", auth=HTTPDigestAuth('admin', pwd), timeout=2.0)
        if res.status_code == 200:
            d = res.json()
            ver = d.get("version", "-")
            uptime_secs = d.get("uptime", 0)
            device_data.update({
                "status": "Online",
                "version": ver,
                "uptime": uptime_secs,
                "uptime_str": format_uptime(uptime_secs),
                "rssi": d.get("wifi_conn_rssi", 0),
                "fw_ok": (ver == latest_v if latest_v else True)
            })
            for comp in d.get("components", []):
                if comp.get("type") == 0:
                    device_data["ison"] = not comp.get("state", False)
                    break
    except Exception as e:
        logging.warning(f"Fehler beim Abrufen von {ip}: {e}")
    return device_data

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user, pw = request.form.get('username'), request.form.get('password')
        conn = get_db()
        row = conn.execute('SELECT password FROM users WHERE username = ?', (user,)).fetchone()
        conn.close()
        if row and check_password_hash(row['password'], pw):
            session['logged_in'] = True
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/')
def index():
    if not session.get('logged_in'): return redirect(url_for('login'))
    latest_v = get_latest_version()
    conn = get_db()
    db_devices = conn.execute('SELECT ip, name FROM devices ORDER BY position ASC').fetchall()
    conn.close()
    task_data = [(d['ip'], d['name'], latest_v) for d in db_devices]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        devices = list(executor.map(get_shelly_status, task_data))
    return render_template('index.html', devices=devices, latest_v=latest_v)

@app.route('/update_device', methods=['POST'])
def update_device():
    if not session.get('logged_in'): return jsonify({"success": False}), 403
    ip = request.json.get('ip')
    pwd = get_config("shelly_password")
    try:
        res_info = requests.get(f"http://{ip}/rpc/Shelly.GetInfoExt", auth=HTTPDigestAuth('admin', pwd), timeout=3)
        if res_info.status_code != 200: return jsonify({"success": False, "error": "Offline"})
        
        model = res_info.json().get("model", "")
        latest_v = get_latest_version()
        ota_url = f"http://shelly.rojer.cloud/update/shelly-homekit-{model}.zip"
        res_update = requests.get(f"http://{ip}/ota", params={"url": ota_url}, auth=HTTPDigestAuth('admin', pwd), timeout=5)
        return jsonify({"success": res_update.status_code == 200})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/scan', methods=['POST'])
def scan():
    if not session.get('logged_in'): return jsonify({"success": False}), 403
    raw_range = get_config("ip_range")
    ip_input = raw_range.replace("DEBUG_UPDATE", "").strip()
    all_ips = []
    try:
        if '/' in ip_input:
            network = ipaddress.ip_network(ip_input, strict=False)
            all_ips = [str(ip) for ip in network.hosts()]
        elif '-' in ip_input:
            parts = ip_input.split('-')
            start_ip = ipaddress.IPv4Address(parts[0].strip())
            end_ip = ipaddress.IPv4Address(parts[1].strip()) if '.' in parts[1] else ipaddress.IPv4Address(f"{'.'.join(parts[0].split('.')[:-1])}.{parts[1].strip()}")
            for ip_int in range(int(start_ip), int(end_ip) + 1):
                all_ips.append(str(ipaddress.IPv4Address(ip_int)))
    except Exception as e:
        logging.error(f"Ungültiger IP-Bereich '{ip_input}': {e}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        found = [r for r in list(executor.map(discover_shelly, all_ips)) if r is not None]
    conn = get_db()
    for dev in found: conn.execute('INSERT OR IGNORE INTO devices (ip, name) VALUES (?, ?)', (dev['ip'], dev['name']))
    conn.commit(); conn.close()
    return jsonify({"success": True, "count": len(found)})

def discover_shelly(ip):
    pwd = get_config("shelly_password")
    try:
        res = requests.get(f"http://{ip}/rpc/Shelly.GetInfoExt", auth=HTTPDigestAuth('admin', pwd), timeout=1.5)
        if res.status_code == 200:
            return {"ip": ip, "name": res.json().get("name") or f"Shelly-{ip.split('.')[-1]}"}
    except Exception as e:
        logging.debug(f"Kein Shelly auf {ip}: {e}")
    return None

@app.route('/update_order', methods=['POST'])
def update_order():
    if not session.get('logged_in'): return jsonify({"success": False}), 403
    order = request.json.get('order', [])
    conn = get_db()
    for index, ip in enumerate(order): conn.execute('UPDATE devices SET position = ? WHERE ip = ?', (index, ip))
    conn.commit(); conn.close()
    return jsonify({"success": True})

@app.route('/control', methods=['POST'])
def control():
    if not session.get('logged_in'): return jsonify({"success": False}), 403
    data = request.json
    ip, action, val = data.get('ip'), data.get('action'), data.get('value')
    pwd = get_config("shelly_password")
    try:
        if action == "reboot":
            requests.post(f"http://{ip}/rpc/Sys.Reboot", data=json.dumps({"delay_ms": 500}), auth=HTTPDigestAuth('admin', pwd), timeout=5)
            return jsonify({"success": True})
        if action == "state":
            new_hardware_state = False if val == 'on' else True
            for target_id in [0, 1]:
                url = f"http://{ip}/rpc/Shelly.SetState"
                payload = {"id": target_id, "type": 0, "state": {"state": new_hardware_state}}
                res = requests.post(url, json=payload, auth=HTTPDigestAuth('admin', pwd), timeout=4)
                if res.status_code == 200 and "component not found" not in res.text: return jsonify({"success": True})
    except Exception as e:
        logging.warning(f"Steuerbefehl fehlgeschlagen ({ip}, {action}): {e}")
    return jsonify({"success": False})

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('logged_in'): return redirect(url_for('login'))
    message = ""
    if request.method == 'POST':
        conn = get_db()
        if request.form.get('admin_password'): conn.execute('UPDATE users SET password = ? WHERE username = "admin"', (generate_password_hash(request.form['admin_password']),))
        conn.execute('UPDATE config SET value = ? WHERE key = "shelly_password"', (request.form.get('shelly_password'),))
        conn.execute('UPDATE config SET value = ? WHERE key = "ip_range"', (request.form.get('ip_range'),))
        conn.commit(); conn.close()
        message = "Einstellungen gespeichert!"
    return render_template('settings.html', message=message, shelly_pwd=get_config("shelly_password"), ip_range=get_config("ip_range"))

@app.route('/logout')
def logout():
    session.pop('logged_in', None); return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
