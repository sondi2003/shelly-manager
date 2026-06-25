import os, sqlite3, requests, json, concurrent.futures, ipaddress, time, logging, socket, secrets, threading
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from requests.auth import HTTPDigestAuth
from werkzeug.security import generate_password_hash, check_password_hash

# Logging MUSS vor dem ersten logging-Aufruf (z.B. zeroconf-Warnung unten) konfiguriert
# werden, sonst richtet der erste Aufruf den Root-Logger implizit auf Level WARNING ein
# und basicConfig wird zum No-Op -> alle info()-Logs (z.B. [OTA] ...) verschwinden.
# force=True überschreibt einen bereits vorhandenen Handler.
logging.basicConfig(level=logging.INFO,
                    format='[%(asctime)s] %(levelname)s: %(message)s',
                    force=True)

try:
    from zeroconf import ServiceBrowser, Zeroconf
    MDNS_AVAILABLE = True
except ImportError:
    MDNS_AVAILABLE = False
    logging.warning("zeroconf nicht installiert — mDNS-Scan nicht verfügbar.")

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'bitte-in-.env-setzen')
DB_PATH = '/app/data/users.db'

GITHUB_RELEASE_URL = "https://api.github.com/repos/mongoose-os-apps/shelly-homekit/releases/latest"
# Firmware-Download-Quelle (modell-spezifisch, latest stable). Wir laden die Zip selbst
# herunter und laden sie dann ans Gerät hoch – daher hier NICHT der Universal-Link
# (der erkennt das Gerät an dessen eigenen Request-Headern, die wir nicht haben).
OTA_FW_URL_TEMPLATE = "http://rojer.me/files/shelly/shelly-homekit-{model}.zip"
cache = {"latest_version": None, "last_check": 0}

# Fortschritt laufender Firmware-Updates, je Geräte-IP. Wird vom Hintergrund-Thread
# geschrieben und vom Frontend per /update_status gepollt (Fortschrittsbalken in der Kachel).
update_progress = {}
progress_lock   = threading.Lock()

def set_progress(ip, percent, stage, done=False, success=None, error=None):
    now = time.time()
    with progress_lock:
        update_progress[ip] = {
            "percent": percent, "stage": stage,
            "done": done, "success": success, "error": error, "ts": now,
        }
        # Alte, abgeschlossene Einträge aufräumen, damit der Speicher nicht wächst.
        for old in [k for k, v in update_progress.items()
                    if v.get("done") and now - v.get("ts", 0) > 300]:
            del update_progress[old]

# ──────────────────────────────────────────────────────────────
# DATENBANK
# ──────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()

    conn.execute('CREATE TABLE IF NOT EXISTS users   (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS config  (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS devices (ip TEXT PRIMARY KEY, name TEXT, position INTEGER DEFAULT 0, group_name TEXT DEFAULT "")')
    conn.execute('''CREATE TABLE IF NOT EXISTS tokens (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        token      TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL
    )''')

    # Migrationen für ältere Installationen
    cursor = conn.execute('PRAGMA table_info(devices)')
    columns = [c[1] for c in cursor.fetchall()]
    for col, definition in [('position', 'INTEGER DEFAULT 0'), ('group_name', 'TEXT DEFAULT ""')]:
        if col not in columns:
            try:
                conn.execute(f'ALTER TABLE devices ADD COLUMN {col} {definition}')
            except Exception as e:
                logging.warning(f"Migration '{col}' fehlgeschlagen: {e}")

    # Standard-Config (nur wenn noch nicht vorhanden)
    defaults = {
        "shelly_password": "",
        "ip_range":        "192.168.1.1-192.168.1.254",
        "scan_mode":       "ip",
        "first_run":       "true",
    }
    for key, value in defaults.items():
        if not conn.execute('SELECT 1 FROM config WHERE key = ?', (key,)).fetchone():
            conn.execute('INSERT INTO config (key, value) VALUES (?, ?)', (key, value))

    # Env-Var ADMIN_PASSWORD: Admin direkt beim Start setzen/überschreiben
    env_pw = os.environ.get('ADMIN_PASSWORD', '').strip()
    if env_pw:
        hashed = generate_password_hash(env_pw)
        existing = conn.execute('SELECT id FROM users WHERE username = "admin"').fetchone()
        if existing:
            conn.execute('UPDATE users SET password = ? WHERE username = "admin"', (hashed,))
        else:
            conn.execute('INSERT INTO users (username, password) VALUES ("admin", ?)', (hashed,))
        conn.execute('UPDATE config SET value = "false" WHERE key = "first_run"')
        logging.info("Admin-Passwort aus ADMIN_PASSWORD Umgebungsvariable gesetzt.")

    conn.commit()
    conn.close()

init_db()

def get_config(key):
    conn = get_db()
    row = conn.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else ""

def is_first_run():
    return get_config('first_run') == 'true'

# ──────────────────────────────────────────────────────────────
# AUTH — Session ODER API-Token
# ──────────────────────────────────────────────────────────────

def check_token(token_value):
    """Prüft ob ein Token gültig ist. Gibt True/False zurück."""
    conn = get_db()
    row = conn.execute('SELECT id FROM tokens WHERE token = ?', (token_value,)).fetchone()
    conn.close()
    return row is not None

def require_auth(f):
    """Decorator: erlaubt Zugriff per Session-Login ODER API-Token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('logged_in'):
            return f(*args, **kwargs)
        token = request.headers.get('X-API-Token') or request.args.get('api_token')
        if token and check_token(token):
            return f(*args, **kwargs)
        if request.is_json or request.headers.get('X-API-Token'):
            return jsonify({"error": "Nicht autorisiert — Session oder gültiger X-API-Token erforderlich"}), 401
        return redirect(url_for('login'))
    return decorated

# ──────────────────────────────────────────────────────────────
# HILFSFUNKTIONEN
# ──────────────────────────────────────────────────────────────

def format_uptime(seconds):
    if not seconds or seconds < 0: return "0s"
    y  = seconds // (3600 * 24 * 365); seconds %= (3600 * 24 * 365)
    mo = seconds // (3600 * 24 * 30);  seconds %= (3600 * 24 * 30)
    d  = seconds // (3600 * 24);       seconds %= (3600 * 24)
    h  = seconds // 3600;              seconds %= 3600
    m  = seconds // 60;                s = seconds % 60
    parts = []
    if y  > 0: parts.append(f"{int(y)}J")
    if mo > 0: parts.append(f"{int(mo)}M")
    if d  > 0: parts.append(f"{int(d)}T")
    if h  > 0: parts.append(f"{int(h)}Std")
    return ", ".join(parts) + (", " if parts else "") + f"{int(m):02d}:{int(s):02d}"

def get_latest_version():
    current_time = time.time()
    if "DEBUG_UPDATE" in get_config("ip_range"):
        return "9.9.9"
    if cache["latest_version"] and (current_time - cache["last_check"] < 3600):
        return cache["latest_version"]
    try:
        res = requests.get(GITHUB_RELEASE_URL, headers={'User-Agent': 'Shelly-Manager-App'}, timeout=10)
        if res.status_code == 200:
            version = res.json().get("tag_name")
            if version:
                cache["latest_version"] = version
                cache["last_check"] = current_time
                return version
    except Exception as e:
        logging.error(f"GitHub Fehler: {e}")
    return cache["latest_version"]

def get_shelly_status(args):
    ip, db_name, latest_v, group_name = args
    pwd = get_config("shelly_password")
    device_data = {
        "ip": ip, "name": db_name, "group_name": group_name,
        "status": "Offline", "version": "-", "ison": False,
        "uptime": 0, "uptime_str": "-", "fw_ok": True,
        "has_meter": False, "power_w": None, "voltage": None, "current": None,
        "model": "", "rssi": 0,
    }
    try:
        res = requests.get(f"http://{ip}/rpc/Shelly.GetInfoExt",
                           auth=HTTPDigestAuth('admin', pwd), timeout=2.0)
        if res.status_code == 200:
            d = res.json()
            ver = d.get("version", "-")
            uptime_secs = d.get("uptime", 0)
            device_data.update({
                "status":     "Online",
                "version":    ver,
                "uptime":     uptime_secs,
                "uptime_str": format_uptime(uptime_secs),
                "rssi":       d.get("wifi_conn_rssi", 0),
                "fw_ok":      (ver == latest_v if latest_v else True),
                "model":      d.get("model", ""),
            })
            for comp in d.get("components", []):
                # Shelly.GetInfoExt liefert die Switch-Felder FLACH auf der Komponente:
                # {"id":0,"type":0,"state":true,"apower":..,"voltage":..,"current":..}.
                # 'state' ist ein Boolean (true = Relais EIN), KEIN verschachteltes Objekt.
                # Defensiv unterstützen wir trotzdem beide Formen (flach + alt verschachtelt).
                st = comp.get("state")
                nested = st if isinstance(st, dict) else {}

                # kSwitch (0) / kOutlet (1) = schaltbares Relais
                if comp.get("type") in (0, 1):
                    if isinstance(st, bool):
                        device_data["ison"] = st
                    elif nested:
                        device_data["ison"] = bool(nested.get("state", False))

                # Leistungswerte: je nach Firmware flach auf comp oder im state-Objekt
                def pick(key):
                    v = comp.get(key)
                    return v if v is not None else nested.get(key)

                power = pick("apower")
                if power is None:
                    power = pick("power")
                if power is not None:
                    voltage = pick("voltage")
                    current = pick("current")
                    device_data["power_w"]   = round(float(power), 1)
                    device_data["voltage"]   = round(float(voltage), 1) if voltage is not None else None
                    device_data["current"]   = round(float(current), 2) if current is not None else None
                    device_data["has_meter"] = True
    except Exception as e:
        logging.warning(f"Fehler beim Abrufen von {ip}: {e}")
    return device_data

def discover_shelly(ip):
    pwd = get_config("shelly_password")
    try:
        res = requests.get(f"http://{ip}/rpc/Shelly.GetInfoExt",
                           auth=HTTPDigestAuth('admin', pwd), timeout=1.5)
        if res.status_code == 200:
            return {"ip": ip, "name": res.json().get("name") or f"Shelly-{ip.split('.')[-1]}"}
    except Exception as e:
        logging.debug(f"Kein Shelly auf {ip}: {e}")
    return None

def discover_mdns(timeout=6):
    if not MDNS_AVAILABLE:
        logging.error("mDNS nicht verfügbar: zeroconf nicht installiert.")
        return []
    found_ips, found = set(), []
    class ShellyListener:
        def add_service(self, zc, type_, name):
            try:
                info = zc.get_service_info(type_, name)
                if not info or not info.addresses: return
                ip = socket.inet_ntoa(info.addresses[0])
                if ip in found_ips: return
                found_ips.add(ip)
                result = discover_shelly(ip)
                if result:
                    found.append(result)
                    logging.info(f"mDNS: Shelly gefunden auf {ip}")
            except Exception as e:
                logging.debug(f"mDNS Listener Fehler: {e}")
        def remove_service(self, zc, type_, name): pass
        def update_service(self, zc, type_, name): pass
    zc = Zeroconf()
    browsers = [ServiceBrowser(zc, "_hap._tcp.local.", ShellyListener()),
                ServiceBrowser(zc, "_http._tcp.local.", ShellyListener())]
    time.sleep(timeout)
    zc.close()
    return found

def grouped_devices(latest_v):
    conn = get_db()
    db_devices = conn.execute(
        'SELECT ip, name, group_name FROM devices ORDER BY group_name ASC, position ASC'
    ).fetchall()
    conn.close()
    task_data = [(d['ip'], d['name'], latest_v, d['group_name'] or '') for d in db_devices]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        devices = list(executor.map(get_shelly_status, task_data))
    groups = {}
    for dev in devices:
        g = dev['group_name'] or ''
        groups.setdefault(g, []).append(dev)
    sorted_groups = sorted(groups.items(), key=lambda x: (x[0] == '', x[0].lower()))
    return devices, sorted_groups

# ──────────────────────────────────────────────────────────────
# ERSTEINRICHTUNG
# ──────────────────────────────────────────────────────────────

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if not is_first_run():
        return redirect(url_for('index'))
    error = ""
    if request.method == 'POST':
        pw  = request.form.get('password', '').strip()
        pw2 = request.form.get('password2', '').strip()
        ip_range       = request.form.get('ip_range', '').strip()
        shelly_password = request.form.get('shelly_password', '').strip()
        if not pw:
            error = "Bitte ein Passwort eingeben."
        elif pw != pw2:
            error = "Passwörter stimmen nicht überein."
        elif len(pw) < 6:
            error = "Passwort muss mindestens 6 Zeichen haben."
        else:
            conn = get_db()
            existing = conn.execute('SELECT id FROM users WHERE username = "admin"').fetchone()
            if existing:
                conn.execute('UPDATE users SET password = ? WHERE username = "admin"',
                             (generate_password_hash(pw),))
            else:
                conn.execute('INSERT INTO users (username, password) VALUES ("admin", ?)',
                             (generate_password_hash(pw),))
            conn.execute('UPDATE config SET value = ? WHERE key = "ip_range"', (ip_range,))
            conn.execute('UPDATE config SET value = ? WHERE key = "shelly_password"', (shelly_password,))
            conn.execute('UPDATE config SET value = "false" WHERE key = "first_run"')
            conn.commit()
            conn.close()
            logging.info("Ersteinrichtung abgeschlossen.")
            return redirect(url_for('login'))
    return render_template('setup.html', error=error,
                           default_ip_range=get_config('ip_range'))

# ──────────────────────────────────────────────────────────────
# HAUPTSEITEN
# ──────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_first_run():
        return redirect(url_for('setup'))
    if request.method == 'POST':
        user = request.form.get('username')
        pw   = request.form.get('password')
        conn = get_db()
        row  = conn.execute('SELECT password FROM users WHERE username = ?', (user,)).fetchone()
        conn.close()
        if row and check_password_hash(row['password'], pw):
            session['logged_in'] = True
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/')
@require_auth
def index():
    if is_first_run():
        return redirect(url_for('setup'))
    latest_v = get_latest_version()
    devices, sorted_groups = grouped_devices(latest_v)
    return render_template('index.html', devices=devices, sorted_groups=sorted_groups, latest_v=latest_v)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# ──────────────────────────────────────────────────────────────
# GERÄTE-AKTIONEN
# ──────────────────────────────────────────────────────────────

@app.route('/status')
@require_auth
def status():
    latest_v = get_latest_version()
    devices, _ = grouped_devices(latest_v)
    return jsonify({"devices": devices, "latest_v": latest_v})

@app.route('/control', methods=['POST'])
@require_auth
def control():
    data   = request.json
    ip     = data.get('ip')
    action = data.get('action')
    val    = data.get('value')
    pwd    = get_config("shelly_password")
    try:
        if action == "reboot":
            requests.post(f"http://{ip}/rpc/Sys.Reboot",
                          data=json.dumps({"delay_ms": 500}),
                          auth=HTTPDigestAuth('admin', pwd), timeout=5)
            return jsonify({"success": True})
        if action == "state":
            new_state = (val == 'on')   # Firmware: state=true bedeutet EIN
            for target_id in [0, 1]:
                res = requests.post(f"http://{ip}/rpc/Shelly.SetState",
                                    json={"id": target_id, "type": 0, "state": {"state": new_state}},
                                    auth=HTTPDigestAuth('admin', pwd), timeout=4)
                logging.info(f"[CTRL] {ip} SetState id={target_id} state={new_state} "
                             f"-> HTTP {res.status_code}: {res.text[:120]!r}")
                if res.status_code == 200 and "component not found" not in res.text:
                    return jsonify({"success": True})
    except Exception as e:
        logging.warning(f"Steuerbefehl fehlgeschlagen ({ip}, {action}): {e}")
    return jsonify({"success": False})

@app.route('/control_group', methods=['POST'])
@require_auth
def control_group():
    group  = request.json.get('group', '')
    action = request.json.get('action')
    pwd    = get_config("shelly_password")
    conn   = get_db()
    db_devices = conn.execute('SELECT ip FROM devices WHERE group_name = ?', (group,)).fetchall()
    conn.close()
    new_state = (action == 'on')   # Firmware: state=true bedeutet EIN
    results = []
    for d in db_devices:
        ip = d['ip']
        for target_id in [0, 1]:
            try:
                res = requests.post(f"http://{ip}/rpc/Shelly.SetState",
                                    json={"id": target_id, "type": 0, "state": {"state": new_state}},
                                    auth=HTTPDigestAuth('admin', pwd), timeout=4)
                if res.status_code == 200 and "component not found" not in res.text:
                    results.append(ip)
                    break
            except Exception as e:
                logging.warning(f"Gruppensteuerung fehlgeschlagen für {ip}: {e}")
    return jsonify({"success": True, "count": len(results)})

@app.route('/scan', methods=['POST'])
@require_auth
def scan():
    scan_mode = get_config("scan_mode")
    if scan_mode == "mdns":
        logging.info("Starte mDNS-Scan...")
        found = discover_mdns(timeout=6)
    else:
        logging.info("Starte IP-Range-Scan...")
        raw_range = get_config("ip_range")
        ip_input  = raw_range.replace("DEBUG_UPDATE", "").strip()
        all_ips   = []
        try:
            if '/' in ip_input:
                all_ips = [str(ip) for ip in ipaddress.ip_network(ip_input, strict=False).hosts()]
            elif '-' in ip_input:
                parts    = ip_input.split('-')
                start_ip = ipaddress.IPv4Address(parts[0].strip())
                end_part = parts[1].strip()
                end_ip   = ipaddress.IPv4Address(end_part) if '.' in end_part \
                           else ipaddress.IPv4Address(f"{'.'.join(parts[0].split('.')[:-1])}.{end_part}")
                for ip_int in range(int(start_ip), int(end_ip) + 1):
                    all_ips.append(str(ipaddress.IPv4Address(ip_int)))
        except Exception as e:
            logging.error(f"Ungültiger IP-Bereich '{ip_input}': {e}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            found = [r for r in executor.map(discover_shelly, all_ips) if r is not None]
    conn = get_db()
    for dev in found:
        conn.execute('INSERT OR IGNORE INTO devices (ip, name) VALUES (?, ?)', (dev['ip'], dev['name']))
    conn.commit()
    conn.close()
    logging.info(f"Scan abgeschlossen: {len(found)} Gerät(e) gefunden.")
    return jsonify({"success": True, "count": len(found), "mode": scan_mode})

def flash_shelly(ip, pwd):
    """Lädt die passende Firmware-Zip herunter und lädt sie ans Gerät hoch –
    exakt wie es die Firmware-Weboberfläche selbst macht:
      1. Modell per Shelly.GetInfoExt ermitteln
      2. Zip von rojer.me herunterladen
      3. Zip per multipart POST an http://<ip>/update hochladen (Feld 'file')
    Gibt (success: bool, message: str) zurück.
    """
    auth = HTTPDigestAuth('admin', pwd)
    # 1) Modell ermitteln
    set_progress(ip, 10, "Ermittle Modell…")
    res_info = requests.get(f"http://{ip}/rpc/Shelly.GetInfoExt", auth=auth, timeout=3)
    logging.info(f"[OTA] {ip} GetInfoExt -> HTTP {res_info.status_code}")
    if res_info.status_code != 200:
        return False, f"Gerät nicht erreichbar / Auth fehlgeschlagen (HTTP {res_info.status_code})"
    info  = res_info.json()
    model = info.get("model", "")
    logging.info(f"[OTA] {ip} model={model!r} version={info.get('version')!r}")
    if not model:
        return False, "Modell konnte nicht ermittelt werden"

    # 2) Firmware-Zip herunterladen
    fw_url = OTA_FW_URL_TEMPLATE.format(model=model)
    set_progress(ip, 25, f"Lade Firmware ({model})…")
    logging.info(f"[OTA] {ip} lade Firmware {fw_url}")
    res_fw = requests.get(fw_url, timeout=30)
    if res_fw.status_code != 200 or not res_fw.content:
        return False, f"Firmware-Download fehlgeschlagen (HTTP {res_fw.status_code}) von {fw_url}"
    logging.info(f"[OTA] {ip} Firmware geladen: {len(res_fw.content)} Bytes")
    set_progress(ip, 45, "Firmware geladen")

    # 3) Zip ans Gerät hochladen (das löst Flash + Reboot aus)
    set_progress(ip, 55, "Lade auf Gerät hoch…")
    logging.info(f"[OTA] {ip} lade Firmware hoch -> POST http://{ip}/update")
    files  = {"file": (f"shelly-homekit-{model}.zip", res_fw.content, "application/zip")}
    res_up = requests.post(f"http://{ip}/update", files=files, auth=auth, timeout=120)
    body = (res_up.text or "").strip()
    logging.info(f"[OTA] {ip} /update -> HTTP {res_up.status_code}; Antwort: {body[:300]!r}")
    if res_up.status_code != 200:
        return False, f"Upload abgelehnt (HTTP {res_up.status_code}): {body[:200]}"
    set_progress(ip, 85, "Geflasht – Gerät startet neu…")
    return True, body[:200] or "Update gestartet"

def wait_for_reboot(ip, pwd, latest, timeout=120):
    """Wartet, bis das Gerät nach dem Flash wieder erreichbar ist, und treibt den
    Balken währenddessen von 88% auf 99%. Gibt die erkannte Version zurück (oder None)."""
    auth = HTTPDigestAuth('admin', pwd)
    start, attempt = time.time(), 0
    while time.time() - start < timeout:
        attempt += 1
        pct = min(99, 88 + attempt)
        try:
            r = requests.get(f"http://{ip}/rpc/Shelly.GetInfoExt", auth=auth, timeout=3)
            if r.status_code == 200:
                ver = r.json().get("version", "")
                if latest and ver == latest:
                    return ver
                set_progress(ip, pct, f"Neustart… (v{ver})")
            else:
                set_progress(ip, pct, "Gerät startet neu…")
        except Exception:
            set_progress(ip, pct, "Gerät startet neu…")
        time.sleep(3)
    # Letzter Versuch, die Version zu lesen
    try:
        r = requests.get(f"http://{ip}/rpc/Shelly.GetInfoExt", auth=auth, timeout=3)
        if r.status_code == 200:
            return r.json().get("version", "")
    except Exception:
        pass
    return None

def update_worker(ip, pwd):
    """Hintergrund-Job: führt das Update aus und schreibt den Fortschritt nach update_progress."""
    logging.info(f"[OTA] === Update-Worker gestartet für {ip} ===")
    set_progress(ip, 5, "Starte…")
    try:
        success, message = flash_shelly(ip, pwd)
    except Exception as e:
        logging.error(f"[OTA] {ip} Ausnahme: {type(e).__name__}: {e}")
        set_progress(ip, 0, "Fehler", done=True, success=False, error=f"{type(e).__name__}: {e}")
        return
    if not success:
        logging.warning(f"[OTA] {ip} fehlgeschlagen: {message}")
        set_progress(ip, 0, "Fehlgeschlagen", done=True, success=False, error=message)
        return
    ver = wait_for_reboot(ip, pwd, get_latest_version())
    logging.info(f"[OTA] {ip} OK – neue Version: {ver}")
    set_progress(ip, 100, f"Fertig – v{ver}" if ver else "Fertig", done=True, success=True)

def start_update(ip, pwd):
    """Startet den Update-Thread für ip, falls nicht bereits eines läuft. Gibt True zurück, wenn gestartet."""
    with progress_lock:
        running = ip in update_progress and not update_progress[ip].get("done")
    if running:
        return False
    set_progress(ip, 0, "In Warteschlange…")
    threading.Thread(target=update_worker, args=(ip, pwd), daemon=True).start()
    return True

@app.route('/update_device', methods=['POST'])
@require_auth
def update_device():
    ip  = request.json.get('ip')
    pwd = get_config("shelly_password")
    logging.info(f"[OTA] === Update angefordert für {ip} ===")
    started = start_update(ip, pwd)
    return jsonify({"success": True, "started": started})

@app.route('/update_all', methods=['POST'])
@require_auth
def update_all():
    latest_v = get_latest_version()
    devices, _ = grouped_devices(latest_v)
    outdated = [d for d in devices if d['status'] == 'Online' and not d['fw_ok']]
    pwd = get_config("shelly_password")
    logging.info(f"[OTA] === Massenupdate angefordert für {len(outdated)} Gerät(e) ===")
    started = [dev['ip'] for dev in outdated if start_update(dev['ip'], pwd)]
    return jsonify({"success": True, "count": len(started), "ips": started})

@app.route('/update_status')
@require_auth
def update_status():
    ip = request.args.get('ip')
    with progress_lock:
        if ip:
            return jsonify(update_progress.get(ip, {}))
        return jsonify(dict(update_progress))

@app.route('/remove_device', methods=['POST'])
@require_auth
def remove_device():
    ip = request.json.get('ip')
    conn = get_db()
    conn.execute('DELETE FROM devices WHERE ip = ?', (ip,))
    conn.commit()
    conn.close()
    logging.info(f"Gerät {ip} entfernt.")
    return jsonify({"success": True})

@app.route('/rename_device', methods=['POST'])
@require_auth
def rename_device():
    ip   = request.json.get('ip')
    name = request.json.get('name', '').strip()
    if not name:
        return jsonify({"success": False, "error": "Name darf nicht leer sein"})
    conn = get_db()
    conn.execute('UPDATE devices SET name = ? WHERE ip = ?', (name, ip))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/set_group', methods=['POST'])
@require_auth
def set_group():
    ip    = request.json.get('ip')
    group = request.json.get('group', '').strip()
    conn  = get_db()
    conn.execute('UPDATE devices SET group_name = ? WHERE ip = ?', (group, ip))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/update_order', methods=['POST'])
@require_auth
def update_order():
    order = request.json.get('order', [])
    conn  = get_db()
    for index, ip in enumerate(order):
        conn.execute('UPDATE devices SET position = ? WHERE ip = ?', (index, ip))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ──────────────────────────────────────────────────────────────
# API-TOKENS
# ──────────────────────────────────────────────────────────────

@app.route('/tokens/create', methods=['POST'])
@require_auth
def tokens_create():
    name = request.json.get('name', '').strip()
    if not name:
        return jsonify({"success": False, "error": "Name erforderlich"})
    token = 'sm_' + secrets.token_hex(24)
    created_at = time.strftime('%Y-%m-%d %H:%M')
    conn = get_db()
    conn.execute('INSERT INTO tokens (name, token, created_at) VALUES (?, ?, ?)',
                 (name, token, created_at))
    conn.commit()
    conn.close()
    logging.info(f"Neuer API-Token erstellt: '{name}'")
    return jsonify({"success": True, "token": token, "name": name, "created_at": created_at})

@app.route('/tokens/delete', methods=['POST'])
@require_auth
def tokens_delete():
    token_id = request.json.get('id')
    conn = get_db()
    conn.execute('DELETE FROM tokens WHERE id = ?', (token_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/tokens/list')
@require_auth
def tokens_list():
    conn   = get_db()
    tokens = conn.execute('SELECT id, name, created_at, substr(token,1,8) as token_preview FROM tokens').fetchall()
    conn.close()
    return jsonify([dict(t) for t in tokens])

# ──────────────────────────────────────────────────────────────
# EINSTELLUNGEN
# ──────────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
@require_auth
def settings():
    message = ""
    if request.method == 'POST':
        conn = get_db()
        if request.form.get('admin_password'):
            conn.execute('UPDATE users SET password = ? WHERE username = "admin"',
                         (generate_password_hash(request.form['admin_password']),))
        conn.execute('UPDATE config SET value = ? WHERE key = "shelly_password"',
                     (request.form.get('shelly_password'),))
        conn.execute('UPDATE config SET value = ? WHERE key = "ip_range"',
                     (request.form.get('ip_range'),))
        conn.execute('UPDATE config SET value = ? WHERE key = "scan_mode"',
                     (request.form.get('scan_mode', 'ip'),))
        conn.commit()
        conn.close()
        message = "Einstellungen gespeichert!"
    conn   = get_db()
    tokens = conn.execute('SELECT id, name, created_at, substr(token,1,10) || "..." as token_preview FROM tokens ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('settings.html',
        message=message,
        shelly_pwd=get_config("shelly_password"),
        ip_range=get_config("ip_range"),
        scan_mode=get_config("scan_mode"),
        tokens=tokens,
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
