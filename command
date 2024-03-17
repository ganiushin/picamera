sudo apt install python3-pip

sudo pip3 install paho-mqtt --break-system-packages
cp /home/pi/picamera/isrgrootx1.pem /home/pi/
sudo cp /home/pi/picamera/servo.py /usr/bin/
sudo cp /home/pi/picamera/servo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable servo.service


sudo apt install firewalld
sudo systemctl enable firewalld
sudo systemctl start firewalld 
sudo firewall-cmd --permanent --add-rich-rule 'rule family="ipv4" source address="192.168.1.56" port port="8554" protocol="tcp" accept'
sudo firewall-cmd --permanent --add-rich-rule 'rule family="ipv4" source address="192.168.1.56" port port="8554" protocol=“udp” accept'
sudo firewall-cmd --permanent --remove-service=dhcpv6-client
sudo firewall-cmd --permanent --add-port=8883/tcp


git clone https://github.com/mpromonet/v4l2rtspserver.git
sudo apt install make cmake

sudo nano /boot/firmware/config.txt
   #camera_auto_detect=1
   start_x=1
   gpu_mem=128
