#!/bin/bash

# Установка python3-pip
sudo apt install python3-pip

# Установка paho-mqtt через pip3
sudo pip3 install paho-mqtt --break-system-packages

# Копирование сертификата в домашний каталог
cp /home/pi/picamera/isrgrootx1.pem /home/pi/

# Копирование скрипта для работы с сервоприводом
sudo cp /home/pi/picamera/servo.py /usr/bin/

# Копирование сервиса для управления сервоприводами
sudo cp /home/pi/picamera/servo.service /etc/systemd/system/

# Перечитываем конфигурацию systemd
sudo systemctl daemon-reload

# Включаем сервис servo.service
sudo systemctl enable servo.service

# Установка и настройка firewalld
sudo apt install firewalld
sudo systemctl enable firewalld
sudo systemctl start firewalld

# Добавление правил в firewall
sudo firewall-cmd --permanent --add-rich-rule 'rule family="ipv4" source address="192.168.1.56" port port="8554" protocol="tcp" accept'
sudo firewall-cmd --permanent --add-rich-rule 'rule family="ipv4" source address="192.168.1.56" port port="8554" protocol="udp" accept'
sudo firewall-cmd --permanent --remove-service=dhcpv6-client
sudo firewall-cmd --permanent --add-port=8883/tcp

# Клонирование v4l2rtspserver
git clone https://github.com/mpromonet/v4l2rtspserver.git

# Установка make и cmake
sudo apt install make cmake

# Установка v4l2rtspserver
cd /home/pi/v4l2rtspserver && cmake . && make && sudo make install

# Изменение конфигурации камеры
sudo sed -i '/camera_auto_detect=1/d' /boot/firmware/config.txt
sudo sed -i '/start_x=1/d' /boot/firmware/config.txt
sudo sed -i '/gpu_mem=128/d' /boot/firmware/config.txt
echo 'start_x=1' | sudo tee -a /boot/firmware/config.txt
echo 'gpu_mem=128' | sudo tee -a /boot/firmware/config.txt

# Настройки применятся после перезагрузки
echo "Необходимо перезагрузить устройство для применения изменений."
