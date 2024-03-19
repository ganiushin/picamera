# send to mqtt message servo1=1;servo2=12
import paho.mqtt.client as mqttClient
import ssl
from time import sleep
import RPi.GPIO as GPIO
import sys

servo1_pin = 2 #6 - 10
servo2_pin = 14 #1 - 12.5

GPIO.setmode(GPIO.BCM)
GPIO.setup(servo1_pin, GPIO.OUT)
GPIO.setup(servo2_pin, GPIO.OUT)

p1 = GPIO.PWM(servo1_pin, 50)
p2 = GPIO.PWM(servo2_pin, 50)

p1.start(0)
p2.start(0)

port = 8883
host = "smarthome.half.ws"
user = "user"
password = "passwd"
ca = "/home/pi/isrgrootx1.pem"
sub_topic = "b1/camera_rotate"

def on_message(client, userdata, message):
    rotate = message.payload.decode()
    res = {x[0]:x[1] for x in [y.split("=") for y in rotate.split(";") if len(y)]}
    p1.ChangeDutyCycle(float(res["servo1"]))
    p2.ChangeDutyCycle(float(res["servo2"]))
    #p1.ChangeDutyCycle(5)
    #p2.ChangeDutyCycle(5)
    sleep(0.1)
    p1.ChangeDutyCycle(0)
    p2.ChangeDutyCycle(0)


def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))

    client.subscribe(
            [
                (sub_topic, 0),
            ]
    )

client = mqttClient.Client(mqttClient.CallbackAPIVersion.VERSION1, "Python")
client.username_pw_set(user, password=password)
client.tls_set(ca_certs=ca)
client.tls_insecure_set(True)
client.connect(host, port, 60)
client.on_connect = on_connect
client.loop_start()

try:
    while True:
        client.on_message=on_message
        sleep(0.1)

except KeyboardInterrupt:

    client.loop_stop()
    p1.stop()
    p2.stop()
    GPIO.cleanup()
