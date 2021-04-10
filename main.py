from ola.ClientWrapper import ClientWrapper
from vlc import Instance
import vlc
import subprocess
import signal
import time
import os

class Tmp:
  #Konstruktor
  def __init__ (self):
    self.last = 0
    self.current = 0
    self.address = 1
    self.usb = None
    self.findStick()

    #Start VLC
    #self.proc = subprocess.Popen(['vlc black.png'], shell=True, stdout=subprocess.PIPE, stdin=subprocess.PIPE)
    self.i = Instance('--codec avcodec,none')
    #self.i = Instance("--codec avcodec,none --sub-filter logo")
    self.media_player = self.i.media_player_new()

    #Logo Overlay (for Dimmer)
    #self.media_player.video_set_logo_string(vlc.VideoLogoOption.logo_file, "/home/pi/python/black.png")
    #self.media_player.video_set_logo_string(vlc.VideoLogoOption.logo_x, "10")
    #self.media_player.video_set_logo_string(vlc.VideoLogoOption.logo_y, "10")
    #self.media_player.video_set_logo_int(vlc.VideoLogoOption.logo_opacity, 150)
    #self.media_player.video_set_logo_int(vlc.VideoLogoOption.logo_delay, 0)
    #self.media_player.video_set_logo_int(vlc.VideoLogoOption.logo_position, 3)
    #self.media_player.video_set_logo_int(vlc.VideoLogoOption.logo_repeat, 1)
    #self.media_player.video_set_logo_int(vlc.VideoLogoOption.logo_enable, 1)
    
   
  #USB-Stick Such-Schleife
  def findStick(self,):
    while len(os.listdir("/media/pi/")) == 0:
      time.sleep(2)
      print('[{3}:{4}:{5}]\tError no USB Device Found'.format(*time.localtime(time.time())))

    self.usb = os.listdir("/media/pi/")[0]


  #Eingehendes DMX Paket
  def NewData(self, data):
    #Abfrage auf Stick
    if len(os.listdir("/media/pi/")) == 0:
      self.findStick()

    #Auswahl von Videoaktion
    self.current = data[self.address-1]
    if (self.current != self.last): #Bei Änderung von DMX Wert
      if (self.current > 0):
        print("Stop Previous")
        print("Play " + str(self.current))
        self.media_player.set_mrl("/media/pi/PAULH32A/0000" + str(self.current) + ".mp4")
        self.media_player.play()
      else:
        print("Stop Previous")
        self.media_player.stop()
    self.last = self.current
    self.media_player.video_set_adjust_int(vlc.VideoAdjustOption.Enable, 1)
    self.media_player.video_set_adjust_float(vlc.VideoAdjustOption.Hue, ((data[self.address]+128)%256)*1.411-180)
    self.media_player.video_set_adjust_float(vlc.VideoAdjustOption.Brightness, data[self.address+1]/128)
    self.media_player.video_set_adjust_float(vlc.VideoAdjustOption.Contrast, data[self.address+2]/128)
    self.media_player.video_set_adjust_float(vlc.VideoAdjustOption.Saturation, data[self.address+3]/85)
    self.media_player.video_set_adjust_float(vlc.VideoAdjustOption.Gamma, data[self.address+4]/25)
    self.media_player.video_set_scale(data[self.address+5]/25)
    #self.media_player.video_set_logo_int(vlc.VideoLogoOption.logo_opacity, data[self.address+6])
    

#Programmaufruf
def main():
  tmp = Tmp()

  universe = 1

  wrapper = ClientWrapper()
  client = wrapper.Client()
  client.RegisterUniverse(universe, client.REGISTER, tmp.NewData)
  wrapper.Run()

if __name__ == '__main__':
  main()