import vlc
import time
import os

from vlc import Instance

# class VLC:
#     def __init__(self):
#         self.Player = Instance('--loop')

#     def addPlaylist(self):
#         self.mediaList = self.Player.media_list_new()
#         path = r"C:\Users\dell5567\Desktop\engsong"
#         songs = os.listdir(path)
#         for s in songs:
#             self.mediaList.add_media(self.Player.media_new(os.path.join(path,s)))
#         self.listPlayer = self.Player.media_list_player_new()
#         self.listPlayer.set_media_list(self.mediaList)
#     def play(self):
#         self.listPlayer.play()
#     def next(self):
#         self.listPlayer.next()
#     def pause(self):
#         self.listPlayer.pause()
#     def previous(self):
#         self.listPlayer.previous()
#     def stop(self):
#         self.listPlayer.stop()

# player = VLC()

# player.addPlaylist("/media/pi/PAULH32A/")

# p = vlc.MediaPlayer()
# p.set_mrl("/media/pi/PAULH32A/00001.mp4")
# i = p.get_instance()
   

# for f in sorted(i.video_filter_list_get()):
#     print(f)
# print("=================================")
# #p.video_set_adjust_int(vlc.VideoAdjustOption.Enable, 1)
# #p.video_set_adjust_float(vlc.VideoAdjustOption.Hue, 130)
# #p.video_set_adjust_float(vlc.VideoAdjustOption.Brightness, 2)

# p.play()
# print('hue', p.video_get_adjust_float(vlc.VideoAdjustOption.Hue))
# print('bright', p.video_get_adjust_float(vlc.VideoAdjustOption.Brightness))

# while True:
#     pass

i = Instance('-vv --config /home/pi/.config/vlc/vlcrc --codec avcodec,none')
media_player = i.media_player_new()
media_player.set_mrl("/media/pi/PAULH32A/00001.mp4")
media_player.play()



media_player.video_set_adjust_int(vlc.VideoAdjustOption.Enable, 1)
i = 0.0
while True:
    media_player.video_set_adjust_float(vlc.VideoAdjustOption.Hue, i)
    i=((0.01+i+180)%360)-180