# Medienserver Doku

## Vorhandene Versionen
- Rocky Horror Show
  - 4 Kanäle --> Ansteuerung von externen Relais für Analogvideo-Umschaltung
  - Externe Speicherung von Configfile und Videos auf Stick
  - Nur *.mp4
- Jekyll and Hyde
  - Work in Progress
  - Interne Speicherung von Configfile und Videos
  - Nur *.jpeg

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
  - Panel Settings
    - Advanced → Minimise panel when not in use, 0px
  - Preferences
    - Add/Remove Software, “Battery” suchen, “Battery monitor plugin for lxpanel” deaktivieren
  - Rechtsklick auf Desktop
    - uncheck Mounted Disks
    - Schwarzer Hintergrund: Layout “No image” und “Colour” schwarz
- SSH
  - “sudo nano /etc/lightdm/lightdm.conf”
    - Add “xserver-command = X -nocursor”
  - “sudo nano .bashrc”
    - Add “alias python=’python3’”
  - Install Requirements
    - “pip3 install pyusb”
    - “pip3 install python-vlc”
    - “pip3 install sacn”
- Github
   - cd /home/pi
  - git clone https://github.com/Pahegi/Mediaserver-Python.git
- Skript in Autorun:
  - crontab -e
  - @reboot sleep 10 && python3 /home/pi/mainrhs.py (Delay, weil sonst Skript nicht startet)
