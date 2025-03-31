from execute_utils import trim_dict
import time
import threading
import json
import numpy as np
from ds_utils import control_listener, append_and_resize_list, NoUpdateException
import zmq
from pyroute2.ethtool.ioctl import IoctlEthtool
from pyroute2.ethtool.ioctl import NotSupportedError

# Retrieve Ethernet WAN statistics using ioctl ethtool client from pyroute2 library then publish on ZMQ socket

def eth_stats(cfg_d): # Config dictionary
    # Init ZMQ DS publisher
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
    prev_ts = time.time_ns()
    prev_proc_time = time.process_time_ns()
    prev_rx_packets = 0.0
    prev_tx_packets = 0.0
    prev_rx_bytes = 0.0
    prev_tx_bytes = 0.0

    rx_packets_history = list()
    tx_packets_history = list()
    rx_bytes_history = list()
    tx_bytes_history = list()

    rx_packet_rate_history = list()
    tx_packet_rate_history = list()
    rx_byte_rate_history = list()
    tx_byte_rate_history = list()
    

    LIST_SIZE = cfg_d['window_size']
    eth_stats_dict = dict()

    # Set up max capped data source freq
    max_freq = cfg_d['max_data_freq'] # hz
    if max_freq != 0:
        max_loop_time = 1.0 / max_freq
    else:
        max_loop_time = 0.0
    slept_loop_time = 0.0
    loop_sleep_diff = max_loop_time

    # Set up loop freq measurement
    update_hz_list = list()
    n_running_avg = 5000
    print_counter = 0

    z_dc_pub.send_string('asdasdasdasd') # Establish connection to subscribers

    loop_start_time = time.time()
    loop_stop_time = time.time() + 1
    while not stop_event.is_set():
        try:
            # Get raw statistics from ethtool over ioctl
            stats = dev.get_statistics()
            ts = time.time_ns()

            for i in stats:
                stats_dict[i[0]] = i[1]

            # Parse raw statistics
            rx_packets = float(stats_dict['rx_packets'])
            tx_packets = float(stats_dict['tx_packets'])
            rx_bytes = float(stats_dict['rx_bytes'])
            tx_bytes = float(stats_dict['tx_bytes'])

            dt_rx_packets = rx_packets - prev_rx_packets
            dt_tx_packets = tx_packets - prev_tx_packets
            dt_rx_bytes = rx_bytes - prev_rx_bytes
            dt_tx_bytes = tx_bytes - prev_tx_bytes
            dt_ts = ts - prev_ts

            # If time elapsed since last overall time counter update from NIC is 0 us, disregard
            if dt_ts == 0:
                raise NoUpdateException()

            rx_packet_rate = (dt_rx_packets * 1e9) / dt_ts # 1e9 because dt_ts is in nanoseconds, convert to seconds
            tx_packet_rate = (dt_tx_packets * 1e9) / dt_ts
            rx_byte_rate = (dt_rx_bytes * 1e9) / dt_ts
            tx_byte_rate = (dt_tx_bytes * 1e9) / dt_ts

            append_and_resize_list(rx_packets_history, LIST_SIZE, rx_packets)
            append_and_resize_list(tx_packets_history, LIST_SIZE, tx_packets)
            append_and_resize_list(rx_bytes_history, LIST_SIZE, rx_bytes)
            append_and_resize_list(tx_bytes_history, LIST_SIZE, tx_bytes)

            append_and_resize_list(rx_packet_rate_history, LIST_SIZE, rx_packet_rate)
            append_and_resize_list(tx_packet_rate_history, LIST_SIZE, tx_packet_rate)
            append_and_resize_list(rx_byte_rate_history, LIST_SIZE, rx_byte_rate)
            append_and_resize_list(tx_byte_rate_history, LIST_SIZE, tx_byte_rate)

            prev_ts = ts
            prev_rx_packets = rx_packets
            prev_tx_packets = tx_packets
            prev_rx_bytes = rx_bytes
            prev_tx_bytes = tx_bytes

            # Calculate statistics
            avg_rx_packets = sum(rx_packets_history)/LIST_SIZE
            avg_tx_packets = sum(tx_packets_history)/LIST_SIZE
            avg_rx_bytes = sum(rx_bytes_history)/LIST_SIZE
            avg_tx_bytes = sum(tx_bytes_history)/LIST_SIZE
            avg_rx_packet_rate = sum(rx_packet_rate_history)/LIST_SIZE
            avg_tx_packet_rate = sum(tx_packet_rate_history)/LIST_SIZE
            avg_rx_byte_rate = sum(rx_byte_rate_history)/LIST_SIZE
            avg_tx_byte_rate = sum(tx_byte_rate_history)/LIST_SIZE
            
            std_rx_packets = np.std(rx_packets_history)
            std_tx_packets = np.std(tx_packets_history)
            std_rx_bytes = np.std(rx_bytes_history)
            std_tx_bytes = np.std(tx_bytes_history)
            std_rx_packet_rate = np.std(rx_packet_rate_history)
            std_tx_packet_rate = np.std(tx_packet_rate_history)
            std_rx_byte_rate = np.std(rx_byte_rate_history)
            std_tx_byte_rate = np.std(tx_byte_rate_history)

            min_rx_packets = min(rx_packets_history)
            min_tx_packets = min(tx_packets_history)
            min_rx_bytes = min(rx_bytes_history)
            min_tx_bytes = min(tx_bytes_history)
            min_rx_packet_rate = min(rx_packet_rate_history)
            min_tx_packet_rate = min(tx_packet_rate_history)
            min_rx_byte_rate = min(rx_byte_rate_history)
            min_tx_byte_rate = min(tx_byte_rate_history)

            max_rx_packets = max(rx_packets_history)
            max_tx_packets = max(tx_packets_history)
            max_rx_bytes = max(rx_bytes_history)
            max_tx_bytes = max(tx_bytes_history)
            max_rx_packet_rate = max(rx_packet_rate_history)
            max_tx_packet_rate = max(tx_packet_rate_history)
            max_rx_byte_rate = max(rx_byte_rate_history)
            max_tx_byte_rate = max(tx_byte_rate_history)

            proc_time = time.process_time_ns()
            dt_proc_time = proc_time - prev_proc_time
            prev_proc_time = proc_time

            # Add current info to dict for serializing and publishing to DS
            publish_dict = {
                f'{process_name}_ts': ts,
                f'{process_name}_pt': dt_proc_time,
                'rx_packets': rx_packets,
                'tx_packets': tx_packets, 
                'rx_bytes': rx_bytes, 
                'tx_bytes': tx_bytes, 
                'rx_packet_rate': rx_packet_rate, 
                'tx_packet_rate': tx_packet_rate, 
                'rx_byte_rate': rx_byte_rate, 
                'tx_byte_rate': tx_byte_rate,
                'avg_rx_packets': avg_rx_packets, 
                'avg_tx_packets': avg_tx_packets, 
                'avg_rx_bytes': avg_rx_bytes, 
                'avg_tx_bytes': avg_tx_bytes, 
                'avg_rx_packet_rate': avg_rx_packet_rate, 
                'avg_tx_packet_rate': avg_tx_packet_rate, 
                'avg_rx_byte_rate': avg_rx_byte_rate, 
                'avg_tx_byte_rate': avg_tx_byte_rate, 
                'std_rx_packets': std_rx_packets, 
                'std_tx_packets': std_tx_packets, 
                'std_rx_bytes': std_rx_bytes, 
                'std_tx_bytes': std_tx_bytes, 
                'std_rx_packet_rate': std_rx_packet_rate, 
                'std_tx_packet_rate': std_tx_packet_rate, 
                'std_rx_byte_rate': std_rx_byte_rate, 
                'std_tx_byte_rate': std_tx_byte_rate, 
                'min_rx_packets': min_rx_packets, 
                'min_tx_packets': min_tx_packets, 
                'min_rx_bytes': min_rx_bytes, 
                'min_tx_bytes': min_tx_bytes, 
                'min_rx_packet_rate': min_rx_packet_rate, 
                'min_tx_packet_rate': min_tx_packet_rate, 
                'min_rx_byte_rate': min_rx_byte_rate, 
                'min_tx_byte_rate': min_tx_byte_rate, 
                'max_rx_packets': max_rx_packets,
                'max_tx_packets': max_tx_packets,
                'max_rx_bytes': max_rx_bytes,
                'max_tx_bytes': max_tx_bytes,
                'max_rx_packet_rate': max_rx_packet_rate, 
                'max_tx_packet_rate': max_tx_packet_rate,
                'max_rx_byte_rate': max_rx_byte_rate,
                'max_tx_byte_rate': max_tx_byte_rate,
                'window_size': LIST_SIZE,
            }

            dict_string = json.dumps(publish_dict)
            header = f"{process_name};{ts};DATA;"
            pub_string = f"{header}{dict_string}"
            z_dc_pub.send_string(pub_string)

            # Store in history dict
            eth_stats_dict[ts] = publish_dict

            # History dict cleanup
            trim_dict(eth_stats_dict, process_name, 8192)

            loop_calc_time = time.time()

        except KeyboardInterrupt as ki:
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
                        print_string = f"RXP{dt_rx_packets} TXP{dt_tx_packets} RXR{rx_packet_rate:.2f} RXBR{rx_byte_rate:.2f} TXR{tx_packet_rate:.2f} TXBR{tx_byte_rate:.2f}"
                        print(f"{header} LSD{loop_sleep_diff:.5f} LSF{avg_update_hz:.5f} {print_string}", end='\r')
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
                
# Start Ethernet WAN statistics monitoring DS in standalone operation mode
if __name__ == '__main__':
    cfg_d = {}
    # Make sure the protocols match between publishers and subscribers
    cfg_d['dc_pub_bind_addr'] = 'tcp://localhost:5652' # Ethernet WAN statistics DS (this) publisher socket address to bind
    cfg_d['ec_sub_addr'] = 'tcp://localhost:5550'      # Experiment Controller (EC) address to subscribe to for control messages
    cfg_d['nic_dev'] = 'enp4s0'                        # Ethernet WAN interface device name
    cfg_d['standalone'] = True                         # Run in standalone mode?
    cfg_d['max_data_freq'] = 200                       # Max update frequency cap to limit unnecessary resource usage
    cfg_d['window_size'] = 100                         # Length of retained history for derivative statistics calculations

    eth_stats(cfg_d)