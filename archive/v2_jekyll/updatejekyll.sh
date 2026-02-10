#!/bin/bash

echo "Starting Update"

killall python3

rm /home/pi/Mediaserver-Python/mainjekyll.py

curl -LJO https://raw.githubusercontent.com/Pahegi/Mediaserver-Python/master/mainjekyll.py

python3 /home/pi/Mediaserver-Python/mainjekyll.py

echo "Done"

exit 0