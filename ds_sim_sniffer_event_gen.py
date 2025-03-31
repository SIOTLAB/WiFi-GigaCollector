from re import S
import pyshark
from multiprocessing import Manager, Process, Lock, Queue, Event
import os
from bisect import bisect_left
import time
import queue
import threading
from datetime import datetime
from ds_utils import AutotrimDict
import zmq
import json
import random

# Randomly simulate packet sniffing events, sends COLLATE_WITH requests to Collator along with packet information to append to the output snapshot

def sim_sniffer(cfg_d):
    # process packet and extract info of interest (mac address, AC, RSSI, etc...)

    def listener_thread():
        z_ec_sub = z_context.socket(zmq.SUB)
        z_ec_sub.setsockopt(zmq.RCVTIMEO, 100)
        z_ec_sub_addr = cfg_d['ec_sub_addr']
        z_ec_sub.connect(z_ec_sub_addr)
        print(f"[{process_name}] zmq EC sub socket bound: {z_ec_sub_addr}")
        z_ec_sub.subscribe('')

        while not stop_event.is_set():
            try:
                message = z_ec_sub.recv_string()
                sniffer_recv_time = time.time_ns() # timestamp when sniffer detected the packet

                if '!!' == message[0:2]:
                    pass # ignore !!!synchronizer!!!s
                else:
                    message_split = message.strip().split(';')
                    if message_split[0] == 'ExptController':
                        if message_split[2] == 'QUIT':
                            stop_event.set()
                        if message_split[2] == 'EXPT_DEF':
                            new_expt_d = json.loads(message_split[3])
                            with expt_d_lock:
                                for k, v in new_expt_d.items():
                                    expt_d[k] = v
                        if message_split[2] == 'EXPT_START':
                            expt_start_event.set()
                            # print("SET EXPT_START_EVENT")
                        if message_split[2] == 'EXPT_STOP':
                            expt_stop_event.set()
                            # print("SET EXPT_STOP_EVENT")
                    elif message_split[0] == 'host_apd' and message_split[2] == 'HOSTAPD_STA_INFO' and 'pairwise key handshake completed' in message_split[4]:
                        # with station_macs_list_lock:
                        station_macs_list.append(message_split[3])
                    elif message_split[0] == 'collator' and message_split[2] == 'RESULT':
                        write_queue.put(message_split[3])
                    elif message_split[0] == 'user_sixteen' and message_split[2] == 'DATA':
                        new_u16_data = json.loads(message_split[3])
                        with sixteen_dict_lock:
                            for k, v in new_u16_data:
                                sixteen_dict.append(k, v)

            except zmq.Again:
                pass

        z_ec_sub.close()
        print(f'[{process_name}] (control_listener) exiting cleanly...')
    
    # init zmq publisher to ec
    process_name = cfg_d['process_name']
    z_context = zmq.Context()
    z_pub = z_context.socket(zmq.PUB)
    z_pub_bind_addr = cfg_d['pub_bind_addr']
    z_pub.bind(z_pub_bind_addr)
    print(f"[{process_name}] zmq DS pub socket bound: {z_pub_bind_addr}")
    
    # init mt shared resources
    expt_d = dict()
    expt_d_lock = threading.Lock()
    expt_start_event = threading.Event()
    expt_stop_event = threading.Event()
    write_queue = queue.Queue()
    sixteen_dict = AutotrimDict(16384)
    sixteen_dict_lock = threading.Lock()
    stop_event = threading.Event()
    
    # init listener thread
    control_listener_thread = threading.Thread(target=listener_thread, name=f"{process_name}_listener", args=())
    control_listener_thread.start()
    print(f"[{cfg_d['process_name']}] Sim-PyShark initialized, ready to start sniffing", flush=True)
    
    time.sleep(1)
    # init counters
    packet_count = 0
    good_packet_count = 0
    good_packet_count_at_start = 0
    sixteen_ignored = 0

    PACKETS_TO_CAPTURE = 10000
    PRINT_INDENT = ' ' * (0)

    event_gen_running = False

    while not stop_event.is_set():
        try:
            if expt_start_event.is_set():
                expt_start_event.clear()
                event_gen_running = True
                print(f"{PRINT_INDENT}[{process_name}] ####### STARTED NEW EXPERIMENT")
            if expt_stop_event.is_set():
                expt_stop_event.clear()
                event_gen_running = False
                header = f"{process_name};{time.time_ns()};"
                z_pub.send_string(f"{header}CAPTURED_ENOUGH")
                print(f"{PRINT_INDENT}[{process_name}] ####### CAPTURED ENOUGH PACKETS")
            if event_gen_running:
                hexchars = '0123456789abcdef'
                # run it down
                collate_req_dict = {
                    'dst_mac': 'aa:bb:cc:dd:ee:ff',
                    'ac_priority': random.randint(0, 7), # AC goes from 0-7?
                    'src_ip': '1.2.3.4',
                    'dst_ip': '5.6.7.8',
                    'ip_checksum': ''.join(random.choice(hexchars) for _ in range(4)),
                    'udp_checksum': ''.join(random.choice(hexchars) for _ in range(4)),
                    'transmission_duration': random.randint(10, 1000),
                    'sniffed_dbm': random.randint(-80, -10),
                    'sniffed_msg_size': random.randint(100, 1400),
                    'frame_ts': time.time_ns() - 2e9, # should be a different format from tshark
                    'frame_ts_unix': time.time_ns() - 2e9, # subtract 2 seconds for demo purposes 
                    'el_atency': random.randint(10000, 100000)
                }

                time.sleep(random.uniform(0.1, 0.5)) # pretend packets are coming in sporadically

                # copy expt_d into collate_req_dict for be/bk/vi/vo/udb/msg_size/t_ch_util/n_stations info from AEC
                with expt_d_lock:
                    for k, v in expt_d.items():
                        collate_req_dict[k] = v
                
                # get collated
                collated_req_json = json.dumps(collate_req_dict)
                header = f"{process_name};{int(time.time_ns())};COLLATE_WITH;{int(time.time_ns() - 1e9)};{collated_req_json}"
                
                z_pub.send_string(header)
                # COLLATE_WITH: requester_name, request_pub_ts, COLLATE_WITH, requested_ts, data_to_append

        except KeyboardInterrupt as ki:
            stop_event.set()

        except queue.Empty as qe:
            pass

    # close all things
    control_listener_thread.join()
    print("[{process_name}] Joined listener_thread")
    z_pub.close()
    z_context.term()
    print(f"[{process_name}] Closed z_pub and terminated z_context")
    