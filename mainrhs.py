from vlc import Instance
import vlc
import sacn
import time
import os

class Server:
  #Konstruktor
  def __init__ (self):
    self.last = 0                                       #Last DMX Values
    self.last2 = 0                                       #Last DMX Values
    self.current = 0                                    #Current DMX Values
    self.current2 = 0                                    #Current DMX Values
    self.address = 1                                    #DMX Start Adress
    self.usb = None                                     #Name of USB Stick
    self.findStick()                                    #Method to search for Stick
    self.receiver = sacn.sACNreceiver()                 #sACN Receiver

    self.vlc_instance = vlc.Instance()                  #VLC Instance
    self.player = self.vlc_instance.media_player_new()

    #Define DMX Packet Callback
    @self.receiver.listen_on('universe', universe=1)    # listens on universe 1
    def callback(packet):  # packet type: sacn.DataPacket
      data = packet.dmxData
      #Abfrage auf Stick
      if len(os.listdir("/media/pi/")) == 0:
        self.findStick()

      #CHANNEL 1 - Auswahl von Videoaktion
      self.current = data[self.address-1]
      if (self.current != self.last): #Bei Änderung von DMX Wert
        if (self.current > 0):
          playpath = "/media/pi/" + self.usb + "/" + str(self.current).zfill(3) + ".mp4"
          print("Adding Media: " + playpath)
          media = self.vlc_instance.media_new(playpath)
          print("Setting Media")
          self.player.set_media(media)
          print("Start Playing")
          self.player.play()
        else:
          print("Stop Previous")
          self.player.stop()
      self.last = self.current


      #CHANNEL 2 - Auswahl von Preload
      self.current2 = data[self.address]
      if (self.current2 != self.last2): #Bei Änderung von DMX Wert
        print("Should Preload " + str(self.current2))
      self.last2 = self.current2

    self.receiver.start()  # start the receiving thread
    self.receiver.join_multicast(1)
    
   
  #USB-Stick Such-Schleife
  def findStick(self,):
    while len(os.listdir("/media/pi/")) == 0:
      time.sleep(2)
      print('[{3}:{4}:{5}]\tError no USB Device Found'.format(*time.localtime(time.time())))

    self.usb = os.listdir("/media/pi/")[0]


#Programmaufruf
def main():
  server = Server()
  

if __name__ == '__main__':
  main()