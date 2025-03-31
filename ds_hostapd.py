import os
import sys
import time
import signal
import threading
import json
import numpy as np
from subprocess import CalledProcessError
import subprocess
from set_monitor import set_channel, set_monitor
from ds_utils import control_listener, append_and_resize_list, NoUpdateException
import re
import zmq

# Start hostapd, monitor for successful ACS, configure sniffer interface with correct channel,
# and publish station connection/authentication/disconnection events on ZMQ socket

def execute_cmd(cmd, stop_event):
    print(f"[host_apd][execute_cmd] :: enter")
    popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True, shell=True)

    for stdout_line in iter(popen.stdout.readline, ""):
        if stop_event.is_set():
            print("[host_apd][execute_cmd] :: main_stop_event.is_set()... break")
            break
        yield stdout_line

    popen.stdout.close()

# Start hostapd 2.7 version using configuration file located in the root directory at "./hostapd_config.conf"
def hostapd(cfg_d): # Config dictionary
    ACS_TIMEOUT = 5 # 5 seconds
    GOOD_REGEX = r'STA (\S+) (.+)' # Regex to search for new events occurring with a station
    # Command to start hostapd; need sudo because hostapd needs low-level access to Wi-Fi interfaces
    # Change this path to match the path of hostapd on your system
    HOSTAPD_CMD = 'sudo ./hostapd-2.7/hostapd/hostapd hostapd_config.conf'
    INTERFACE = cfg_d['nic_dev']

    # Init ZMQ DS publisher
    process_name = 'host_apd'
    z_context = zmq.Context()
    z_dc_pub = z_context.socket(zmq.PUB)
    z_dc_pub_bind_addr = cfg_d['dc_pub_bind_addr']
    z_dc_pub.bind(z_dc_pub_bind_addr)
    print(f"[{process_name}] zmq dc pub socket bound: {z_dc_pub_bind_addr}")

    # Start EC listener thread
    stop_event = threading.Event()
    control_listener_thread = threading.Thread(target=control_listener, name=f"{process_name}_control_listener", args=(process_name, z_context, cfg_d['ec_sub_addr'], stop_event))
    control_listener_thread.start()

    print(f'[{process_name}] setting monitor mode...')
    set_monitor() # Set monitor mode on secondary sniffer Wi-Fi interface
    
    while not stop_event.is_set():
        try:
            acs_start_time = time.time()
            HOSTAPD_RUNNING = False
                
            for std_out in execute_cmd(HOSTAPD_CMD, stop_event):
                if INTERFACE in std_out:

                    # Sometimes hostapd ACS doesn't initialize properly, so need to automatically restart it a few times
                    if not HOSTAPD_RUNNING:
                        # If broken (= if 5 sec timeout expires and ACS channel match isn't found)
                        if time.time() > acs_start_time + ACS_TIMEOUT:
                            os.system('sudo pkill -2 hostapd')
                            break
                        if "ACS-COMPLETED" in std_out:
                            match = re.search(r'ACS-COMPLETED\s*freq=(\d*)\s*channel=(\d*)', std_out) # Get selected channel
                            if match:
                                channel = match.group(2).strip()
                                set_channel(channel) # Set sniffer channel once ACS is complete
                                HOSTAPD_RUNNING = True
                                print(f'[host_apd] ACS: {match.group(0)}')
                            # Try to force all stations to reconnect using ACP over Ethernet
                            pub_string = f"{process_name};{time.time_ns()};HOSTAPD_INIT_COMPLETE;ACS={channel}"
                            print(pub_string)
                            z_dc_pub.send_string(pub_string)
                    
                    # Do normal hostapd tasks and wait for new items on stdout
                    else:
                        # Publish events containing device MAC address and associated information when a station connects or disconnects
                        match = re.search(GOOD_REGEX, std_out)
                        if match:
                            thelist = list(match.groups())
                            mac_address = thelist[0]
                            msg = thelist[1]

                            pub_string = f"{process_name};{time.time_ns()};HOSTAPD_STA_INFO;{mac_address};{msg}"
                            print(pub_string)
                            z_dc_pub.send_string(pub_string)

        except KeyboardInterrupt as ki:
            print(f"[{process_name}]: interrupt received, stopping: {str(ki)}")
            stop_event.set()
        except CalledProcessError as cpe:
            if cpe.returncode == 1:
                print('HOSTAPD could not be started: ', str(cpe))
        finally:
            if stop_event.is_set():
                control_listener_thread.join()
                z_dc_pub.close()
                z_context.term()
                print(f'[{process_name}] exiting cleanly...')
                break

# Start hostapd in standalone mode
if __name__ == '__main__':
    cfg_d = {}
    cfg_d['dc_pub_bind_addr'] = 'tcp://localhost:5552'
    cfg_d['ec_sub_addr'] = 'tcp://localhost:5550'
    cfg_d['nic_dev'] = 'wlp6s0' # Interface to use for AP

    hostapd(cfg_d)