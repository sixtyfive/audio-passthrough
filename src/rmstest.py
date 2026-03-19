from pyalsa import pcm
import numpy as np
import time

# Open the device
dev = pcm.PCM(type=pcm.PCM_CAPTURE, mode=pcm.PCM_NONBLOCK, card='hw:2,0')

# Set parameters
dev.setchannels(1)
dev.setrate(44100)
dev.setformat(pcm.PCM_FORMAT_S16_LE)
dev.setperiodsize(1024)

print("🎧 Monitoring audio with pyalsa...")

last_active = False
THRESHOLD = 500

try:
  while True:
    # Try to read one period
    length, data = dev.read()
    if length:
      samples = np.frombuffer(data, dtype=np.int16)
      rms = np.sqrt(np.mean(samples**2))

      if rms > THRESHOLD and not last_active:
        print(f"🔊 Sound started! RMS: {rms:.1f}")
        last_active = True
      elif rms <= THRESHOLD and last_active:
        print(f"🔇 Sound stopped. RMS: {rms:.1f}")
        last_active = False

    time.sleep(0.05)

except KeyboardInterrupt:
  print("\n🛑 Monitoring stopped by user.")

