import os
import time
import threading
import json
import numpy as np
from ds_utils import control_listener, append_and_resize_list, NoUpdateException
import zmq

# Retrieve CPU load averages over 1-minute, 5-minute, and 15-minute periods then publish on ZMQ socket

def load_avg_stats(cfg_d): # Config dictionary
    # Init ZMQ DS publisher
    process_name = cfg_d['process_name']
    z_context = zmq.Context()
    z_dc_pub = z_context.socket(zmq.PUB)
    z_dc_pub_bind_addr = cfg_d['pub_bind_addr']
    z_dc_pub.bind(z_dc_pub_bind_addr)
    print(f"[{process_name}] zmq dc pub socket bound: {z_dc_pub_bind_addr}")

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
    slept_loop_time = 0.0
    loop_sleep_diff = max_loop_time

    # Set up process time measurement
    prev_proc_time = time.process_time_ns()

    # set up loop freq measurement
    update_hz_list = list()
    n_running_avg = 5000
    print_counter = 0

    z_dc_pub.send_string('asdasdasdasd') # Establish connection to subscribers
    
    loop_start_time = time.time()
    loop_stop_time = time.time() + 1
    while not stop_event.is_set():
        try:
            # Get CPU load averages from Python os module
            loadavg_1m, loadavg_5m, loadavg_15m = os.getloadavg()
            ts = time.time_ns()

            
            csv_pub_string = f"{process_name};{ts};DATA;"
            
            proc_time = time.process_time_ns()
            dt_proc_time = proc_time - prev_proc_time
            prev_proc_time = proc_time

            # Add current info to dict for serializing and publishing to DS
            publish_dict = {
                f'{process_name}_ts': ts,
                f'{process_name}_pt': dt_proc_time,
                'loadavg_1m': loadavg_1m,
                'loadavg_5m': loadavg_5m,
                'loadavg_15m': loadavg_15m,
            }
            csv_pub_string += json.dumps(publish_dict)
            z_dc_pub.send_string(csv_pub_string)

            loop_calc_time = time.time()


        except KeyboardInterrupt as ki:
            print(f"[{process_name}]: interrupt received, stopping: {str(ki)}")
            stop_event.set()
        except NoUpdateException:
            pass
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

                        str_builder2 = f"LSF{avg_update_hz:.2f} LSD{loop_sleep_diff:.8f} LT{loop_time:.8f} 1m{loadavg_1m}, 5m{loadavg_5m}, 15m{loadavg_15m}"
                        
                        print(f"[{str_builder2}] ", end='')

                        str_builder = '' # customize string here
                        print(str_builder, end = '\r')
                        print_counter = 0
                
                loop_start_time = time.time()
                
            except KeyboardInterrupt as ki:
                print(f"[{process_name}]: interrupt received, stopping: {str(ki)}")
                stop_event.set()
            if stop_event.is_set():
                control_listener_thread.join()
                z_dc_pub.close()
                z_context.term()
                break

# Start CPU load averages statistics monitoring DS in standalone operation mode
if __name__ == '__main__':
    cfg_d = {}
    # Make sure the protocols match between publishers and subscribers
    cfg_d['dc_pub_bind_addr'] = 'tcp://localhost:5653' # CPU load averages statistics DS (this) publisher socket address to bind
    cfg_d['ec_sub_addr'] = 'tcp://localhost:5550'      # Experiment Controller (EC) address to subscribe to for control messages
    cfg_d['standalone'] = True                         # Run in standalone mode?
    cfg_d['max_data_freq'] = 100                       # Max update frequency cap to limit unnecessary resource usage
    cfg_d['window_size'] = 100                         # Length of retained history for derivative statistics calculations

    load_avg_stats(cfg_d)