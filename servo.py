import paho.mqtt.client as mqttClient
from pathlib import Path
from time import monotonic, sleep
import logging
import os
import signal
import sys
import threading
from typing import Dict, Optional

# === Настройка логирования ===
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "WARNING"),
    format='[%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# === Конфигурация ===
# Аппаратный ШИМ, оверлей pwm-2chan загружает servo.service:
# GPIO 12 (BCM) - канал PWM0, физический пин 32
# GPIO 13 (BCM) - канал PWM1, физический пин 33
PWM_CHIP = Path("/sys/class/pwm/pwmchip0")
SERVO1_CHANNEL = 0
SERVO2_CHANNEL = 1
SERVO1_MIN = 6.0
SERVO1_MAX = 10.0
SERVO2_MIN = 1.0
SERVO2_MAX = 12.5
PWM_FREQUENCY = 50  # Hz для сервомоторов
PWM_PERIOD_NS = 1_000_000_000 // PWM_FREQUENCY
PWM_PULSE_DURATION = 0.05  # секунды
PWM_FIRST_MOVE_DURATION = 1.0  # секунды, если положение серво неизвестно
PWM_SETUP_TIMEOUT = 5.0  # секунды, udev выдаёт права на pwmchip не мгновенно
DUTY_CYCLE_OFF = 0

# === Параметры плавного вращения ===
SMOOTH_STEP = 0.1  # Шаг изменения угла за один цикл
SMOOTH_DELAY = 0.02  # Задержка между шагами (секунды)
SMOOTH_MAX_STEPS = 30  # Максимальное число шагов на одно движение
SMOOTH_ENABLED = True  # Включить плавное вращение

# Хост и учётные данные задаются в /etc/picamera/servo.env, см. servo.env.example
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER = os.environ["MQTT_USER"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
MQTT_CA_CERT = os.environ.get("MQTT_CA_CERT", "/home/pi/isrgrootx1.pem")
MQTT_SUB_TOPIC = "b1/camera_rotate"
MQTT_STATE_TOPIC = f"{MQTT_SUB_TOPIC}/state"
MQTT_AVAILABILITY_TOPIC = f"{MQTT_SUB_TOPIC}/availability"
MQTT_KEEPALIVE = 60
MQTT_CLIENT_ID = "servo_controller"


def write_sysfs(path: Path, value: str) -> None:
    """Запись в sysfs с ожиданием, пока файл появится и udev выдаст на него права."""
    deadline = monotonic() + PWM_SETUP_TIMEOUT
    while True:
        try:
            path.write_text(value)
            return
        except (FileNotFoundError, PermissionError):
            if monotonic() >= deadline:
                raise
            sleep(0.1)


class HardwarePwm:
    """Канал аппаратного ШИМ: импульсы формирует контроллер, CPU в этом не участвует."""

    def __init__(self, channel: int) -> None:
        self.path = PWM_CHIP / f"pwm{channel}"
        if not self.path.exists():
            write_sysfs(PWM_CHIP / "export", str(channel))
        write_sysfs(self.path / "period", str(PWM_PERIOD_NS))
        write_sysfs(self.path / "duty_cycle", str(DUTY_CYCLE_OFF))
        write_sysfs(self.path / "enable", "1")

    def set_duty(self, duty: float) -> None:
        """Установка заполнения в процентах, как в значениях MQTT-команды."""
        (self.path / "duty_cycle").write_text(str(int(PWM_PERIOD_NS * duty / 100)))

    def stop(self) -> None:
        self.set_duty(DUTY_CYCLE_OFF)
        (self.path / "enable").write_text("0")


class Servo:
    """Сервопривод: канал ШИМ, допустимый диапазон, текущий угол и цель движения."""

    def __init__(self, channel: int, min_val: float, max_val: float) -> None:
        self.pwm = HardwarePwm(channel)
        self.min_val = min_val
        self.max_val = max_val
        self.angle: Optional[float] = None  # None, пока положение неизвестно
        self.target: Optional[float] = None  # None, если двигаться не нужно


# === Глобальные переменные ===
servos: Dict[str, Servo] = {}
mqtt_client: Optional[mqttClient.Client] = None
motion_thread: Optional[threading.Thread] = None

# === Состояние потока движения, защищено motion_wakeup ===
motion_wakeup = threading.Condition()
motion_version: int = 0  # Растёт с каждой новой командой
shutting_down: bool = False


def setup_servos() -> Dict[str, Servo]:
    """Инициализация аппаратного ШИМ для сервоприводов."""
    try:
        result = {
            "servo1": Servo(SERVO1_CHANNEL, SERVO1_MIN, SERVO1_MAX),
            "servo2": Servo(SERVO2_CHANNEL, SERVO2_MIN, SERVO2_MAX),
        }
        logger.info("Аппаратный ШИМ инициализирован успешно")
        return result
    except OSError as e:
        logger.error(f"Ошибка инициализации ШИМ: {e}")
        raise


def validate_angle(angle: float, min_val: float, max_val: float) -> bool:
    """Проверка угла на соответствие диапазону."""
    return min_val <= angle <= max_val


def parse_angle(name: str, value: str, servo: Servo) -> Optional[float]:
    """Преобразование значения из payload в угол. Возвращает None, если значение некорректно."""
    try:
        angle = float(value)
    except ValueError as e:
        logger.error(f"{name}: неверный формат угла '{value}': {e}")
        return None
    if not validate_angle(angle, servo.min_val, servo.max_val):
        logger.warning(f"{name}: угол {angle} вне диапазона [{servo.min_val}, {servo.max_val}]")
        return None
    return angle


def set_targets(params: Dict[str, str]) -> bool:
    """
    Установка целей для потока движения. Новая цель заменяет предыдущую,
    поэтому команды не копятся в очереди.

    Returns:
        True, если принята хотя бы одна цель
    """
    global motion_version

    accepted = False
    with motion_wakeup:
        for name, servo in servos.items():
            if name not in params:
                continue
            angle = parse_angle(name, params[name], servo)
            if angle is not None:
                servo.target = angle
                accepted = True
        if accepted:
            motion_version += 1
            motion_wakeup.notify()
    return accepted


def restore_angles(params: Dict[str, str]) -> None:
    """Восстановление неизвестных углов из retained-состояния после перезапуска."""
    with motion_wakeup:
        for name, servo in servos.items():
            if servo.angle is not None or name not in params:
                continue
            angle = parse_angle(name, params[name], servo)
            if angle is not None:
                servo.angle = angle
                logger.info(f"{name}: восстановлен угол {angle:.2f}")


def is_outdated(version: int) -> bool:
    """Пришла новая команда или сервис завершает работу."""
    return motion_version != version or shutting_down


def move_to_targets(version: int) -> bool:
    """
    Плавное перемещение сервоприводов к целям: оба движутся одновременно.
    Новая команда прерывает движение, и оно пересчитывается от текущих углов.

    Returns:
        True, если цели достигнуты
    """
    with motion_wakeup:
        plan = [(s, s.angle, s.target) for s in servos.values() if s.target is not None]

    hold = PWM_PULSE_DURATION
    max_diff = 0.0
    for servo, start, target in plan:
        if start is None:
            # Плавно ехать неоткуда: сразу подаём цель и даём серво время доехать
            servo.pwm.set_duty(target)
            hold = PWM_FIRST_MOVE_DURATION
        else:
            max_diff = max(max_diff, abs(target - start))

    steps = min(int(max_diff / SMOOTH_STEP) + 1, SMOOTH_MAX_STEPS) if SMOOTH_ENABLED else 1
    for i in range(1, steps + 1):
        if is_outdated(version):
            return False
        for servo, start, target in plan:
            if start is not None:
                servo.angle = start + (target - start) * i / steps
                servo.pwm.set_duty(servo.angle)
        sleep(SMOOTH_DELAY)

    # Финальный импульс
    for servo, _, target in plan:
        servo.angle = target
        servo.pwm.set_duty(target)
    sleep(hold)
    return not is_outdated(version)


def finish_move(version: int) -> None:
    """Отключение сигнала после движения, чтобы серво не дрожали, и публикация состояния."""
    with motion_wakeup:
        if is_outdated(version):
            return
        for servo in servos.values():
            if servo.target is not None:
                servo.target = None
                servo.pwm.set_duty(DUTY_CYCLE_OFF)
    publish_state()


def motion_loop() -> None:
    """Поток движения: MQTT-колбэк только задаёт цели и поэтому не блокируется."""
    version = 0
    while True:
        with motion_wakeup:
            motion_wakeup.wait_for(lambda: motion_version != version or shutting_down)
            if shutting_down:
                return
            version = motion_version
        try:
            if move_to_targets(version):
                finish_move(version)
        except OSError as e:
            logger.error(f"Ошибка установки угла: {e}")


def publish_state() -> None:
    """Публикация текущих углов: по ним HA видит положение, а сервис восстанавливает его после перезапуска."""
    payload = ";".join(
        f"{name}={servo.angle:.2f}" for name, servo in servos.items() if servo.angle is not None
    )
    mqtt_client.publish(MQTT_STATE_TOPIC, payload, qos=1, retain=True)


def parse_payload(payload: str) -> Dict[str, str]:
    """
    Парсинг payload сообщения MQTT.

    Формат: "servo1=6.5;servo2=8.0"

    Returns:
        Словарь с ключами servo1 и servo2
    """
    result = {}
    try:
        pairs = payload.split(";")
        for pair in pairs:
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                logger.warning(f"Пропущена некорректная пара: {pair}")
                continue
            key, value = pair.split("=", 1)
            result[key.strip()] = value.strip()
    except Exception as e:
        logger.error(f"Ошибка парсинга payload: {e}")
    return result


def on_message(client, userdata, message) -> None:
    """Callback при получении сообщения MQTT."""
    try:
        payload = message.payload.decode('utf-8')
        logger.info(f"Получено сообщение {message.topic}: {payload}")

        params = parse_payload(payload)
        if message.topic == MQTT_STATE_TOPIC:
            restore_angles(params)
        elif not set_targets(params):
            logger.error("Нет корректных значений для servo1 и servo2")

    except UnicodeDecodeError as e:
        logger.error(f"Ошибка декодирования сообщения: {e}")
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}", exc_info=True)


def on_connect(client, userdata, flags, reasonCode, properties) -> None:
    """Callback при подключении к MQTT брокеру."""
    if reasonCode == 0:
        logger.info("Успешно подключено к MQTT брокеру")
        client.subscribe([(MQTT_SUB_TOPIC, 0), (MQTT_STATE_TOPIC, 0)])
        client.publish(MQTT_AVAILABILITY_TOPIC, "online", qos=1, retain=True)
        logger.info(f"Подписка на топики: {MQTT_SUB_TOPIC}, {MQTT_STATE_TOPIC}")
    else:
        logger.error(f"Ошибка подключения к MQTT, код: {reasonCode}")


def on_disconnect(client, userdata, disconnect_flags, reasonCode, properties) -> None:
    """Callback при отключении от MQTT брокера."""
    logger.warning(f"Отключено от MQTT брокера, код: {reasonCode}")


def setup_mqtt() -> mqttClient.Client:
    """Инициализация MQTT клиента. Подключение и повторные попытки выполняет loop_forever."""
    try:
        client = mqttClient.Client(
            mqttClient.CallbackAPIVersion.VERSION2,
            MQTT_CLIENT_ID
        )
        client.username_pw_set(MQTT_USER, password=MQTT_PASSWORD)
        client.tls_set(ca_certs=MQTT_CA_CERT)
        client.will_set(MQTT_AVAILABILITY_TOPIC, "offline", qos=1, retain=True)

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect
        # Ограничиваем ресурсы и настраиваем повторные подключения
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.max_inflight_messages_set(8)
        client.max_queued_messages_set(32)

        client.connect_async(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
        logger.info(f"Подключение к MQTT брокеру {MQTT_HOST}:{MQTT_PORT}")

        return client
    except Exception as e:
        logger.error(f"Ошибка настройки MQTT: {e}")
        raise


def cleanup() -> None:
    """Корректное завершение работы и освобождение ресурсов."""
    global shutting_down

    logger.info("Завершение работы...")

    with motion_wakeup:
        shutting_down = True
        motion_wakeup.notify()
    if motion_thread:
        motion_thread.join(timeout=2)

    try:
        if mqtt_client:
            mqtt_client.publish(MQTT_AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
            mqtt_client.disconnect()
            logger.info("MQTT клиент отключен")
    except Exception as e:
        logger.error(f"Ошибка при отключении MQTT: {e}")

    try:
        for servo in servos.values():
            servo.pwm.stop()
        logger.info("ШИМ отключен")
    except OSError as e:
        logger.error(f"Ошибка при отключении ШИМ: {e}")


def handle_sigterm(signum, frame) -> None:
    """systemd останавливает сервис через SIGTERM: завершаемся так же, как по Ctrl+C."""
    raise KeyboardInterrupt


def main() -> None:
    """Основная функция программы."""
    global servos, mqtt_client, motion_thread

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        servos = setup_servos()

        motion_thread = threading.Thread(target=motion_loop, daemon=True)
        motion_thread.start()

        mqtt_client = setup_mqtt()

        # Блокирующий цикл MQTT снижает использование CPU
        logger.info("Сервис управления сервоприводами запущен (блокирующий режим MQTT)")
        if SMOOTH_ENABLED:
            logger.info(f"Плавное вращение включено (шаг: {SMOOTH_STEP}, задержка: {SMOOTH_DELAY}с, макс. шагов: {SMOOTH_MAX_STEPS})")
        mqtt_client.loop_forever(retry_first_connection=True)

    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
