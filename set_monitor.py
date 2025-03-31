#import yo_mama as mi_mama
from execute_utils import execute
import subprocess
import re
import os

interfaces = ['your_device_here']

def load_drivers():
    print('[load_drivers]: Loading drivers...')
    # Put any custom device driver loading code here

def set_monitor():
    load_drivers()
    
    try:
        for interface in interfaces:
            lines = ['sudo airmon-ng check kill',                  # may or may not be needed
                    f'sudo ip link set {interface} down',          # turn it off
                    f'sudo iw dev {interface} set type monitor',   # set card to monitor mode
                    f'sudo ip link set {interface} up',            # turn it back on
                    #f'sudo iw dev {interface} set channel {channel}'     # set the channel here, not in wireshark
                    ]
            for line in lines:
                print(f'{interface} >>>>>>>>>>>>>>>>>>>>>>>', line)
                for output_line in execute(line, None):
                    print(output_line)

    except Exception as e:
        print('SET_MONITOR ERROR: ' + str(e))

def set_channel(channel):
    try:
        for interface in interfaces:
            lines = [
                    f'sudo iw dev {interface} set channel {channel}'     # set the channel here, not in wireshark
                    ]
            for line in lines:
                print(f'{interface} >>>>>>>>>>>>>>>>>>>>>>>', line)
                for output_line in execute(line, None):
                    print(output_line)

    except Exception as e:
        print('SET_MONITOR ERROR: ' + str(e))

if __name__ == '__main__':
    set_monitor()





      