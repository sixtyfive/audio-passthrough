# detect alsa silence
THRESHOLD=0.001
while true; do
  arecord -D hw:3,0 -f S16_LE -r 48000 -c 2 -d 1 -q /tmp/probe.wav
  RMS=$(sox /tmp/probe.wav -n stat 2>&1 | grep -E 'RMS\s+amplitude' | awk '{print $3}')
  ACTIVE=$(awk -v r="$RMS" -v t="$THRESHOLD" 'BEGIN{print (r+0 > t+0) ? "yes" : "no"}')
  echo "$(date): signal=${ACTIVE} rms=${RMS}"
  sleep 5
done
