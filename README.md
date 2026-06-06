# Shelly Manager 🐚

Ein leichtgewichtiger, Docker-basierter Manager für Shelly-Geräte mit der **[Mongoose OS HomeKit Firmware](https://github.com/mongoose-os-apps/shelly-homekit)**. Zentrales Dashboard zum Steuern, Überwachen und Aktualisieren aller Geräte im Netzwerk.

---

## ✨ Features

- **Dashboard** — alle Shellys auf einen Blick: Status, Signal, Uptime, Firmware
- **Gruppen** — Geräte in Räume einteilen, ganze Gruppen ein-/ausschalten
- **Live-Uptime** — tickt sekündlich im Browser ohne Seitenreload
- **Firmware-Check** — prüft automatisch via GitHub ob Updates verfügbar sind
- **OTA-Update** — Firmware-Update per Klick, auch für alle Geräte gleichzeitig
- **Energieverbrauch** — Watt, Volt, Ampere für Geräte mit Strommessung (Plug S, 1PM etc.)
- **Netzwerk-Scan** — IP-Bereich oder mDNS (automatisch)
- **API-Tokens** — externer Zugriff für Home Assistant, Skripte etc.
- **Dark / Light Mode** — umschaltbar, wird gespeichert
- **Drag & Drop** — Reihenfolge der Karten frei sortierbar
- **Docker** — komplett containerisiert, Daten bleiben bei Updates erhalten

---

## 🚀 Installation

### Voraussetzungen
- Docker & Docker Compose

### 1. Repository klonen

```bash
git clone https://github.com/DEIN_USERNAME/shelly-manager.git
cd shelly-manager
```

### 2. Konfiguration anlegen

```bash
cp .env.example .env
```

Öffne `.env` und setze mindestens:

```env
HOST_PORT=8095
APP_DB_PATH=./data
SECRET_KEY=hier-einen-langen-zufaelligen-string    # openssl rand -hex 32
```

### 3. Container starten

```bash
docker-compose up --build -d
```

### 4. Setup-Wizard

Beim ersten Start öffne `http://dein-server:8095` im Browser.  
Du wirst automatisch zum **Ersteinrichtungs-Wizard** weitergeleitet wo du:
- das Admin-Passwort setzt
- den IP-Scan Bereich konfigurierst
- das Shelly-Passwort einträgst (falls gesetzt)

---

## ⚙️ Konfiguration

### Umgebungsvariablen (`.env`)

| Variable | Beschreibung | Pflicht |
|---|---|---|
| `HOST_PORT` | Port des Dashboards | ✅ |
| `APP_DB_PATH` | Pfad für die SQLite-Datenbank | ✅ |
| `SECRET_KEY` | Geheimer Schlüssel für Sessions | ✅ |
| `ADMIN_PASSWORD` | Admin-Passwort direkt setzen (überspringt Wizard) | — |
| `FLASK_ENV` | `production` (Standard) | — |

### Updates durchführen

Deine Daten (Gerätliste, Gruppen, Tokens) liegen im `data/`-Ordner als SQLite-Datenbank und bleiben bei Updates erhalten.

```bash
git pull
docker-compose up --build -d
```

---

## 🔑 API-Token Nutzung

Tokens ermöglichen externen Tools den Zugriff ohne Browser-Login.

**Token erstellen:** Einstellungen → API-Tokens → Name eingeben → Token erstellen

**Token verwenden:**

```bash
# Gerät einschalten
curl -X POST http://dein-server:8095/control \
  -H "Content-Type: application/json" \
  -H "X-API-Token: sm_dein-token" \
  -d '{"ip": "192.168.1.50", "action": "state", "value": "on"}'

# Gruppe ausschalten
curl -X POST http://dein-server:8095/control_group \
  -H "Content-Type: application/json" \
  -H "X-API-Token: sm_dein-token" \
  -d '{"group": "Wohnzimmer", "action": "off"}'

# Status aller Geräte
curl http://dein-server:8095/status \
  -H "X-API-Token: sm_dein-token"
```

### Home Assistant Beispiel

```yaml
rest_command:
  shelly_wohnzimmer_an:
    url: "http://dein-server:8095/control_group"
    method: POST
    headers:
      X-API-Token: "sm_dein-token"
      Content-Type: "application/json"
    payload: '{"group": "Wohnzimmer", "action": "on"}'
```

---

## 🌐 mDNS (optional)

Wenn der Container im lokalen Netz läuft (nicht in der Cloud), kann mDNS Geräte automatisch ohne IP-Konfiguration finden.  
Dazu in `docker-compose.yml` `network_mode: host` aktivieren und mDNS in den Einstellungen wählen.

---

## 🤝 Mitwirken

Pull Requests sind willkommen!

1. Fork erstellen
2. Feature-Branch anlegen (`git checkout -b feature/mein-feature`)
3. Änderungen committen (`git commit -m 'feat: mein feature'`)
4. Branch pushen (`git push origin feature/mein-feature`)
5. Pull Request öffnen

---

## 📄 Lizenz

MIT License — siehe [LICENSE](LICENSE)
