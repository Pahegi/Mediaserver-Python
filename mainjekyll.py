from vlc import Instance
import configparser
import traceback
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
  #Constructor
  def __init__ (self):
    self.CH1Current = 0                                 
    self.CH1Last = 0                                    
    self.CH2Current = 0                                 
    self.CH2Last = 0                                
    self.address = 1                                    #DMX Start Address
    self.universe = 1                                   #DMX Universe
    self.receiver = sacn.sACNreceiver()                 #sACN Receiver
    self.data = 0;
    mediapath = "/home/pi/media/"                       #Path of Media Folders and Files
    try:
      config = configparser.ConfigParser()
      config.read("/home/pi/config.txt")
      self.address = config.getint("DMX-Konfiguration", "Adresse")
      self.universe = config.getint("DMX-Konfiguration", "Universum")
      print("Loaded adress", self.address, "and universe", self.universe, " from configfile")
    except Exception as e:
      print(traceback.format_exc())
      print("Couldn't find config.txt, loaded adress", self.address, "and universe", self.universe)

    self.vlc_instance = vlc.Instance()                  #VLC Instance
    self.player = self.vlc_instance.media_player_new()


    #Define DMX Packet Callback
    @self.receiver.listen_on('universe', universe=self.universe)
    def callback(packet):
      self.data = packet.dmxData

      #Selection of Video
      self.CH1Current = self.data[self.address-1]
      self.CH2Current = self.data[self.address]
      if ((self.CH1Current != self.CH1Last) or (self.CH2Current != self.CH2Last)): #When values change
        if (self.CH1Current > 0):
          try:
            #Sorting and selection of folders/files
            folderlist = sorted(os.listdir(mediapath))
            if (len(folderlist) >= self.CH2Current):
              folder = folderlist[self.CH2Current]
              filelist = sorted(os.listdir(mediapath + "/" + folder))
              if (len(filelist) >= self.CH1Current):
                file = filelist[self.CH1Current-1]
                playpath = (mediapath + folder + "/" + file)
                #Wiedergabe
                self.media = self.vlc_instance.media_new(playpath)
                self.player.set_media(self.media)
                print("Playing new Media with Loop " + ("on: " if (self.data[self.address+1] > 127) else "off: ") + playpath)
                self.media.add_option("input-repeat=" + str(10000 if (self.data[self.address+1] > 127) else 0))
                self.player.play()
            else:
              print("Stopping Player")
              self.player.stop()
          except Exception as e:
            print(traceback.format_exc())
      self.CH1Last = self.CH1Current
      self.CH2Last = self.CH2Current
    self.receiver.start()  # start the receiving thread
    self.receiver.join_multicast(1)
     
  
def main():
  os.system("tvservice -p")
  server = Server()

if __name__ == '__main__':
  main()
