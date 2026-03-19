import alsaaudio
import numpy as np
import time

inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE,
                    mode=alsaaudio.PCM_NONBLOCK,
                    device='hw:1,0',
                    channels=1,
                    rate=44100,
                    format=alsaaudio.PCM_FORMAT_S16_LE,
                    periodsize=1024)

print("Monitoring audio on Line In...")

THRESHOLD = 500
last_active = False

try:
  while True:
    length, data = inp.read()
    if length:
      samples = np.frombuffer(data, dtype=np.int16)

      if samples.size == 0:
        continue

      rms = np.sqrt(np.mean(samples**2))

      if rms > THRESHOLD and not last_active:
        print(f"Sound started! RMS: {rms:.1f}")
        last_active = True
      elif rms <= THRESHOLD and last_active:
        print(f"Sound stopped. RMS: {rms:.1f}")
        last_active = False

    time.sleep(0.05)

except KeyboardInterrupt:
  print("\nMonitoring stopped by user.")

