from vlc import Instance
import configparser
import traceback
import vlc
import sacn
import time
import os

################
# SERVER-CLASS #
################
class Server:
  def __init__ (self):
    self.mediapath = "/home/pi/media/"
    self.configpath = "/home/pi/config.txt"
    self.dmx = DMX()
    self.vlc = VLC()
    self.channellist = Channellist(self.dmx.address)
    self.receiver = sacn.sACNreceiver()
    self.loadConfig(self.configpath)

  #start()
  #starts Mediaserver
  def start(self):
    #DMX Packet Callback
    @self.receiver.listen_on('universe', universe=self.dmx.universe)
    def callback(packet):
      self.channellist.update(packet)
      if (self.channellist.isNew(0) | self.channellist.isNew(1)):
        if (self.channellist.get(0) > 0):
          playpath = self.getPlaypath(self.mediapath, self.channellist)
          if (playpath != ""):
            self.vlc.setMedia(playpath)
            self.vlc.setLoop(self.channellist.get(2) > 127)
            self.vlc.play()
          else:
            self.vlc.stop()
        else:
          self.vlc.stop()
    self.receiver.start()
    self.receiver.join_multicast(1)

  #loadConfig()
  #loads configfile and sets dmx values
  def loadConfig(self, configpath):
    try:
      config = configparser.ConfigParser()
      config.read(configpath)
      self.dmx.address = config.getint("DMX-Konfiguration", "Adresse")
      self.dmx.universe = config.getint("DMX-Konfiguration", "Universum")
      print("Loaded adress", self.dmx.toString(), "from configfile")
      return
    except Exception as e:
      print(traceback.format_exc())
      print("Couldn't find config.txt, loaded adress", self.dmx.toString())
      return

  #getPlaypath(mediapath, channellist)
  #calculates path of media file based on base-path and DMX-values
  def getPlaypath(self, mediapath, channellist):
    folderlist = sorted(os.listdir(mediapath))
    if (len(folderlist)-1 >= self.channellist.get(1)):
      folder = folderlist[self.channellist.get(1)]
      filelist = sorted(os.listdir(mediapath + "/" + folder))
      if (len(filelist) >= self.channellist.get(0)):
        file = filelist[self.channellist.get(0)-1]
        playpath = (mediapath + folder + "/" + file)
        return playpath
    return ""

#################
# CHANNEL-CLASS #
#################
class Channel:
  def __init__ (self, address):
    self.current = -1
    self.last = -1
    self.new = True
    self.address = address

  def update(self, packet):
    value = packet.dmxData[self.address - 1]
    if (self.current != value):
      self.last = self.current
      self.current = value
      self.new = True
    else:
      self.new = False
    return

  def isNew(self):
    return self.new

#####################
# CHANNELLIST-CLASS #
#####################
class Channellist:
  def __init__ (self, address):
    self.channel = [Channel(i+address) for i in range(3)]

  def update(self, packet):
    for i in range(3):
      self.channel[i].update(packet)

  def isNew(self, offset):
    return self.channel[offset].isNew()

  def get(self, offset):
    return self.channel[offset].current

#############
# DMX-CLASS #
#############
class DMX:
  def __init__(self):
    self.address = 1
    self.universe = 1
  
  def toString(self):
    return (str(self.universe) + "." + str(self.address))

#############
# VLC-CLASS #
#############
class VLC:
  def __init__(self):
    self.vlc_instance = vlc.Instance()
    self.player = self.vlc_instance.media_player_new()
    self.loop = False
    self.playpath = ""

  def setMedia(self, playpath):
    try:
      if (playpath != ""):
        self.media = self.vlc_instance.media_new(playpath)
        self.player.set_media(self.media)
        self.playpath = playpath
    except Exception as e:
            print(traceback.format_exc())

  def setLoop(self, loop):
    if (self.playpath != ""):
      self.media.add_option("input-repeat=" + str(10000 if loop else 0))
    
  def play(self):
    if (self.playpath != ""):
      print("Starting Media '" + self.playpath + "' with Loop", "on" if self.loop else "off")
      self.player.play()

  def stop(self):
    print("Stopping Media")
    self.player.stop()

###############
# MAIN-METHOD #
###############
def main():
  mediaserver = Server()
  mediaserver.start()

if __name__ == '__main__':
  main()