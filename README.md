# Shelly Mongoose Manager 🚀

Ein leichtgewichtiger, Docker-basierter Manager für Shelly-Geräte, die mit der **Mongoose OS HomeKit Firmware** betrieben werden. Dieses Tool ermöglicht die zentrale Steuerung, Überwachung und das einfache Durchführen von Firmware-Updates über ein modernes Web-Dashboard.

## ✨ Features

- **Zentrales Dashboard:** Alle Shellys auf einen Blick (Status, IP, Signalstärke).
- **Mongoose OS optimiert:** Nutzt die spezifischen RPC-Endpunkte für Status und Schaltung.
- **Intelligenter Firmware-Check:** Prüft automatisch über die GitHub-API, ob eine neue Version von `shelly-homekit` verfügbar ist.
- **One-Click Update:** Führt Firmware-Updates direkt aus dem Dashboard via OTA-Proxy durch.
- **Echtzeit-Uptime:** Anzeige der Laufzeit in Jahren, Monaten, Tagen und Sekunden – tickt live im Browser.
- **Drag & Drop Sortierung:** Ordne deine Geräteoberfläche so an, wie du es möchtest (Reihenfolge wird gespeichert).
- **Netzwerk-Scanner:** Scannt IP-Bereiche oder CIDR-Subnetze nach neuen Geräten.
- **Security:** Passwortgeschützter Login und Unterstützung für Shelly-Authentifizierung (Digest Auth).

## 🛠 Installation (Docker)

Der Shelly Manager ist für den Betrieb in Docker optimiert.

1. **Repository klonen:**
   ```bash
   git clone [https://github.com/DEIN_USERNAME/shelly-manager.git](https://github.com/DEIN_USERNAME/shelly-manager.git)
   cd shelly-manager

   Docker Container bauen & starten:
   docker build -t shelly-manager .
docker run -d -p 5000:5000 -v $(pwd)/data:/app/data --name shelly-manager-app shelly-manager

Dashboard aufrufen:
Öffne http://localhost:5000 in deinem Browser.

Standard-User: admin

Standard-Passwort: admin (Sofort in den Einstellungen ändern!)

⚙️ Konfiguration
Gehe nach dem ersten Login auf SETUP, um folgende Werte zu konfigurieren:

Admin-Passwort: Ändere das Passwort für das Dashboard.

Shelly-Passwort: Das Passwort, welches du in der Mongoose-Firmware für deine Geräte gesetzt hast.

IP-Range: Gib dein Subnetz an (z. B. 192.168.1.0/24) oder einen Bereich (192.168.1.10-20).


<img width="1355" height="948" alt="image" src="https://github.com/user-attachments/assets/549b8705-9ed2-40dd-b276-b6b806d52012" />



🚀 Roadmap / Mitwirken
Dieses Projekt ist Open Source und ich freue mich über jede Unterstützung! Geplante Features sind:

Möchtest du mithelfen?

Forke das Repository.

Erstelle einen Feature-Branch (git checkout -b feature/neues-feature).

Commit deine Änderungen (git commit -m 'Add some feature').

Push den Branch (git push origin feature/neues-feature).

Öffne einen Pull Request.

📄 Lizenz
Verteilt unter der MIT-Lizenz. Siehe LICENSE für weitere Informationen.

