# detect snapcast server silence
curl -s http://evolution:1780/jsonrpc \
  -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' | \
  python3 -c "
import json,sys
data = json.load(sys.stdin)
streams = {s['id']: s['status'] for s in data['result']['server']['streams']}
print(streams.get('default', 'idle'))"
