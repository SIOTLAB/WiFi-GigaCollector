import zmq
import json
import time
from datetime import datetime
import pandas as pd
import multiprocessing
import sys
import traceback

# Import all DSs, CC, SC, AEC server to subprocess later
from ds_sim_chan_stats import sim_chan_stats
from ds_sim_hwq_stats import sim_hwq_stats
from ds_sim_eth_stats import sim_eth_stats
from ds_load_avg_stats import load_avg_stats
from ds_sim_sniffer_event_gen import sim_sniffer

from cc_collector_collator import collector

from sc_file_writer import csv_writer

# Define Experiment Definitions (EDs) to loop through
list_of_expt_defs = [
    {
        'bk': False,        # Send BacKground (BK) access category packets
        'be': False,        # Send Best Effort (BE) access category packets
        'vi': False,        # Send VIdeo (VI) access category packets
        'vo': False,        # Send VOice (VO) access category packets
        'udb': 'U',         # 'U' for Uplink traffic only, 'D' for Downlink only, 'B' for Both
        't_ch_util': 0.7,   # Desired total channel utilization from all traffic on network
        'packet_size': 100, # Packet sizes to generate
        'ud_ratio': 0.0,    # Uplink/downlink ratio: 0.0 for all downlink, 1.0 for all uplink, 0.5 for even mix of both
        'lehist_filename': f"./data/aecs_log1_{datetime.now().strftime('%Y-%m-%d %H%M%S')}.lehist", # AEC server debug log filename
        'update': True      # Take effect immediately
    }, 
    {
        'bk': True,
        'be': True,
        'vi': True,
        'vo': True,
        'udb': 'U',
        't_ch_util': 0.1,
        'packet_size': 1400,
        'ud_ratio': 0.5,
        'lehist_filename': f"./data/aecs_log2_{datetime.now().strftime('%Y-%m-%d %H%M%S')}.lehist",
        'update': True
    }
]

# Global config for all subsystems
gc_config = {
    # Define the EC instance
    'ec': {
        'process_name': 'ExptController',                      # User-defined name of this Experiment Controller
        'z_ec_pub_bind_addr': 'tcp://localhost:5550',          # ZMQ socket address to publish system control messages to
    },
    # Define DSs
    'dss': {
        'DS_SimChanStats': {                                   # User-defined name for this Data Source (DS)
            'target': sim_chan_stats,                          # Function imported at the top of the file to subprocess for this DS
            'process': None,                                   # Process object used by the EC to track clean exits with .join() later
            'process_name': 'DS_SimChanStats',                 # User-defined name for this DS used by the DS itself
            'pub_bind_addr': 'ipc:///tmp/gigacol/5650',        # ZMQ socket address to publish data points
            'nic_dev': 'wlp6s0',                               # Wi-Fi network interface device name
            'standalone': False,                               # Run in standalone mode and print debug messages?
            'max_data_freq': 100,                              # How many updates per second
            'window_size': 100,                                # How many data points to keep in memory for averaging
            'ec_sub_addr': 'tcp://localhost:5550',             # ZMQ socket to subscribe to for control messages
        },
        'DS_SimHWQStats': {                                    # The next few DSs are essentially the same
            'target': sim_hwq_stats,
            'process': None,
            'process_name': 'DS_SimHWQStats',
            'pub_bind_addr': 'tcp://localhost:5651',
            'nic_dev': 'wlp6s0',
            'standalone': False,
            'max_data_freq': 400,
            'window_size': 400,
            'ec_sub_addr': 'tcp://localhost:5550',
        },
        'DS_SimEthStats': {
            'target': sim_eth_stats,
            'process': None,
            'process_name': 'DS_SimEthStats',
            'pub_bind_addr': 'tcp://localhost:5652',
            'nic_dev': 'enp4s0',
            'standalone': False,
            'max_data_freq': 200,
            'window_size': 200,
            'ec_sub_addr': 'tcp://localhost:5550',
        },
        'DS_LoadAvgs': {
            'target': load_avg_stats,
            'process': None,
            'process_name': 'DS_LoadAvgs',
            'pub_bind_addr': 'tcp://localhost:5653',
            'standalone': False,
            'max_data_freq': 100,
            'window_size': 100,
            'ec_sub_addr': 'tcp://localhost:5550',
        },
        'DS_SimSniffer': {                                     # The simulated sniffer has new options
            'target': sim_sniffer,
            'process': None,
            'process_name': 'DS_SimSniffer',
            'pub_bind_addr': 'tcp://localhost:5554',
            'nic_dev': 'wlp7s0',                               # Wi-Fi network interface for sniffing packets
            'target_mac': 'aa:bb:cc:dd:ee:ff',                 # MAC address of target device to filter traffic
            'ap_mac': '00:11:22:33:44:55',                     # MAC address of AP network interface
            'encryption_type': 'wpa-pwd',                      # Wi-Fi network encryption type
            'decryption_key': 'putapasswordhere!:gc_network',  # Wi-Fi password:network name
            'ec_sub_addr': 'tcp://localhost:5550',             
        }
    },
    # Define CCs
    'ccs': {
        'CC0': {
            'target': collector,
            'process': None,
            'process_name': 'CC0',
            # Attached_DSs are DSs that this CC instance should subscribe to for data collection
            'attached_dss': ['DS_SimChanStats', 'DS_SimEthStats', 'DS_LoadAvgs', 'DS_SimHWQStats'], 
            # event_sources are DSs or other applications that trigger COLLATE or COLLATE_WITH calls.
            # DS_SimSniffer goes here because it doesn't generate any data for the CC to save, but tells the CC
            # to create snapshots and append some data to it at collation time using COLLATE_WITH.
            'event_sources': ['DS_SimSniffer'],
            'max_data_history_size': 16384,
            'collator_pub_bind_addr': 'tcp://localhost:6551',
            'cc_write_debug_log': True,                        # Write a debug log?
            'cc_debug_log_path': f"./sim_data/CC0_{datetime.now().strftime('%Y-%m-%d-%H.%M.%S')}.csv", # File path for the debug log
            'ec_sub_addr': 'tcp://localhost:5550',
        }
    },
    # Define SCs
    'scs': {
        'SC_CSVWriter': {
            'target': csv_writer,
            'process': None,
            'process_name': 'SC_CSVWriter',
            'out_fname': f"./sim_data/collated_data_{datetime.now().strftime('%Y-%m-%d-%H.%M.%S')}.csv",
            # This list can be used to set a filter to use snapshots from certain event generators only,
            # but needs to be implemented in the Snapshot Consumer to take effect.
            'requesters': ['DS_SimSniffer'],                   
            'ccs': ['CC0'],                                    # The CC instances to subscribe to for snapshots. Can be multiple.
            'ec_sub_addr': 'tcp://localhost:5550',
        }
    },
    # Define AEC server
    # 'aec_s': {                                                        # 
    #     'target': aec_server,                                         # Function to call
    #     'process': None,                                              # Process object for AEC to manage clean exits with .join() (doesn't work with AEC for now)
    #     'process_name': 'AECServer',                                  # Define process name
    #     'ec_sub_addr': 'tcp://localhost:5550',                        # ZMQ subscriber address for EC control messages
    #     'chan_stats_sub_addr': 'ipc:///tmp/gigacol/5650',             # ZMQ subscriber address for Wi-Fi channel statistics (ds_chan_stats.py)
    #     'aec_s_write_debug_log': True,                                # Write a debug log for the AEC server?
    #     'aec_s_debug_log_path': f"./sim_data/AECS_{datetime.now().strftime('%Y-%m-%d-%H.%M.%S')}.csv", # Filename for debug log
    #     'dc_pub_bind_addr': 'tcp://localhost:5600',                   # ZMQ publisher address for overall stats (unused)
    #     'aec_c_control_pub_bind_addr': 'tcp://172.16.0.1:9001',       # ZMQ publisher address for publishing control messages to clients
    #     'aec_sp_control_pub_bind_addr': 'ipc:///tmp/gigacol/5601',    # ZMQ publisher address for server-side internal control messages
    #     'aec_s_recvr_stats_pub_bind_addr': 'ipc:///tmp/gigacol/5602', # ZMQ publisher address for receiver statistics (unused now)
    #     'bt_ports': {                            # Ports to send background traffic on
    #         'bk': 9002,                          # BacKground (BK)
    #         'be': 9003,                          # Best Effort (BE)
    #         'vi': 9004,                          # VIdeo (VI)
    #         'vo': 9005,                          # VOice (VO)
    #     },
    #     'btc_sub_base_addrs': {                  # Addresses of client stations to transfer traffic with
    #         # 'btc_s1': 'tcp://192.168.0.11',    # name:address pair
    #         # 'btc_s2': 'tcp://192.168.0.12',    
    #         # 'btc_s3': 'tcp://192.168.0.13',
    #         'btc_s5': 'tcp://192.168.0.15',
    #     },
    #     'bts_pub_base_bind_addr': 'tcp://192.168.0.4', # Address on the server/AP for the AEC server to bind to
    #     'max_data_freq': 5000,                         # Frequency at which the PID controllers will run
    # }
}

# Initialize this EC
process_name = gc_config['ec']['process_name']
z_context = zmq.Context()
z_ec_pub = z_context.socket(zmq.PUB)
z_ec_pub_bind_addr = gc_config['ec']['z_ec_pub_bind_addr']
z_ec_pub.bind(z_ec_pub_bind_addr)
print(f"[{process_name}] ZMQ EC pub socket bound: {z_ec_pub_bind_addr}")

# Note 1: When used in full AP setup (not this demo setup), would also listen for hostapd ACS and STA (dis)assoc/auth messages
# Note 2: Or, as this implementation is meant to more clearly demonstrate subsystems, full AP data capture setup
# ------- could integrate EC + sniffer + SC instead.

try:
    # Start subprocesses for DSs
    for ds_process_name, params in gc_config['dss'].items():
        # Can switch to multithreading mode by commenting the "multiprocessing" line and uncommenting the "threading" line for testing
        process_obj = multiprocessing.Process(target=params['target'], name=ds_process_name, args=(params.copy(),))
        # process_obj = threading.Thread(target=params['target'], name=process_name, args=(arg for arg in params['args'].values()))
        gc_config['dss'][ds_process_name]['process'] = process_obj
        process_obj.start()
    # Start subprocesses for CCs
    for cc_process_name, params in gc_config['ccs'].items():
        old_a_dss = params['attached_dss']
        new_a_dss = {k: gc_config['dss'][k]['pub_bind_addr'] for k in old_a_dss}
        
        old_e_s = params['event_sources']
        new_e_s = {k: gc_config['dss'][k]['pub_bind_addr'] for k in old_e_s}

        params['attached_dss'] = new_a_dss
        params['event_sources'] = new_e_s

        process_obj = multiprocessing.Process(target=params['target'], name=cc_process_name, args=(params.copy(),))
        gc_config['ccs'][cc_process_name]['process'] = process_obj
        process_obj.start()

    # Start subprocess for AEC Server 
    # Note: AEC server current does not exit cleanly, so it is not included here.
    # ----- It is recommended to run the AEC server in a separate terminal window first, then run this EC after.


    # EC experiment swapping loop
    for expt_d in list_of_expt_defs:
        # Start subprocesses for SCs. Inside the experiment loop because the SC_File_Writer implementation
        # is intended to close and restart for every experiment.
        for sc_process_name, params in gc_config['scs'].items():
            old_ccs = params['ccs']
            new_ccs = {k: gc_config['ccs'][k]['collator_pub_bind_addr'] for k in old_ccs}

            params['ccs'] = new_ccs

            # Update the output filename to most closely match the start of this next experiment
            params['out_fname'] = f"./sim_data/collated_data_{datetime.now().strftime('%Y-%m-%d-%H.%M.%S')}.csv"

            process_obj = multiprocessing.Process(target=params['target'], name=sc_process_name, args=(params.copy(),))
            gc_config['scs'][sc_process_name]['process'] = process_obj
            process_obj.start()
        
        # Send new Experiment Definition to AEC server and SimSniffer event generator

        time.sleep(2)
        z_ec_pub.send_string('!!!synchronizer!!!')
        print(f"[{process_name}] Sent synchronizer message...")
        header = f'{process_name};{time.time_ns()};'
        z_ec_pub.send_string(header + 'EXPT_DEF;' + json.dumps(list_of_expt_defs[0]))
        print(f"[{process_name}] Sent EXPT_DEF: {expt_d}")
        time.sleep(1)
        
        # Start AEC traffic generation
        z_ec_pub.send_string(header + 'BTS_START_TRAFFIC')
        print(f"[{process_name}] Sent BTS_START_TRAFFIC")

        # Wait a bit for AEC to spin up
        time.sleep(3)

        # Send EXPT_START signal so event gen starts
        z_ec_pub.send_string(header + 'EXPT_START')
        print(f"[{process_name}] Sent EXPT_START")

        # Send EXPT_START_RECORD so file writer starts taking CC output
        z_ec_pub.send_string(header + 'EXPT_START_RECORD_CSV')
        print(f"[{process_name}] Sent EXPT_START_RECORD_CSV")

        # Run experiment for 5 seconds
        # Note: better metrics for stopping like number of captured packets may be used instead;
        #       this is kept simple for demonstration purposes
        time.sleep(5)

        # Send EXPT_STOP signal so event gen stops
        z_ec_pub.send_string(header + 'EXPT_STOP')
        print(f"[{process_name}] Sent EXPT_STOP")

        # Stop CSV writer so it'll write to file with EXPT_STOP_RECORD
        z_ec_pub.send_string(header + 'EXPT_STOP_RECORD_CSV')
        print(f"[{process_name}] Sent EXPT_STOP_RECORD_CSV")

        # Stop AEC traffic by sending message
        z_ec_pub.send_string(header + 'BTS_STOP_TRAFFIC')
        print(f"[{process_name}] Sent BTS_STOP_TRAFFIC")
        time.sleep(1)
    
    # Stop all systems after completion of experiments
    z_ec_pub.send_string(header + 'QUIT')

    # Wait for all child processes to join
    for ds_process_name, params in gc_config['dss'].items():
        params['process'].join()
        print(f"[{process_name}] <- {ds_process_name} joined!")
    for cc_process_name, params in gc_config['ccs'].items():
        params['process'].join()
        print(f"[{process_name}] <- {cc_process_name} joined!")
    for sc_process_name, params in gc_config['scs'].items():
        params['process'].join()
        print(f"[{process_name}] <- {sc_process_name} joined!")

except Exception as e:
    traceback.print_exc()
    z_ec_pub.close()
    z_context.term()

finally:
    z_ec_pub.close()
    z_context.term()