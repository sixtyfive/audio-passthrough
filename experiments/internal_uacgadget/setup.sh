#!/bin/bash
# Configures Raspberry Pi as a USB Audio Device

# 1. Enter ConfigFS
cd /sys/kernel/config/usb_gadget/
mkdir -p g_audio
cd g_audio

# 2. Device Identity (UAC2 Gadget)
echo 0x1d6b > idVendor  # Linux Foundation
echo 0x0104 > idProduct # Multifunction Composite Gadget
echo 0x0100 > bcdDevice # v1.0.0
echo 0x0200 > bcdUSB    # USB 2.0
mkdir -p strings/0x409
echo "f00feef00feef00f" > strings/0x409/serialnumber
echo "Jonathan" > strings/0x409/manufacturer
echo "Bedroom Speakers" > strings/0x409/product

# 3. Create Audio Function (UAC2)
mkdir -p functions/uac2.usb0
# Optional: Adjust channels (default 2) or sample rate if needed
# echo 44100,48000,96000,192000 > functions/uac2.usb0/c_srate # capture
# echo 44100,48000,96000,192000 > functions/uac2.usb0/p_srate # playback
echo 44100,48000,96000 > functions/uac2.usb0/c_srate # capture
echo 44100,48000 > functions/uac2.usb0/p_srate # playback

# Set Sample Size to 4 bytes (enables 24-bit and 32-bit support)
# echo 4 > functions/uac2.usb0/p_ssize
# echo 4 > functions/uac2.usb0/c_ssize

# 4. Create Configuration
mkdir -p configs/c.1/strings/0x409
echo "Audio Config" > configs/c.1/strings/0x409/configuration
ln -s functions/uac2.usb0 configs/c.1/

# 5. Enable the Gadget (Use the name from /sys/class/udc)
ls /sys/class/udc > UDC
