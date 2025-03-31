import time
import re
import threading
import json
import numpy as np
import zmq
from ds_utils import control_listener
import random

# Randomly simulate ath9k transmission queue statistics per Wi-Fi Access Category (AC) from driver using debugfs then publish on ZMQ socket

"""
Expected Input 

(VO):  qnum: 3 qdepth:  0 ampdu-depth:  0 pending:   0
(VI):  qnum: 2 qdepth:  0 ampdu-depth:  0 pending:   0
(BE):  qnum: 1 qdepth:  0 ampdu-depth:  0 pending:   0
(BK):  qnum: 0 qdepth:  0 ampdu-depth:  0 pending:   0
(CAB): qnum: 8 qdepth:  0 ampdu-depth:  0 pending:   0
"""

Q_MATCH = r"\(([A-Z]+)\):\s+(\S+):\s+(\d+)\s+(\S+):\s+(\d+)\s+(\S+):\s+(\d+)\s+(\S+):\s+(\d+)"

# Expected row indices for queue type
VO = 0   # VOice
VI = 1   # VIdeo
BE = 2   # Best Effort
BK = 3   # BacKground
CAB = 4

# Expected column indices for every field in each queue's row
AC = 0
QNUM = 1
QNUM_VAL = 2
QDEPTH = 3
QDEPTH_VAL = 4
ADEPTH = 5
ADEPTH_VAL = 6
PENDING = 7
PENDING_VAL = 8

def sim_hwq_stats(cfg_d):
    process_name = cfg_d['process_name']
    sanity_check_time = time.time()
    sanity_check_counter = 0
    path = '/sys/kernel/debug/ieee80211/phy0/ath9k/queues'
    print(f"[{process_name}] Starting...")

    hwq_stats_dict = {}
    
    z_context = zmq.Context()
    z_dc_pub = z_context.socket(zmq.PUB)
    z_dc_pub_bind_addr = cfg_d['pub_bind_addr']
    z_dc_pub.bind(z_dc_pub_bind_addr)
    print(f"[{process_name}] ZMQ DS pub socket bound: {z_dc_pub_bind_addr}")

    # Start EC listener thread
    stop_event = threading.Event()
    control_listener_thread = threading.Thread(target=control_listener, name=f"{process_name}_control_listener", args=(process_name, z_context, cfg_d['ec_sub_addr'], stop_event))
    control_listener_thread.start()

    # Set up max capped data source freq
    max_freq = cfg_d['max_data_freq'] # hz
    if max_freq != 0:
        max_loop_time = 1.0 / max_freq
    else:
        max_loop_time = 0.0
    loop_sleep_diff = max_loop_time
    slept_loop_time = 0.0
    sleep_diff = max_loop_time

    # set up process time measurement
    prev_proc_time = time.process_time_ns()

    # set up loop freq measurement
    update_hz_list = list()
    n_running_avg = 5000
    print_counter = 0

    z_dc_pub.send_string('asdasdasdasd') # Establish connection to subscribers

    # Read queue information from ath9k debugfs

    loop_start_time = time.time()
    loop_stop_time = time.time() + 1
    while not stop_event.is_set():
        try:
            ts = time.time_ns()
            proc_time = time.process_time_ns()
            dt_proc_time = proc_time - prev_proc_time
            prev_proc_time = proc_time

            # Add current info to dict for serializing and publishing to DS
            pub_dict = {f'{process_name}_ts': ts,
                        f'{process_name}_pt': dt_proc_time,

                        'vo_qdepth_val':  random.randint(0, 30),
                        'vo_adepth_val':  random.randint(0, 30),
                        'vo_pending_val': random.randint(0, 30),

                        'vi_qdepth_val':  random.randint(0, 30),
                        'vi_adepth_val':  random.randint(0, 30),
                        'vi_pending_val': random.randint(0, 30),

                        'be_qdepth_val':  random.randint(0, 30),
                        'be_adepth_val':  random.randint(0, 30),
                        'be_pending_val': random.randint(0, 30),

                        'bk_qdepth_val':  random.randint(0, 30),
                        'bk_adepth_val':  random.randint(0, 30),
                        'bk_pending_val': random.randint(0, 30)
                        
                        }
            dict_string = json.dumps(pub_dict)
            header = f"{process_name};{ts};DATA;"
            pub_string = f"{header}{dict_string}"
            z_dc_pub.send_string(pub_string)

            loop_calc_time = time.time()

        except KeyboardInterrupt as ki:
            stop_event.set()
        finally:
            try:
                # Sleep to maintain consistent update frequency if needed
                if loop_sleep_diff > 0.0:
                    time.sleep(loop_sleep_diff)

                loop_stop_time = time.time()
                
                # Loop freq adjustment calculations
                loop_calc_diff = loop_calc_time - loop_start_time # Time spent processing in loop
                loop_time = loop_stop_time - loop_start_time # Time spent overall on loop including sleep time
                
                loop_slept_freq = 1.0 / (loop_time)
                update_hz_list.append(loop_slept_freq)

                if len(update_hz_list) > n_running_avg:
                    del update_hz_list[0]

                if max_freq != 0:
                    loop_sleep_diff = max_loop_time - loop_calc_diff # Subtract processing time from desired overall loop time to compensate

                # Print out info for standalone operation; need to customize string to print below
                if cfg_d['standalone']:
                    print_counter += 1
                    if print_counter > 100:
                        avg_update_hz = sum(update_hz_list) / len(update_hz_list)
                        print(f"{header} LSD{loop_sleep_diff:.5f} LSF{avg_update_hz:.5f}", end='\r')
                        print_counter = 0
                
                loop_start_time = time.time()

            except KeyboardInterrupt as ki:
                print(f"[{process_name}]: interrupt received, stopping: {str(ki)}")
                stop_event.set()
            if stop_event.is_set():
                control_listener_thread.join()
                z_dc_pub.close()
                z_context.term()
                print(f'[{process_name}] exiting cleanly...')
                break
                
# Start ath9k hardware queue statistics monitoring DS in standalone operation mode
if __name__ == '__main__':
    cfg_d = {}
    cfg_d['process_name'] = 'DS_SimHWQStats'
    # Make sure the protocols match between publishers and subscribers
    cfg_d['pub_bind_addr'] = 'tcp://localhost:5651' # ath9k queue statistics DS (this) publisher socket address to bind
    cfg_d['ec_sub_addr'] = 'tcp://localhost:5550'      # Experiment Controller (EC) address to subscribe to for control messages
    cfg_d['nic_dev'] = 'wlp6s0'                        # Wi-Fi interface device name
    cfg_d['standalone'] = True                         # Run in standalone mode?
    cfg_d['max_data_freq'] = 400                       # Max update frequency cap to limit unnecessary resource usage
    cfg_d['window_size'] = 100                         # # Length of retained history for derivative statistics calculations

    hwq_stats(cfg_d)