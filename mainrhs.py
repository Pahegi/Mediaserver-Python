from vlc import Instance
import vlc
import sacn
import time
import os

##############################################################
# DMX Footprint:
# CH1: Selection of Video (0 Stop, 1-255 Play Files)
# CH2: Selection of Folder (0-255)
# CH3: Selection of Playmode (0-127 Non-Loop, 128-255 Loop)
##############################################################


class Server:
  #Konstruktor
  def __init__ (self):
    self.CH1Current = 0                                 #Current DMX Values
    self.CH1Last = 0                                    #Current DMX Values
    self.CH2Current = 0                                 #Current DMX Values
    self.CH2Last = 0                                    #Current DMX Values
    self.address = 1                                    #DMX Start Adress
    self.usb = None                                     #Name of USB Stick
    self.findStick()                                    #Method to search for Stick
    self.receiver = sacn.sACNreceiver()                 #sACN Receiver
    self.data = 0;

    self.vlc_instance = vlc.Instance()                  #VLC Instance
    self.player = self.vlc_instance.media_player_new()
    # self.events = self.player.event_manager()
    # self.events.event_attach(vlc.EventType.MediaPlayerEndReached, self.videoFinished)

    #Define DMX Packet Callback
    @self.receiver.listen_on('universe', universe=1)    # listens on universe 1
    def callback(packet):  # packet type: sacn.DataPacket
      self.data = packet.dmxData
      #Abfrage auf Stick
      if len(os.listdir("/media/pi/")) == 0:
        self.findStick()

      #Auswahl von Videoaktion
      self.CH1Current = self.data[self.address-1]
      self.CH2Current = self.data[self.address]
      if ((self.CH1Current != self.CH1Last) or (self.CH2Current != self.CH2Last)): #Bei Änderung von DMX Werten
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

    self.receiver.start()  # start the receiving thread
    self.receiver.join_multicast(1)
    
   
  #USB-Stick Such-Schleife
  def findStick(self):
    while len(os.listdir("/media/pi/")) == 0:
      time.sleep(2)
      print('[{3}:{4}:{5}]\tError no USB Device Found'.format(*time.localtime(time.time())))

    self.usb = os.listdir("/media/pi/")[0]

  # def videoFinished(self, a):
  #   if (self.data[self.address] < 85):
  #     print("TODO: Stop Video")
      
#Programmaufruf
def main():
  server = Server()
  

if __name__ == '__main__':
  main()