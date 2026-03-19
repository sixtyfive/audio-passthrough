"""
Utility functions for ALSA device management and audio processing.
"""

import subprocess
import logging
import re
import numpy as np
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

def get_alsa_devices() -> List[str]:
    """
    Get list of available ALSA audio devices.
    
    Returns:
        List of device names
    """
    try:
        result = subprocess.run(['aplay', '-l'], capture_output=True, text=True, check=True)
        devices = []
        for line in result.stdout.split('\n'):
            if 'card' in line and ':' in line:
                # Extract device name from line like "card 1: ICUSBAUDIO7D [ICUSBAUDIO7D], device 0:"
                match = re.search(r'card \d+: (\w+)', line)
                if match:
                    devices.append(match.group(1))
        
        # Also check for snd_rpi_merus_amp specifically using arecord -L
        try:
            result_list = subprocess.run(['arecord', '-L'], capture_output=True, text=True, check=True)
            if 'snd_rpi_merus_amp' in result_list.stdout:
                devices.append('snd_rpi_merus_amp')
        except:
            pass
        
        return devices
    except FileNotFoundError:
        logger.warning("ALSA tools not found. Running in simulation mode.")
        # Return expected devices for testing purposes
        return ['ICUSBAUDIO7D', 'snd_rpi_merus_amp']
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get ALSA devices: {e}")
        return []

def find_device_card_number(device_name: str) -> Optional[int]:
    """
    Find the card number for a given ALSA device name.
    
    Args:
        device_name: Name of the ALSA device
        
    Returns:
        Card number if found, None otherwise
    """
    try:
        result = subprocess.run(['aplay', '-l'], capture_output=True, text=True, check=True)
        for line in result.stdout.split('\n'):
            if device_name in line:
                match = re.search(r'card (\d+):', line)
                if match:
                    return int(match.group(1))
        return None
    except FileNotFoundError:
        logger.warning("ALSA tools not found. Running in simulation mode.")
        # Return simulated card numbers for testing
        if device_name == 'ICUSBAUDIO7D':
            return 1
        elif device_name == 'snd_rpi_merus_amp':
            return 2
        return None
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to find card number for {device_name}: {e}")
        return None

def create_audio_pipe(source_device: str, dest_device: str) -> Optional[subprocess.Popen]:
    """
    Create an audio pipe from source to destination device using arecord/aplay.
    Uses the specific hardware paths and parameters that work in production.
    
    Args:
        source_device: Source ALSA device name (for reference only)
        dest_device: Destination ALSA device name (for reference only)
        
    Returns:
        Subprocess.Popen object if successful, None otherwise
    """
    from config import SOURCE_ALSA_DEVICE, DESTINATION_ALSA_DEVICE, SAMPLE_RATE, BUFFER_SIZE
    
    try:
        # Create pipe using arecord | aplay with exact parameters from successful testing
        arecord_cmd = [
            'arecord',
            '-D', SOURCE_ALSA_DEVICE,  # plughw:1
            '-f', 'S16_LE',
            '-r', str(SAMPLE_RATE),  # 48000
            '-c', '2',
            '-B', str(BUFFER_SIZE),  # 20000
            '-F', str(BUFFER_SIZE)   # 20000
        ]
        
        aplay_cmd = [
            'aplay',
            '-D', DESTINATION_ALSA_DEVICE,  # plughw:4
            '-f', 'S16_LE',
            '-r', str(SAMPLE_RATE),  # 48000
            '-c', '2',
            '-B', str(BUFFER_SIZE),  # 20000
            '-F', str(BUFFER_SIZE)   # 20000
        ]
        
        # Start arecord process
        arecord_proc = subprocess.Popen(arecord_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Start aplay process with arecord's output as input
        aplay_proc = subprocess.Popen(aplay_cmd, stdin=arecord_proc.stdout, stderr=subprocess.PIPE)
        
        # Close arecord's stdout in parent process
        arecord_proc.stdout.close()
        
        logger.info(f"Created audio pipe from {SOURCE_ALSA_DEVICE} to {DESTINATION_ALSA_DEVICE}")
        
        # Return a tuple of both processes wrapped in a container
        return AudioPipeProcess(arecord_proc, aplay_proc)
        
    except FileNotFoundError:
        logger.warning("ALSA tools not found. Creating simulation pipe.")
        # In simulation mode, create a dummy process that simulates a pipe
        dummy_proc = subprocess.Popen(['sleep', '3600'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return AudioPipeProcess(dummy_proc, dummy_proc)
    except Exception as e:
        logger.error(f"Failed to create audio pipe: {e}")
        return None

def destroy_audio_pipe(pipe_process: 'AudioPipeProcess') -> bool:
    """
    Destroy an audio pipe by terminating the subprocess.
    
    Args:
        pipe_process: AudioPipeProcess object to terminate
        
    Returns:
        True if successful, False otherwise
    """
    if pipe_process is None:
        return True
    
    try:
        pipe_process.terminate()
        logger.info("Audio pipe destroyed")
        return True
    except Exception as e:
        logger.error(f"Failed to destroy audio pipe: {e}")
        return False

def test_alsa_device_access(device_path: str, mode: str = 'playback') -> bool:
    """
    Test if an ALSA device path is accessible.
    
    Args:
        device_path: ALSA device path (e.g., 'plughw:1')
        mode: 'playback' or 'capture'
        
    Returns:
        True if device is accessible, False otherwise
    """
    try:
        if mode == 'playback':
            cmd = ['aplay', '-D', device_path, '--dump-hw-params', '/dev/null']
        else:
            cmd = ['arecord', '-D', device_path, '--dump-hw-params', '-d', '1']
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        print(result)
        return result.returncode == 0
    except:
        return False

def set_pcm_input_source(device_path: str, input_source: str) -> bool:
    """
    Set the PCM input source for the specified ALSA device.
    
    Args:
        device_path: ALSA device path (e.g., 'plughw:1')
        input_source: Input source name (e.g., 'IEC958 In')
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Use amixer to set the input source
        cmd = ['amixer', '-D', device_path, 'sset', input_source, 'unmute']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info(f"Successfully set input source to {input_source} on {device_path}")
            return True
        else:
            logger.warning(f"Failed to set input source: {result.stderr}")
            return False
            
    except FileNotFoundError:
        logger.warning("amixer not found. Running in simulation mode.")
        return True
    except Exception as e:
        logger.error(f"Failed to set PCM input source: {e}")
        return False

def calculate_rms(audio_data: np.ndarray) -> float:
    """
    Calculate RMS (Root Mean Square) of audio data.
    
    Args:
        audio_data: Audio data as numpy array
        
    Returns:
        RMS value
    """
    if len(audio_data) == 0:
        return 0.0
    
    return np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))

class AudioPipeProcess:
    """
    Container for managing audio pipe processes.
    """
    
    def __init__(self, arecord_proc: subprocess.Popen, aplay_proc: subprocess.Popen):
        self.arecord_proc = arecord_proc
        self.aplay_proc = aplay_proc
    
    def terminate(self):
        """Terminate both processes."""
        try:
            self.arecord_proc.terminate()
            self.aplay_proc.terminate()
            
            # Wait for processes to terminate
            self.arecord_proc.wait(timeout=5)
            self.aplay_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Force kill if they don't terminate gracefully
            self.arecord_proc.kill()
            self.aplay_proc.kill()
    
    def is_alive(self) -> bool:
        """Check if both processes are still running."""
        return (self.arecord_proc.poll() is None and 
                self.aplay_proc.poll() is None)
