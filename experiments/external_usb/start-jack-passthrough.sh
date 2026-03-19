#!/bin/bash

cpufreq-set -g performance

RT_PRIORITY=80
CPU_CORES="2,3"
CAPTURE_DEVICE="hw:ICUSBAUDIO7D,0"
PLAYBACK_DEVICE="hw:sndrpimerusamp,0"
RATE=48000
PERIOD_SIZE=1024
NUM_PERIODS=2

JACK_NO_AUDIO_RESERVATION=1 \
taskset -c $CPU_CORES \
  chrt -f $RT_PRIORITY \
  jackd -R -P $RT_PRIORITY -d alsa \
    -C $CAPTURE_DEVICE \
    -P $PLAYBACK_DEVICE \
    -r $RATE -p $PERIOD_SIZE -n $NUM_PERIODS &
JACK_PID=$!

sleep 2

echo "connecting pipeline"
jack_connect system:capture_1 system:playback_1
jack_connect system:capture_2 system:playback_2
jack_lsp -l

echo "press Ctrl+C to stop"
wait $JACK_PID
