"""
Main audio monitoring class that handles signal detection and pipe management.
"""

import logging
import time
import threading
import subprocess
import pyaudio
import numpy as np
from typing import Optional
from config import *
from utils import (
    get_alsa_devices, 
    find_device_card_number, 
    create_audio_pipe, 
    destroy_audio_pipe, 
    calculate_rms,
    set_pcm_input_source,
    test_alsa_device_access,
    AudioPipeProcess
)

logger = logging.getLogger(__name__)

class AudioMonitor:
    """
    Main class for monitoring audio input and managing audio pipes.
    """
    
    def __init__(self, simulation_mode=False, skip_device_check=False, alsa_only=False):
        self.simulation_mode = simulation_mode
        self.skip_device_check = skip_device_check
        self.alsa_only = alsa_only
        self.audio = pyaudio.PyAudio() if not simulation_mode and not alsa_only else None
        self.stream: Optional[pyaudio.Stream] = None
        self.pipe_process: Optional[AudioPipeProcess] = None
        self.monitor_process: Optional[subprocess.Popen] = None
        self.is_running = False
        self.is_pipe_active = False
        self.last_signal_time = time.time()
        self.silence_start_time = None
        self.monitor_thread: Optional[threading.Thread] = None
        
        # Initialize audio format
        self.format = pyaudio.paInt16
        self.channels = CHANNELS
        self.sample_rate = SAMPLE_RATE
        self.chunk_size = CHUNK_SIZE
        
        # Simulation variables
        self.simulation_counter = 0
        # Signal pattern: 3 seconds signal, 35 seconds silence, 2 seconds signal, 35 seconds silence
        # At 100ms intervals (MONITORING_INTERVAL = 0.1), this means:
        # 30 readings for signal (3 seconds), 350 readings for silence (35 seconds)
        self.simulation_signal_pattern = [True] * 30 + [False] * 350 + [True] * 20 + [False] * 350
        
    def initialize_audio_stream(self) -> bool:
        """
        Initialize PyAudio stream for monitoring the source device.
        
        Returns:
            True if successful, False otherwise
        """
        if self.simulation_mode:
            logger.info("Running in simulation mode - no audio stream initialization needed")
            return True
        
        if self.alsa_only:
            logger.info("Running in ALSA-only mode - using arecord for monitoring")
            return True
        
        try:
            # Find source device
            source_card = find_device_card_number(SOURCE_DEVICE)
            if source_card is None:
                logger.error(f"Source device {SOURCE_DEVICE} not found")
                return False
            
            # Get device info
            device_info = None
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                if SOURCE_DEVICE.lower() in info['name'].lower():
                    device_info = info
                    break
            
            if device_info is None:
                logger.warning(f"Could not find PyAudio device for {SOURCE_DEVICE}. Using default input device for simulation.")
                # Try to use default input device for simulation
                try:
                    self.stream = self.audio.open(
                        format=self.format,
                        channels=self.channels,
                        rate=self.sample_rate,
                        input=True,
                        frames_per_buffer=self.chunk_size
                    )
                    logger.info(f"Initialized default audio stream for simulation")
                    return True
                except Exception as e:
                    logger.error(f"Failed to initialize default audio stream: {e}")
                    return False
            
            # Create input stream using the hardware-specific configuration
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.sample_rate,  # Now 48000 Hz
                input=True,
                input_device_index=device_info['index'],
                frames_per_buffer=self.chunk_size
            )
            
            logger.info(f"Initialized audio stream for {SOURCE_DEVICE}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize audio stream: {e}")
            return False
    
    def read_audio_data(self) -> Optional[np.ndarray]:
        """
        Read audio data from the input stream.
        
        Returns:
            Audio data as numpy array, or None if error
        """
        if self.simulation_mode:
            # Generate simulated audio data
            pattern_index = self.simulation_counter % len(self.simulation_signal_pattern)
            has_signal = self.simulation_signal_pattern[pattern_index]
            
            if has_signal:
                # Generate audio data with signal (random noise above threshold)
                audio_data = np.random.randint(-8000, 8000, self.chunk_size, dtype=np.int16)
            else:
                # Generate quiet audio data (below threshold)
                audio_data = np.random.randint(-100, 100, self.chunk_size, dtype=np.int16)
            
            self.simulation_counter += 1
            return audio_data
        
        if self.alsa_only:
            # Use arecord to get audio data (fall back to simulation if not available)
            try:
                if self.monitor_process is None or self.monitor_process.poll() is not None:
                    # Start or restart the monitoring process
                    cmd = [
                        'arecord', '-D', SOURCE_ALSA_DEVICE, '-f', 'S16_LE', 
                        '-r', str(SAMPLE_RATE), '-c', str(CHANNELS), '-t', 'raw'
                    ]
                    self.monitor_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                # Read chunk of data
                bytes_to_read = self.chunk_size * self.channels * 2  # 2 bytes per sample
                raw_data = self.monitor_process.stdout.read(bytes_to_read)
                
                if len(raw_data) == bytes_to_read:
                    audio_data = np.frombuffer(raw_data, dtype=np.int16)
                    return audio_data
                else:
                    return None
                    
            except FileNotFoundError:
                # arecord not available, fall back to simulation
                logger.warning("arecord not available, falling back to simulation mode")
                self.simulation_mode = True
                return self.read_audio_data()  # Recursive call to use simulation mode
            except Exception as e:
                logger.error(f"Failed to read audio data using arecord: {e}")
                return None
        
        try:
            if self.stream is None:
                return None
            
            raw_data = self.stream.read(self.chunk_size, exception_on_overflow=False)
            audio_data = np.frombuffer(raw_data, dtype=np.int16)
            return audio_data
            
        except Exception as e:
            logger.error(f"Failed to read audio data: {e}")
            return None
    
    def detect_signal(self, audio_data: np.ndarray) -> bool:
        """
        Detect if there's an audio signal present.
        
        Args:
            audio_data: Audio data to analyze
            
        Returns:
            True if signal detected, False if silence
        """
        if audio_data is None or len(audio_data) == 0:
            return False
        
        rms = calculate_rms(audio_data)
        
        # Use hysteresis to prevent rapid switching
        if self.is_pipe_active:
            # If pipe is active, use lower threshold to detect silence
            return rms > SILENCE_THRESHOLD
        else:
            # If pipe is not active, use higher threshold to detect signal
            return rms > SIGNAL_THRESHOLD
    
    def start_pipe(self) -> bool:
        """
        Start the audio pipe from source to destination.
        
        Returns:
            True if successful, False otherwise
        """
        if self.is_pipe_active:
            return True
        
        self.pipe_process = create_audio_pipe(SOURCE_DEVICE, DESTINATION_DEVICE)
        if self.pipe_process is not None:
            self.is_pipe_active = True
            self.last_signal_time = time.time()
            self.silence_start_time = None
            logger.info("Audio pipe started")
            return True
        
        return False
    
    def stop_pipe(self) -> bool:
        """
        Stop the audio pipe.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.is_pipe_active:
            return True
        
        success = destroy_audio_pipe(self.pipe_process)
        if success:
            self.pipe_process = None
            self.is_pipe_active = False
            self.silence_start_time = None
            logger.info("Audio pipe stopped")
        
        return success
    
    def monitor_audio(self):
        """
        Main monitoring loop that runs in a separate thread.
        """
        logger.info("Starting audio monitoring")
        
        while self.is_running:
            try:
                # Read audio data
                audio_data = self.read_audio_data()
                if audio_data is None:
                    time.sleep(MONITORING_INTERVAL)
                    continue
                
                # Detect signal
                has_signal = self.detect_signal(audio_data)
                current_time = time.time()
                
                if has_signal:
                    # Signal detected
                    self.last_signal_time = current_time
                    self.silence_start_time = None
                    
                    if not self.is_pipe_active:
                        logger.info("Audio signal detected, starting pipe")
                        self.start_pipe()
                else:
                    # Silence detected
                    if self.is_pipe_active:
                        if self.silence_start_time is None:
                            self.silence_start_time = current_time
                            logger.info("Silence detected, starting timeout")
                        elif current_time - self.silence_start_time > SILENCE_TIMEOUT:
                            logger.info(f"Silence timeout ({SILENCE_TIMEOUT}s) reached, stopping pipe")
                            self.stop_pipe()
                
                # Check if pipe process is still alive
                if self.is_pipe_active and self.pipe_process and not self.pipe_process.is_alive():
                    logger.warning("Audio pipe process died, resetting state")
                    self.is_pipe_active = False
                    self.pipe_process = None
                
                time.sleep(MONITORING_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(MONITORING_INTERVAL)
    
    def start(self) -> bool:
        """
        Start the audio monitor.
        
        Returns:
            True if successful, False otherwise
        """
        if self.is_running:
            logger.warning("Audio monitor is already running")
            return True
        
        # Check if devices are available (skip check if using direct hardware paths)
        available_devices = get_alsa_devices()
        logger.info(f"Available ALSA devices: {available_devices}")
        
        # Since we're using direct hardware paths (plughw:1, plughw:4), we can proceed
        # even if the device names aren't detected through aplay -l
        if SOURCE_DEVICE not in available_devices:
            logger.warning(f"Source device {SOURCE_DEVICE} not detected, but proceeding with {SOURCE_ALSA_DEVICE}")
        
        if DESTINATION_DEVICE not in available_devices:
            logger.warning(f"Destination device {DESTINATION_DEVICE} not detected, but proceeding with {DESTINATION_ALSA_DEVICE}")
        
        # Test hardware device access (unless skipped)
        if not self.skip_device_check:
            logger.info(f"Testing access to hardware devices...")
            source_accessible = test_alsa_device_access(SOURCE_ALSA_DEVICE, 'capture')
            dest_accessible = test_alsa_device_access(DESTINATION_ALSA_DEVICE, 'playback')
            
            if not source_accessible:
                logger.error(f"Cannot access source device {SOURCE_ALSA_DEVICE}")
                return False
            else:
                logger.info(f"Source device {SOURCE_ALSA_DEVICE} is accessible")
            
            if not dest_accessible:
                logger.error(f"Cannot access destination device {DESTINATION_ALSA_DEVICE}")
                return False
            else:
                logger.info(f"Destination device {DESTINATION_ALSA_DEVICE} is accessible")
        else:
            logger.info(f"Skipping device accessibility checks")
        
        # Set the PCM input source to IEC958 In
        if not set_pcm_input_source(SOURCE_ALSA_DEVICE, SOURCE_INPUT):
            logger.warning(f"Could not set PCM input source to {SOURCE_INPUT}")
        
        # Initialize audio stream
        if not self.initialize_audio_stream():
            return False
        
        # Start monitoring thread
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self.monitor_audio, daemon=True)
        self.monitor_thread.start()
        
        logger.info("Audio monitor started successfully")
        return True
    
    def stop(self):
        """
        Stop the audio monitor.
        """
        if not self.is_running:
            return
        
        logger.info("Stopping audio monitor")
        self.is_running = False
        
        # Stop any active pipe
        self.stop_pipe()
        
        # Wait for monitoring thread to finish
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        # Clean up audio stream
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        
        # Clean up monitoring process
        if self.monitor_process and self.monitor_process.poll() is None:
            self.monitor_process.terminate()
            self.monitor_process.wait()
            self.monitor_process = None
        
        logger.info("Audio monitor stopped")
    
    def get_status(self) -> dict:
        """
        Get current status of the audio monitor.
        
        Returns:
            Dictionary containing status information
        """
        current_time = time.time()
        
        status = {
            'is_running': self.is_running,
            'is_pipe_active': self.is_pipe_active,
            'source_device': SOURCE_DEVICE,
            'destination_device': DESTINATION_DEVICE,
            'last_signal_time': self.last_signal_time,
            'silence_duration': 0,
            'time_until_timeout': 0
        }
        
        if self.is_pipe_active and self.silence_start_time:
            silence_duration = current_time - self.silence_start_time
            status['silence_duration'] = silence_duration
            status['time_until_timeout'] = max(0, SILENCE_TIMEOUT - silence_duration)
        
        return status
    
    def __del__(self):
        """Destructor to clean up resources."""
        self.stop()
        if hasattr(self, 'audio') and self.audio is not None:
            self.audio.terminate()
