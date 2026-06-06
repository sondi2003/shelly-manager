<div align="center">

# 🐚 Shelly Manager

**A clean, self-hosted dashboard for Shelly devices running the [Mongoose OS HomeKit Firmware](https://github.com/mongoose-os-apps/shelly-homekit)**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](requirements.txt)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](requirements.txt)

[Features](#-features) · [Quick Start](#-quick-start) · [API](#-api-tokens) · [Wiki](../../wiki)

</div>

---

> **Note:** This project is specifically built for Shelly devices flashed with the **Mongoose OS HomeKit firmware** (`shelly-homekit`). It will not work with the official Shelly firmware.

---

## ✨ Features

| | |
|---|---|
| 📊 **Live Dashboard** | All your devices at a glance — status, signal strength, uptime, firmware version |
| 🏠 **Rooms & Groups** | Organize devices into rooms, control an entire group with one click |
| ⚡ **Energy Monitoring** | Real-time power consumption (W/V/A) for Plug S, 1PM, 2.5 and other metering devices |
| 🔄 **OTA Updates** | Check & apply firmware updates directly from the dashboard, including bulk update |
| 🌐 **Auto-Discovery** | Scan IP ranges or use mDNS to find devices automatically |
| 🔑 **API Tokens** | Integrate with Home Assistant, scripts, or any HTTP client — no browser required |
| 🌙 **Dark / Light Mode** | Smooth theme toggle, preference saved in browser |
| 🔒 **Secure by default** | First-run setup wizard, no default credentials, digest auth for Shelly devices |
| 🐳 **Docker-native** | Single container, data persists across updates via volume mount |

---

## 📸 Screenshot

<!-- Replace with your actual screenshot -->
> *Dashboard screenshot coming soon*

---

## ⚡ Quick Start

**Requirements:** Docker & Docker Compose

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/shelly-manager.git
cd shelly-manager

# 2. Create your configuration
cp .env.example .env
# Edit .env — set HOST_PORT, APP_DB_PATH and SECRET_KEY

# 3. Build and start
docker-compose up --build -d

# 4. Open your browser
# → http://your-server:8095
# The setup wizard will guide you through the first-run configuration.
```

→ **Detailed installation guide:** [Wiki — Installation](../../wiki/Installation)

---

## 🔑 API Tokens

Shelly Manager supports token-based authentication for external access — no browser login required.

Create a token in **Settings → API Tokens**, then use it in any HTTP client:

```bash
# Turn on a single device
curl -X POST http://your-server:8095/control \
  -H "X-API-Token: sm_your-token-here" \
  -H "Content-Type: application/json" \
  -d '{"ip": "192.168.1.50", "action": "state", "value": "on"}'

# Control an entire group
curl -X POST http://your-server:8095/control_group \
  -H "X-API-Token: sm_your-token-here" \
  -H "Content-Type: application/json" \
  -d '{"group": "Living Room", "action": "off"}'
```

→ **Full API reference:** [Wiki — API Reference](../../wiki/API-Reference)

---

## 🏠 Home Assistant

```yaml
# configuration.yaml
rest_command:
  shelly_livingroom_on:
    url: "http://your-server:8095/control_group"
    method: POST
    headers:
      X-API-Token: "sm_your-token-here"
      Content-Type: "application/json"
    payload: '{"group": "Living Room", "action": "on"}'
```

---

## 🔄 Updating

Your device list, groups, settings and API tokens are stored in a SQLite database mounted as a Docker volume. **They are preserved across updates.**

```bash
git pull
docker-compose up --build -d
```

---

## 🤝 Contributing

Contributions are welcome! Please open an issue first to discuss what you'd like to change.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.

---

<div align="center">
<sub>Built with ❤️ for the Mongoose OS / shelly-homekit community</sub>
</div>
