#!/bin/bash
# bg.sh NAME "cmd" : run detached on PC2, log to bg_NAME.log
setsid nohup bash -c "$2" > bg_$1.log 2>&1 < /dev/null &
echo started $1
