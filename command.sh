#!/bin/bash
# Настройка Raspberry Pi Zero W на Raspberry Pi OS Lite 32-bit (Trixie):
# серво (MQTT), камера (MediaMTX), сеть и firewall. В конце перезагружает устройство.
#
# 1. Скопировать файлы репозитория в /home/pi/picamera
# 2. Скопировать setup.env.example в setup.env и заполнить
# 3. Запустить, можно безопасно повторять:
#    sudo systemd-run --unit=picamera-setup /bin/bash /home/pi/picamera/command.sh
#    Журнал: /home/pi/picamera/setup.log
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
exec >> setup.log 2>&1
trap 'echo "ОШИБКА в строке $LINENO, перезагрузки не будет"' ERR
echo "=== $(date) начало"

. ./setup.env
MEDIAMTX_VERSION=v1.21.3
MEDIAMTX_DIR=/home/pi/mediamtx
export DEBIAN_FRONTEND=noninteractive

echo "--- Пакеты"
apt-get update
apt-get -y -o Dpkg::Options::=--force-confold upgrade
apt-get install -y python3-paho-mqtt nftables

echo "--- Сервоприводы"
install -m 644 -o pi -g pi isrgrootx1.pem /home/pi/isrgrootx1.pem
install -d -m 755 /etc/picamera
if [ -f servo.env ]; then
    install -m 600 -o root -g root servo.env /etc/picamera/servo.env
    rm -f servo.env
elif [ ! -f /etc/picamera/servo.env ]; then
    install -m 600 -o root -g root servo.env.example /etc/picamera/servo.env
    echo "Заполните /etc/picamera/servo.env"
fi
install -m 644 servo.service /etc/systemd/system/servo.service

echo "--- Камера"
if [ "$("$MEDIAMTX_DIR/mediamtx" --version 2>/dev/null | head -1)" != "$MEDIAMTX_VERSION" ]; then
    install -d -o pi -g pi "$MEDIAMTX_DIR"
    curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_armv6.tar.gz" \
        | tar -xz -C "$MEDIAMTX_DIR" mediamtx
    chown pi:pi "$MEDIAMTX_DIR/mediamtx"
fi
if [ ! -f "$MEDIAMTX_DIR/mediamtx.yml" ]; then
    install -m 600 -o pi -g pi mediamtx.yml.example "$MEDIAMTX_DIR/mediamtx.yml"
    echo "Заполните пользователя, пароль и адреса клиентов в $MEDIAMTX_DIR/mediamtx.yml"
fi
if [ ! -f "$MEDIAMTX_DIR/server.key" ]; then
    (
        umask 077
        openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
            -keyout "$MEDIAMTX_DIR/server.key" -out "$MEDIAMTX_DIR/server.crt" -days 3650 \
            -subj "/CN=picamera" -addext "subjectAltName=IP:$STATIC_IP"
    )
    chown pi:pi "$MEDIAMTX_DIR/server.key" "$MEDIAMTX_DIR/server.crt"
    chmod 644 "$MEDIAMTX_DIR/server.crt"
fi
install -m 644 mediamtx.service /etc/systemd/system/mediamtx.service

echo "--- Загрузка: аппаратный ШИМ на GPIO 12 и 13, Bluetooth выключен"
for line in dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4 dtoverlay=disable-bt; do
    if ! grep -qxF "$line" /boot/firmware/config.txt; then
        printf '[all]\n%s\n' "$line" >> /boot/firmware/config.txt
    fi
done

echo "--- Перезапуск сервисов без пароля"
cat > /etc/sudoers.d/picamera <<'EOF'
pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart servo.service, /usr/bin/systemctl stop servo.service, /usr/bin/systemctl start servo.service
pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart mediamtx.service, /usr/bin/systemctl stop mediamtx.service, /usr/bin/systemctl start mediamtx.service
EOF
chmod 440 /etc/sudoers.d/picamera
visudo -cf /etc/sudoers.d/picamera

echo "--- Firewall: SSH и RTSPS только для клиентов"
cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
flush ruleset

table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;
        iif "lo" accept
        ct state established,related accept
        icmp type echo-request accept
        icmpv6 type { echo-request, nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert } accept
        tcp dport 22 accept
        ip saddr { $STREAM_CLIENTS } tcp dport 8322 accept
    }
}
EOF
nft -c -f /etc/nftables.conf

echo "--- Лишние службы и запись на карту"
touch /etc/cloud/cloud-init.disabled
systemctl mask avahi-daemon.service avahi-daemon.socket bluetooth.service hciuart.service udisks2.service ModemManager.service
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=16M\n' > /etc/systemd/journald.conf.d/10-volatile.conf

systemctl daemon-reload
systemctl enable servo.service mediamtx.service nftables.service

echo "--- Часовой пояс"
timedatectl set-timezone "$TIMEZONE"

echo "--- IPv6 выключен через sysctl: с ipv6.disable=1 таблица inet в nftables может не загрузиться"
printf 'net.ipv6.conf.all.disable_ipv6=1\nnet.ipv6.conf.default.disable_ipv6=1\nnet.ipv6.conf.lo.disable_ipv6=1\n' > /etc/sysctl.d/90-disable-ipv6.conf

echo "--- Wi-Fi: страна, энергосбережение, статический адрес"
sed -i -E "s/cfg80211\.ieee80211_regdom=[A-Z]+/cfg80211.ieee80211_regdom=$WIFI_COUNTRY/" /boot/firmware/cmdline.txt
printf '[connection]\nwifi.powersave=2\n' > /etc/NetworkManager/conf.d/10-wifi-powersave-off.conf
WIFI_CONNECTION=$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: '$2 == "wlan0" {print $1}')
nmcli connection modify "$WIFI_CONNECTION" ipv4.method manual ipv4.addresses "$STATIC_IP/24" ipv4.gateway "$GATEWAY" ipv4.dns "$DNS" ipv6.method disabled

echo "=== $(date) готово, перезагрузка"
systemctl reboot
