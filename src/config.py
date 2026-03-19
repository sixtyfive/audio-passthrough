"""
Configuration settings for the ALSA audio monitor.
"""

# ALSA Device Configuration
SOURCE_DEVICE = "ICUSBAUDIO7D"
SOURCE_INPUT = "IEC958 In"
DESTINATION_DEVICE = "snd_rpi_merus_amp"

# Hardware-specific ALSA device paths
SOURCE_ALSA_DEVICE = "plughw:1"
DESTINATION_ALSA_DEVICE = "plughw:4"

# Audio Detection Parameters
SAMPLE_RATE = 48000  # Updated to match your working parameters
CHUNK_SIZE = 1024
CHANNELS = 2
FORMAT_BITS = 16
BUFFER_SIZE = 20000  # New buffer parameters from your testing

# Signal Detection Thresholds
SILENCE_THRESHOLD = 0.01  # RMS threshold below which audio is considered silent
SIGNAL_THRESHOLD = 0.02   # RMS threshold above which audio is considered present
SILENCE_TIMEOUT = 30.0    # Seconds of silence before destroying pipe

# Monitoring Configuration
MONITORING_INTERVAL = 0.1  # Seconds between audio level checks
PIPE_CHECK_INTERVAL = 1.0  # Seconds between pipe status checks

# Logging Configuration
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
