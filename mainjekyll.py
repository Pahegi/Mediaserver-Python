# Copyright (C) 2021 Paul Hermann - All Rights Reserved
# You may use, distribute and modify this code under the
# terms of the GPL-3.0 License.
# https://github.com/Pahegi/Mediaserver-Python/blob/master/LICENSE


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
    self.loadConfig(self.configpath)
    self.channellist = Channellist(self.dmx.address)
    self.receiver = sacn.sACNreceiver()

  #start()
  #Starts Mediaserver and initializes Callback-Method for incoming DMX-Frames
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
    self.receiver.join_multicast(self.dmx.universe)

  #loadConfig(String)
  #Loads configfile from path and sets dmx values
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

  #getPlaypath(String, Channellist)
  #Calculates path of media file based on base-path and DMX-values
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
    self.new = True
    self.address = address

  #update()
  #Updates value of channel with packet and sets new flag
  def update(self, packet):
    value = packet.dmxData[self.address - 1]
    if (self.current != value):
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

  #update(packet)
  #Updates values of all channels with packet
  def update(self, packet):
    for i in range(3):
      self.channel[i].update(packet)

  #isNew(Int)
  #Returns True, if channel at address+offset received a new value in last DMX-Frame
  def isNew(self, offset):
    return self.channel[offset].isNew()

  #get(Int)
  #Returns value of DMX-Channel at address+offset
  def get(self, offset):
    return self.channel[offset].current

#############
# DMX-CLASS #
#############
class DMX:
  def __init__(self):
    self.address = 1
    self.universe = 1
  
  #toString()
  #Returns simple representation of universe and address
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

  #setMedia(String)
  #Sets media to play from a path String
  def setMedia(self, playpath):
    try:
      if (playpath != ""):
        self.media = self.vlc_instance.media_new(playpath)
        self.player.set_media(self.media)
        self.playpath = playpath
    except Exception as e:
            print(traceback.format_exc())

  #setLoop(Boolean)
  #Sets state of loop at media
  def setLoop(self, loop):
    if (self.playpath != ""):
      self.media.add_option("input-repeat=" + str(10000 if loop else 0))
  
  #play()
  #Starts playing media from configured path
  def play(self):
    if (self.playpath != ""):
      print("Starting Media '" + self.playpath + "' with Loop", "on" if self.loop else "off")
      self.player.play()

  #stop()
  #Stops media
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
