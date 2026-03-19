# detect s/pdif carrier loss
get_trigger_time() {
  awk '/trigger_time:/ {print $2}' /proc/asound/card3/pcm0c/sub0/status
}
LAST_TRIGGER=$(get_trigger_time)
while true; do
  CURRENT_TRIGGER=$(get_trigger_time)
  if [ "$CURRENT_TRIGGER" != "$LAST_TRIGGER" ]; then
    echo "Stream restarted — carrier loss detected"
    LAST_TRIGGER=$CURRENT_TRIGGER
    # trigger switch away from JACK
  fi
  sleep 0.2
done
