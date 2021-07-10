# Medienserver Doku

## Vorhandene Versionen
- Rocky Horror Show
  - 4 Kanäle
    - CH1: Selection of Video (0 Stop, 1-255 Play Files)
    - CH2: Selection of Folder (0-255)
    - CH3: Selection of Playmode (0-127 Non-Loop, 128-255 Loop)
    - CH4: Selection of Videosource (0-127 PGM, 128-255 Cam)
  - Externe Speicherung von Configfile und Videos auf Stick
  - Nur *.mp4
  - Festgelegte Benennung von Ordnern (000-255) und Dateien (001.mp4-255.mp4)
- Jekyll and Hyde
  - 3 Kanäle
    - CH1: Selection of Video (0 Stop, 1-255 Play Files)
    - CH2: Selection of Folder (0-255)
    - CH3: Selection of Playmode (0-127 Non-Loop, 128-255 Loop)
  - Interne Speicherung von Configfile und Videos
    - Content: "/home/pi/media/"
    - Config: "/home/pi/config.txt"
  - Jedes von VLC unterstütze Dateiformat möglich
  - Dateibenennung frei, Mapping auf DMX geschieht über alphabetische Sortierung

## Bekannte Fehler
- Laufendes Video friert ein bei Auswahl von nicht existenter Datei
- Loop läuft nur 10000 mal
- Loop-Status wird nur bei Medienstart gesetzt
- Bei Loop Neustart werden Frames am Anfang gedroppt
- Videos unter 1sek werden teils unvollständig abgespielt

## Anstehende Features
- Webserver für Konfiguration

## Raspberry Pi Einrichtung

- Betriebssystem flashen
  - Raspberry Pi OS with desktop
  - Kernel version: 5.10
- Datei “ssh” auf SD Karte root erstellen
- Login per SSH mit “pi”, “raspberry”
- “sudo raspi-config”
  - System Options → Password, 12345678
  - Display Options → Disable Screen Blanking
  - Interface Options → Enable VNC
  - Advanced Options → Expand Filesystem
- Reboot
- Update
  - “sudo apt update”
  - “sudo apt upgrade”
- Desktop aufräumen per VNC Verbindung
  - Schnellkonfiguration durchlaufen
  - Panel Settings (In Taskleiste)
    - Advanced → Minimise panel when not in use, 0px höhe
  - Preferences (In Startmenü)
    - Add/Remove Software, “Battery” suchen, “Battery monitor plugin for lxpanel” deaktivieren
  - Rechtsklick auf Desktop, Einstellungen
    - uncheck Mounted Disks und Trashcan
    - Schwarzer Hintergrund: Layout “No image” und “Colour” schwarz
- SSH
  - “sudo nano /etc/lightdm/lightdm.conf”
    - In Seat-Sektion “xserver-command = X -nocursor” hinzufügen
  - “sudo nano .bashrc”
    - Add “alias python=’python3’”
  - Install Requirements
    - “pip3 install pyusb”
    - “pip3 install python-vlc”
    - “pip3 install sacn”
  - Screensaver deaktiveren
    - "sudo apt install xscreensaver"
    - https://www.elektronik-kompendium.de/sites/raspberry-pi/2107011.htm
- Github
  - cd /home/pi
  - git clone https://github.com/Pahegi/Mediaserver-Python.git
- Skript in Autorun:
  - crontab -e
  - Rocky Horror Show: "@reboot sleep 10 && python3 /home/pi/mainrhs.py" hinzufügen (Delay, weil sonst Skript nicht startet)
  - Jekyll and Hyde: "@reboot sleep 10 && python3 /home/pi/Mediaserver-Python/mainjekyll.py" hinzufügen
