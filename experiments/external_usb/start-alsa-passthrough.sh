#!/bin/bash

cpufreq-set -g performance

P=80
IN=16
OUT=24
R=48000
B=9000 # supposed to be buffer time (but verbose output only shows buffer_size and sets it to either 240 or, if this value is > ~9000, to 480)
F=2500 # period_time in µs (checked - not being changed by ALSA)

taskset -c 2 chrt -f $P arecord -v -M -D hw:CARD=ICUSBAUDIO7D,DEV=0 -f S${IN}_LE -r $R -c 2 -B $B -F $F | \
taskset -c 3 chrt -f $P aplay -v -M -D plughw:CARD=sndrpimerusamp,DEV=0 -f S${OUT}_LE -r $R -c 2 -B $B -F $F
