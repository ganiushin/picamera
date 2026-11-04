#!/bin/bash
# Установка на Raspberry Pi Zero W: серво (MQTT) и камера (MediaMTX).
# Запускать из каталога репозитория: cd /home/pi/picamera && ./command.sh
set -e

# Последняя версия с поддержкой камеры на Debian Bullseye: в v1.21.0 она убрана
MEDIAMTX_VERSION=v1.20.1
MEDIAMTX_DIR=/home/pi/mediamtx

# paho-mqtt 2.x нет в пакетах Bullseye
sudo apt install -y python3-pip
pip3 install --user paho-mqtt

# Сервоприводы
cp isrgrootx1.pem /home/pi/
sudo install -m 644 servo.py /usr/bin/servo.py
sudo install -m 644 servo.service /etc/systemd/system/servo.service
sudo install -d -m 755 /etc/picamera
if ! sudo test -f /etc/picamera/servo.env; then
    sudo install -m 600 servo.env.example /etc/picamera/servo.env
    echo "Заполните /etc/picamera/servo.env"
fi

# Камера
mkdir -p "$MEDIAMTX_DIR"
curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_armv6.tar.gz" \
    | tar -xz -C "$MEDIAMTX_DIR" mediamtx
if [ ! -f "$MEDIAMTX_DIR/mediamtx.yml" ]; then
    install -m 600 mediamtx.yml.example "$MEDIAMTX_DIR/mediamtx.yml"
    echo "Заполните пользователя, пароль и адреса клиентов в $MEDIAMTX_DIR/mediamtx.yml"
fi
if [ ! -f "$MEDIAMTX_DIR/server.key" ]; then
    (
        umask 077
        openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
            -keyout "$MEDIAMTX_DIR/server.key" -out "$MEDIAMTX_DIR/server.crt" -days 3650 \
            -subj "/CN=picamera" -addext "subjectAltName=IP:$(hostname -I | cut -d' ' -f1)"
    )
    chmod 644 "$MEDIAMTX_DIR/server.crt"
fi
sudo install -m 644 mediamtx.service /etc/systemd/system/mediamtx.service

sudo systemctl daemon-reload
sudo systemctl enable servo.service mediamtx.service

# Камера Module 3 (imx708): в /boot/config.txt нужны camera_auto_detect=0 и dtoverlay=imx708
# Порты закрываются через iptables (netfilter-persistent): SSH и клиенты из mediamtx.yml
echo "Проверьте настройки камеры в /boot/config.txt и перезагрузите устройство."
