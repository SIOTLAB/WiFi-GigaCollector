import time
import threading
import json
import numpy as np
from ds_utils import control_listener, append_and_resize_list, NoUpdateException
import zmq
from pyroute2.ethtool.ioctl import IoctlEthtool
from pyroute2.ethtool.ioctl import NotSupportedError

# Retrieve Wi-Fi channel statistics using ath9k driver through ioctl ethtool client in pyroute2 library then publish on ZMQ socket

def chan_stats(cfg_d): # Config dictionary
    # Init zmq DS publisher
    process_name = cfg_d['process_name']
    z_context = zmq.Context()
    z_dc_pub = z_context.socket(zmq.PUB)
    z_dc_pub_bind_addr = cfg_d['pub_bind_addr']
    z_dc_pub.bind(z_dc_pub_bind_addr)
    print(f"[{process_name}] ZMQ DS pub socket bound: {z_dc_pub_bind_addr}")

    # Start EC listener thread
    stop_event = threading.Event()
    control_listener_thread = threading.Thread(target=control_listener, name=f"{process_name}_control_listener", args=(process_name, z_context, cfg_d['ec_sub_addr'], stop_event))
    control_listener_thread.start()

    # Init ethtool for Wi-Fi NIC and dict for storing retrieved stats
    dev = IoctlEthtool(cfg_d['nic_dev'])
    stats_dict = {}

    # Init other vars
    sanity_check_time = time.time()
    sanity_check_counter = 0

    prev_ch_time = 0.0
    prev_busy_time = 0.0
    prev_ch_busy_rx = 0.0
    prev_ch_busy_tx = 0.0
    prev_tx_retries = 0.0

    LIST_SIZE = cfg_d['window_size']
    time_elapsed_history = list()
    ch_util_history = list()
    ch_util_uplink_history = list()
    ch_util_downlink_history = list()
    zero_diff_history = list()
    chan_stats_dict = dict()
    
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

    # Set up loop freq measurement
    update_hz_list = list()
    n_running_avg = 5000
    print_counter = 0

    z_dc_pub.send_string('asdasdasdasd') # Establish connection to subscribers
    
    loop_start_time = time.time()
    loop_stop_time = time.time() + 1
    while not stop_event.is_set():
        try:
            # Get statistics from ethtool over ioctl
            stats = dev.get_statistics()
            ts = time.time_ns() # int(time.time() * 1000)

            for i in stats:
                stats_dict[i[0]] = i[1]
            
            ch_time = float(stats_dict['ch_time'])
            busy_time = float(stats_dict['ch_time_busy'])
            ch_busy_rx = float(stats_dict['ch_time_rx'])
            ch_busy_tx = float(stats_dict['ch_time_tx'])
            tx_retries = float(stats_dict['tx_retries'])

            # If time elapsed since last overall time counter update from NIC is 0 us, disregard
            dt_ch_time = ch_time-prev_ch_time
            if dt_ch_time == 0:
                raise NoUpdateException()

            # Get channel utilization and breakdown of ul/dl
            ch_util_diff = busy_time-prev_busy_time
            ch_util = (ch_util_diff) / dt_ch_time
            ch_util_uplink = (ch_busy_rx-prev_ch_busy_rx) / dt_ch_time
            ch_util_downlink = (ch_busy_tx-prev_ch_busy_tx) / dt_ch_time
            tx_retries = (tx_retries-prev_tx_retries)

            prev_ch_time = ch_time
            prev_busy_time=busy_time
            prev_ch_busy_rx = ch_busy_rx
            prev_ch_busy_tx = ch_busy_tx
            prev_tx_retries = tx_retries

            ch_util_history.append(ch_util)
            if len(ch_util_history) > LIST_SIZE:
                del ch_util_history[0]

            zero_diff_history.append(ch_util_diff)
            if len(zero_diff_history) > LIST_SIZE:
                del zero_diff_history[0]

            ch_util_uplink_history.append(ch_util_uplink)
            if len(ch_util_uplink_history) > LIST_SIZE:
                del ch_util_uplink_history[0]
            
            ch_util_downlink_history.append(ch_util_downlink)
            if len(ch_util_downlink_history) > LIST_SIZE:
                del ch_util_downlink_history[0]
            
            
            # Calculate statistics for total, uplink, and downlink channel utils
            avg_ch_util = sum(ch_util_history)/LIST_SIZE
            avg_ul_util = sum(ch_util_uplink_history)/LIST_SIZE
            avg_dl_util = sum(ch_util_downlink_history)/LIST_SIZE
            
            std_ch_util = np.std(ch_util_history)
            std_ul_util = np.std(ch_util_uplink_history)
            std_dl_util = np.std(ch_util_downlink_history)

            min_ch_util = min(ch_util_history)
            min_ul_util = min(ch_util_uplink_history)
            min_dl_util = min(ch_util_downlink_history)

            max_ch_util = max(ch_util_history)
            max_ul_util = max(ch_util_uplink_history)
            max_dl_util = max(ch_util_downlink_history)


            
            # Get process time for (optional) DS performance analysis
            proc_time = time.process_time_ns()
            dt_proc_time = proc_time - prev_proc_time
            prev_proc_time = proc_time

            # Add current info to dict for serializing and publishing to DS
            publish_dict = {
                f'{process_name}_ts': ts,
                f'{process_name}_pt': dt_proc_time,
                'ch_util': ch_util,
                'ch_util_uplink': ch_util_uplink,
                'ch_util_downlink': ch_util_downlink, 
                'tx_retries': tx_retries, 
                'window_size': LIST_SIZE,
                'avg_ch_util': avg_ch_util,
                'avg_ul_util': avg_ul_util, 
                'avg_dl_util': avg_dl_util,
                'std_ch_util': std_ch_util, 
                'std_ul_util': std_ul_util,
                'std_dl_util': std_dl_util, 
                'min_ch_util': min_ch_util, 
                'min_ul_util': min_ul_util, 
                'min_dl_util': min_dl_util, 
                'max_ch_util': max_ch_util,
                'max_ul_util': max_ul_util, 
                'max_dl_util': max_dl_util, 
            }

            header = f"{process_name};ts;DATA;"
            csv_pub_string = header + json.dumps(publish_dict)
            z_dc_pub.send_string(csv_pub_string)

            zero_count = len(list(filter(lambda x: x < 0.05, zero_diff_history)))
            avg_diff = sum(zero_diff_history)/LIST_SIZE
            print_list = [avg_ch_util, avg_ul_util, avg_dl_util, zero_count, ch_util_diff, dt_ch_time]

            loop_calc_time = time.time()


        except KeyboardInterrupt as ki:
            print(f"[{process_name}]: interrupt received, stopping: {str(ki)}")
            stop_event.set()
        except NoUpdateException:
            loop_calc_time = time.time()
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

                        str_builder2 = ''
                        for i in print_list:
                            str_builder2 += f"{i:.2f}, "
                        str_builder2 += f"LSF{avg_update_hz:.2f} LSD{loop_sleep_diff:.8f} LT{loop_time:.8f} LD{len(chan_stats_dict)}"
                        
                        print(f"[{str_builder2}] ", end='')

                        str_builder = '' # Define final string here
                        print(str_builder, end = '\r')
                
                loop_start_time = time.time()
                
            except KeyboardInterrupt as ki:
                print(f"[{process_name}]: interrupt received, stopping: {str(ki)}")
                stop_event.set()
            if stop_event.is_set():
                control_listener_thread.join()
                z_dc_pub.close()
                z_context.term()
                break

# Start Wi-Fi channel statistics monitoring DS in standalone operation mode
if __name__ == '__main__':
    cfg_d = {}
    # Make sure the protocols match between publishers and subscribers
    cfg_d['dc_pub_bind_addr'] = 'ipc:///tmp/gigacol/5650' # Wi-Fi channel statistics DS (this) publisher socket address to bind
    cfg_d['ec_sub_addr'] = 'ipc:///tmp/gigacol/5550'      # Experiment Controller (EC) address to subscribe to for control messages
    cfg_d['nic_dev'] = 'wlp6s0'                           # Wi-Fi interface device name
    cfg_d['standalone'] = True                            # Run in standalone mode?
    cfg_d['max_data_freq'] = 100                          # Max update frequency cap to allow for more granularity in channel utilization readings
    cfg_d['window_size'] = 3 * cfg_d['max_data_freq']     # Length of retained history for average channel utilization value calculations

    chan_stats(cfg_d)