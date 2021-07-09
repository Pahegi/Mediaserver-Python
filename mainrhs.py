from vlc import Instance
import RPi.GPIO as GPIO
import configparser
import vlc
import sacn
import time
import os

##############################################################
# DMX Footprint:
# CH1: Selection of Video (0 Stop, 1-255 Play Files)
# CH2: Selection of Folder (0-255)
# CH3: Selection of Playmode (0-127 Non-Loop, 128-255 Loop)
# CH4: Selection of Videosource (0-127 PGM, 128-255 Cam)
##############################################################


class Server:
  #Konstruktor
  def __init__ (self):
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(23, GPIO.OUT)
    GPIO.setup(24, GPIO.OUT)
    self.CH1Current = 0                                 
    self.CH1Last = 0                                    
    self.CH2Current = 0                                 
    self.CH2Last = 0
    self.CH4Current = 0                                 
    self.CH4Last = 0                                  
    self.address = 1                                    #DMX Start Adress
    self.usb = None                                     #Name of USB Stick
    self.findStick()                                    #Method to search for Stick
    self.receiver = sacn.sACNreceiver()                 #sACN Receiver
    self.data = 0;

    config = configparser.ConfigParser()
    if len(os.listdir("/media/pi/")) == 0:
        self.findStick()
    config.read("/media/pi/" + self.usb + "/config.txt")
    adress = config.get("DMX-Konfiguration", "Adresse")
    print("Loaded adress", adress, " from configfile")

    self.vlc_instance = vlc.Instance()                  #VLC Instance
    self.player = self.vlc_instance.media_player_new()

    #Define DMX Packet Callback
    @self.receiver.listen_on('universe', universe=1)    # listens on universe 1
    def callback(packet):  # packet type: sacn.DataPacket
      self.data = packet.dmxData

      #Auswahl von Videoaktion
      self.CH1Current = self.data[self.address-1]
      self.CH2Current = self.data[self.address]
      if ((self.CH1Current != self.CH1Last) or (self.CH2Current != self.CH2Last)): #Bei Änderung von DMX Werten
        #Abfrage auf Stick
        if len(os.listdir("/media/pi/")) == 0:
          self.findStick()
        if (self.CH1Current > 0):
          playpath = "/media/pi/" + self.usb + "/" + str(self.CH2Current).zfill(3) + "/" + str(self.CH1Current).zfill(3) + ".mp4"
          print("Playing new Media: " + playpath)
          self.media = self.vlc_instance.media_new(playpath)
          self.player.set_media(self.media)
          print("Turning Loop " + ("On" if (self.data[self.address+1] > 127) else "Off"))
          self.media.add_option("input-repeat=" + str(10000 if (self.data[self.address+1] > 127) else 0))
          self.player.play()
        else:
          print("Stopping Player")
          self.player.stop()
      self.CH1Last = self.CH1Current
      self.CH2Last = self.CH2Current

      self.CH4Current = self.data[self.address+2]
      if (self.CH4Current != self.CH4Last): #Bei Änderung von DMX Werten
        if (self.CH4Current > 127):
          print("Turning Relais on")
          GPIO.output(23, GPIO.HIGH)
          GPIO.output(24, GPIO.HIGH)
        else:
          print("Turning Relais off")
          GPIO.output(23, GPIO.LOW)
          GPIO.output(24, GPIO.LOW)
      self.CH4Last = self.CH4Current

    self.receiver.start()  # start the receiving thread
    self.receiver.join_multicast(1)
    
   
  #USB-Stick Such-Schleife
  def findStick(self):
    while len(os.listdir("/media/pi/")) == 0:
      time.sleep(2)
      print('[{3}:{4}:{5}]\tError no USB Device Found'.format(*time.localtime(time.time())))

    self.usb = os.listdir("/media/pi/")[0]
     
#Programmaufruf
def main():
  os.system("tvservice -p")
  server = Server()
  

if __name__ == '__main__':
  main()
