# E1.31 (sACN) Mediaserver

This is a simple python-based E1.31 (sACN) controllable mediaserver running on a Raspberry Pi.

## Versions (project-specific)
- Rocky Horror Show (mainrhs.py)
  - Includes control of two relais for composite video switching
  - 4 Channels
    - CH1: Selection of Video (0 Stop, 1-255 Play Files)
    - CH2: Selection of Folder (0-255)
    - CH3: Selection of Playmode (0-127 Non-Loop, 128-255 Loop)
    - CH4: Selection of Videosource (0-127 PGM, 128-255 Cam)
  - Mediafiles and configfile on external USB-Stick
  - *.mp4 only
  - Fixed mapping of DMX values to foldernames (000-255) and filenames (001.mp4-255.mp4)
- Jekyll and Hyde (mainjekyll.py)
  - 3 Channels
    - CH1: Selection of Video (0 Stop, 1-255 Play Files)
    - CH2: Selection of Folder (0-255)
    - CH3: Selection of Playmode (0-127 Non-Loop, 128-255 Loop)
  - Internal storage of mediafiles and configfile
    - Media: "/home/pi/media/"
    - Config: "/home/pi/config.txt"
  - Supports every format supported by VLC-Media-Player
  - No fixed mapping of DMX values to foldernames
    - Mapping of DMX values to alphabetically sorted foldernames and filenames

## Known bugs
- Loop only runs 10000 times
- Loop-state only gets set when selecting a new mediafile to play
- at the start of the loop some video-frames get dropped
- Videos under one second sometimes only get played partially

## Planned features
- Webserver for configuration
- Configuration of screen resolution in configfile (and webserver)
- Update-routine
- WiFi Support

## Raspberry Pi configuration

- Flashing OS
  - Raspberry Pi OS with desktop
  - Kernel version: 5.10
- Create file "ssh" on sd card root-folder
- Login via ssh with credentials "pi", "raspberry"
  - "sudo raspi-config"
    - System Options → Password, 12345678
    - Display Options → Disable Screen Blanking
    - Interface Options → Enable VNC
    - Advanced Options → Expand Filesystem
  - “sudo apt update”
  - “sudo apt upgrade”
  - "sudo reboot now"
- Connect via VNC to pi
  - Panel Settings (In task-bar)
    - Advanced → Minimise panel when not in use, 0px height
  - Preferences (In start-menu)
    - Add/Remove Software, search "Battery", deactivate "Battery monitor plugin for lxpanel"
  - Right click on desktop, preferences
    - uncheck Mounted Disks und Trashcan
    - Choose layout "No image" and "Color" black
- Connect via SSH
  - "sudo nano /etc/lightdm/lightdm.conf"
    - In Seat-section add line "xserver-command = X -nocursor"
  - "sudo nano .bashrc"
    - Add "alias python=’python3’"
  - Install requirements
    - "pip3 install pyusb"
    - "pip3 install python-vlc"
    - "pip3 install sacn"
  - cd /home/pi
  - git clone https://github.com/Pahegi/Mediaserver-Python.git
- Put script into autorun:
  - crontab -e
  - Select 1 (Nano)
  - Rocky Horror Show: Add Line "@reboot sleep 10 && python3 /home/pi/mainrhs.py"
  - Jekyll and Hyde: Add Line "@reboot sleep 10 && python3 /home/pi/Mediaserver-Python/mainjekyll.py" 
- Disable screensaver
    - "sudo apt install xscreensaver"
    - Connect via VNC:
      - startmenu, settings, screensaver, disable screensaver
