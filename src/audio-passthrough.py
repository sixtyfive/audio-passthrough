#!/usr/bin/env python3
"""
ALSA Audio Monitor - Command-line program for monitoring audio input and managing pipes.

This program monitors the IEC958 In input on ALSA soundcard ICUSBAUDIO7D for audio signals,
and creates/destroys audio pipes to snd_rpi_merus_amp based on signal presence and silence detection.
"""

import argparse
import logging
import signal
import sys
import time
from audio_monitor import AudioMonitor
from config import *

# Global monitor instance for signal handling
monitor = None

def setup_logging(log_level: str = LOG_LEVEL):
    """
    Setup logging configuration.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )

def signal_handler(signum, frame):
    """
    Handle termination signals gracefully.
    
    Args:
        signum: Signal number
        frame: Current stack frame
    """
    global monitor
    
    print(f"\nReceived signal {signum}, shutting down...")
    
    if monitor:
        monitor.stop()
    
    sys.exit(0)

def print_status(monitor: AudioMonitor):
    """
    Print current status of the audio monitor.
    
    Args:
        monitor: AudioMonitor instance
    """
    status = monitor.get_status()
    
    print(f"\n{'='*60}")
    print(f"Audio Monitor Status")
    print(f"{'='*60}")
    print(f"Running: {'Yes' if status['is_running'] else 'No'}")
    print(f"Pipe Active: {'Yes' if status['is_pipe_active'] else 'No'}")
    print(f"Source Device: {status['source_device']}")
    print(f"Destination Device: {status['destination_device']}")
    
    if status['last_signal_time']:
        last_signal_ago = time.time() - status['last_signal_time']
        print(f"Last Signal: {last_signal_ago:.1f} seconds ago")
    
    if status['silence_duration'] > 0:
        print(f"Silence Duration: {status['silence_duration']:.1f} seconds")
        print(f"Time Until Timeout: {status['time_until_timeout']:.1f} seconds")
    
    print(f"{'='*60}")

def main():
    """
    Main function for the ALSA audio monitor.
    """
    global monitor
    
    parser = argparse.ArgumentParser(
        description="Monitor ALSA audio input and manage audio pipes based on signal detection"
    )
    
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default=LOG_LEVEL,
        help='Set logging level'
    )
    
    parser.add_argument(
        '--status-interval',
        type=float,
        default=10.0,
        help='Interval in seconds for status updates (0 to disable)'
    )
    
    parser.add_argument(
        '--silence-threshold',
        type=float,
        default=SILENCE_THRESHOLD,
        help='RMS threshold below which audio is considered silent'
    )
    
    parser.add_argument(
        '--signal-threshold',
        type=float,
        default=SIGNAL_THRESHOLD,
        help='RMS threshold above which audio is considered present'
    )
    
    parser.add_argument(
        '--silence-timeout',
        type=float,
        default=SILENCE_TIMEOUT,
        help='Seconds of silence before destroying pipe'
    )
    
    parser.add_argument(
        '--test-devices',
        action='store_true',
        help='Test device availability and exit'
    )
    
    parser.add_argument(
        '--simulation',
        action='store_true',
        help='Run in simulation mode (no real audio hardware required)'
    )
    
    parser.add_argument(
        '--skip-device-check',
        action='store_true',
        help='Skip hardware device accessibility checks'
    )
    
    parser.add_argument(
        '--alsa-only',
        action='store_true',
        help='Use ALSA tools only for monitoring (no PyAudio)'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    # Update configuration with command-line arguments
    import config
    config.SILENCE_THRESHOLD = args.silence_threshold
    config.SIGNAL_THRESHOLD = args.signal_threshold
    config.SILENCE_TIMEOUT = args.silence_timeout
    
    # Test devices if requested
    if args.test_devices:
        from utils import get_alsa_devices, find_device_card_number
        
        print("Testing ALSA device availability...")
        devices = get_alsa_devices()
        print(f"Available devices: {devices}")
        
        source_card = find_device_card_number(SOURCE_DEVICE)
        dest_card = find_device_card_number(DESTINATION_DEVICE)
        
        print(f"Source device '{SOURCE_DEVICE}': {'Found (card {})'.format(source_card) if source_card is not None else 'Not found'}")
        print(f"Destination device '{DESTINATION_DEVICE}': {'Found (card {})'.format(dest_card) if dest_card is not None else 'Not found'}")
        
        return
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and start monitor
    monitor = AudioMonitor(simulation_mode=args.simulation, skip_device_check=args.skip_device_check, alsa_only=args.alsa_only)
    
    print(f"Starting ALSA Audio Monitor")
    if args.simulation:
        print("Running in SIMULATION MODE - no real audio hardware required")
    print(f"Source: {SOURCE_DEVICE} ({SOURCE_INPUT}) -> {SOURCE_ALSA_DEVICE}")
    print(f"Destination: {DESTINATION_DEVICE} -> {DESTINATION_ALSA_DEVICE}")
    print(f"Sample Rate: {SAMPLE_RATE} Hz")
    print(f"Buffer Size: {BUFFER_SIZE}")
    print(f"Silence Threshold: {config.SILENCE_THRESHOLD}")
    print(f"Signal Threshold: {config.SIGNAL_THRESHOLD}")
    print(f"Silence Timeout: {config.SILENCE_TIMEOUT} seconds")
    print(f"Press Ctrl+C to stop")
    
    if not monitor.start():
        logger.error("Failed to start audio monitor")
        sys.exit(1)
    
    # Main status loop
    try:
        last_status_time = time.time()
        
        while True:
            current_time = time.time()
            
            # Print status at intervals
            if args.status_interval > 0 and current_time - last_status_time >= args.status_interval:
                print_status(monitor)
                last_status_time = current_time
            
            time.sleep(1)
            
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        monitor.stop()
        sys.exit(1)

if __name__ == "__main__":
    main()
