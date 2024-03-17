#!/bin/bash
sleep 60 && v4l2rtspserver -W 1024 -H 768 -F 15 -P 8554 -U user:passwd /dev/video0
