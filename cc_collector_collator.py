from execute_utils import execute, trim_dict, sanity_check_dict
import time, re
from subprocess import CalledProcessError
import subprocess
import multiprocessing
import threading
import signal
import zmq
import sys
import pandas as pd
import os
import queue
from bisect import bisect_left
import json
from datetime import datetime
from ds_utils import TIML, get_add_col_str, get_add_col_str_nocomma

# Collector/Collator subsystem. The Collector subscribes to DSs listed in the configuration
# dictionary dc_cfg_d, which is the dictionary defined in the EC for the CC processes.
# The Collector is also subscribed to the EC and any event generators. The EC sends system-wide
# control messages such as QUIT for a clean exit by all subsystems. The event generators
# tell the Collator when to collate data into a temporally-coherent snapshot and publish it.

def collector(dc_cfg_d):
    def collator(dc_cfg_d, z_context, pub_lock_dict, pub_dicts, stop_event):
        z_collator_pub = z_context.socket(zmq.PUB)
        z_collator_pub_bind_addr = dc_cfg_d['collator_pub_bind_addr']
        z_collator_pub.bind(z_collator_pub_bind_addr)
        print(f"[{process_name}] ZMQ collator pub socket bound: {z_collator_pub_bind_addr}")
        z_collator_pub.send_string('!!!synchronizer!!!')

        while not stop_event.is_set():
            try:
                requester_name, requested_ts, with_data = collate_queue.get(timeout=0.1) # probably (ts, requester) tuple to put in header of output
                requested_ts = int(requested_ts)
                collate_start_ts = time.time_ns() 

                publish_list = []
                
                publish_str = f"{requester_name};{process_name};{requested_ts};RESULT;{'{'}"
                if with_data[0] == '{': # probably a json dict string
                    publish_str += with_data[1:-1] + ',' # remove the { } and append

                
                for ds in pub_lock_dict.keys():
                    with pub_lock_dict[ds]:
                        ds_data = pub_dicts[ds].find_by_ts(requested_ts)
                        publish_list.append(ds_data)
                        # publish_str += ds_data + ','
                # publish_str = publish_str[:-1]

                # publish the dict
                collate_stop_ts = time.time_ns()
                publish_list += [get_add_col_str_nocomma(f'collate_start_ts_{process_name}', collate_start_ts), get_add_col_str_nocomma(f'collate_req_ts_{process_name}', requested_ts), get_add_col_str_nocomma(f'collate_stop_ts_{process_name}', collate_stop_ts) + '}']
                # publish_str += (get_add_col_str('collate_start_ts', collate_start_ts) + get_add_col_str('collate_req_ts', requested_ts) + get_add_col_str('collate_stop_ts', collate_stop_ts) + '}')

                publish_str += ','.join(publish_list)
                z_collator_pub.send_string(publish_str)

                collate_queue.task_done()

            except queue.Empty:
                pass
        
        z_collator_pub.close()
        print(f"[{'collator'}] exiting cleanly...")


    process_name = dc_cfg_d['process_name']

    z_context = zmq.Context()
    z_sub = z_context.socket(zmq.SUB)
    z_sub.setsockopt(zmq.RCVTIMEO, 100)

    collate_queue = queue.Queue()
    
    pub_count_dict = {}
    pub_rate_hist_dict = {}
    pub_avg_rate_dict = {}
    pub_std_rate_dict = {}

    pub_dicts = {}
    pub_lock_dict = {}
    # Subscribe to DSs and create data structures for them
    for ds, sub_addr in dc_cfg_d['attached_dss'].items():
        z_sub.connect(sub_addr)
        pub_count_dict[ds] = 0
        pub_rate_hist_dict[ds] = []
        pub_avg_rate_dict[ds] = 0
        pub_std_rate_dict[ds] = 0
            
        if ds != 'ec':
            pub_lock_dict[ds] = threading.Lock()
            pub_dicts[ds] = TIML(ds, dc_cfg_d['max_data_history_size'])
        print(f"[{process_name}] ZMQ sub socket subscribed: {ds} = {sub_addr}")
    
    # Subscribe to event sources/EC and DON'T create data structures for them
    for ds, sub_addr in dc_cfg_d['event_sources'].items():
        z_sub.connect(sub_addr)
        pub_count_dict[ds] = 0
        pub_rate_hist_dict[ds] = []
        pub_avg_rate_dict[ds] = 0
        pub_std_rate_dict[ds] = 0
            
        print(f"[{process_name}] ZMQ sub socket subscribed: {ds} = {sub_addr}")
    
    pub_count_dict['ec'] = 0
    pub_rate_hist_dict['ec'] = []
    pub_avg_rate_dict['ec'] = 0
    pub_std_rate_dict['ec'] = 0
    z_sub.connect(dc_cfg_d['ec_sub_addr'])
    print(f"[{process_name}] ZMQ sub socket subscribed: EC = {dc_cfg_d['ec_sub_addr']}")
    z_sub.subscribe('')

    stop_event = threading.Event()

    # start collator thread
    collator_thread = threading.Thread(target=collator, name=f"{process_name}_collator_thread", args=(dc_cfg_d, z_context, pub_lock_dict, pub_dicts, stop_event,))
    collator_thread.start()

    last_total_count = 0
    count_hz_list = list()
    count_ts_list = list()
    cc_proc_time = []

    # set up loop freq measurement
    update_hz_list = list()
    n_running_avg = 10
    print_counter = 0
    
    loop_start_time = time.time()
    loop_stop_time = time.time() + 1
    sec_start_time = time.time_ns()

    collate_queue_size = 0

    cc_proc_time.append(time.process_time_ns())
    while not stop_event.is_set():
        try:
            message = z_sub.recv_string()
            
            msg_recv_time = time.time_ns() # timestamp when message reaches collector
            message_split = message.strip().split(';')
            
            # add special cases here
            if '!!' == message[0:2]:
                pass # ignore !!!synchronizer!!!s
            else:
                if message_split[0] == 'ExptController':
                    if message_split[2] == 'COLLATE':
                        collate_queue.put((message_split[3], message_split[4]))
                    if message_split[2] == 'COLLATE_WITH':
                        collate_queue.put((message_split[3], message_split[4]))
                    if message_split[2] == 'QUIT':
                        stop_event.set()
                    pub_count_dict['ec'] += 1
                # for most dcs, just add to its dict and do speed calcs
                elif message_split[0] == 'host_apd':
                    if message_split[2] == 'COLLATE':
                        collate_queue.put((message_split[3], message_split[4]))
                    if message_split[2] == 'COLLATE_WITH':
                        collate_queue.put((message_split[3], message_split[4], message_split))
                elif message_split[2] == 'DATA':
                    data_json = message_split[3][1:-1]
                    ds = message_split[0]
                    ts_key = int(message_split[1])#f"{ds}_ts"
                    data_json += get_add_col_str(f"{ds}_clctr_recv_ts_{process_name}", msg_recv_time)
                    with pub_lock_dict[ds]:
                        pub_dicts[ds].append(ts_key, data_json)
                    pub_count_dict[ds] += 1
                elif message_split[2] == 'COLLATE_WITH':
                    collate_queue.put((message_split[0], message_split[3], message_split[4]))
                    # COLLATE_WITH: requester_name, req_pub_ts, COLLATE_WITH, requested_ts, data_to_append
                elif message_split[2] == 'CAPTURED_ENOUGH':
                    pass # Ignore this message
                else:
                    print(f'[{process_name}] ERROR: UNKNOWN MESSAGE!!!')# = {message}')
                    print(f"message_split: {message_split}")
                    print(f"message_split[2]: '{message_split[2]}'")

            loop_calc_time = time.time() # timestamp when Collector's done processing message-related things

            # Do message read speed calcs
            loop_stop_time = time.time()
            loop_time = loop_stop_time - loop_start_time
            loop_freq = 1.0 / loop_time
            update_hz_list.append(loop_freq)
            if len(update_hz_list) > n_running_avg:
                del update_hz_list[0]

            total_count = sum(pub_count_dict.values())
            if total_count % 1000 == 0 and total_count > 0:
                collate_queue_size = collate_queue.qsize()
                loadavg_t = os.getloadavg()
                if not count_hz_list:
                    count_ts_list.append((*loadavg_t, 0, 0, time.process_time_ns(), collate_queue_size, total_count, time.time(), *pub_count_dict.values()))
                else:
                    count_ts_list.append((*loadavg_t, sum(update_hz_list) / len(update_hz_list), sum(count_hz_list) / len(count_hz_list), time.process_time_ns(), collate_queue_size, total_count, time.time(), *pub_count_dict.values()))
                sec_end_time = time.time_ns()
                count_diff = total_count - last_total_count
                time_diff = (sec_end_time - sec_start_time)/1e9
                rate = (count_diff) / (time_diff)
                count_hz_list.append(rate)
                if len(count_hz_list) > n_running_avg:
                    del count_hz_list[0]
                sec_start_time = time.time_ns()
                last_total_count = total_count

            print_counter += 1
            if print_counter > 100:
                avg_update_hz = sum(update_hz_list) / len(update_hz_list)
                print_counter = 0

            num_pubs = len(pub_count_dict)

            if len(count_hz_list) > 5:
                avg_rate = sum(count_hz_list) / len(count_hz_list)
                # i = list(pub_count_dict.keys())[0]
                # avg_pub_hz_s = f"{i}: {str(pub_count_dict[i]).zfill(7)}; sum {num_pubs} = {total_count}; collate_qsize = {collate_queue_size}; {i} dict_len = {pub_dicts[i].counter} rate = {str(round(avg_rate)).zfill(7)}; len_update_hz_list = {len(update_hz_list)}; len_count_hz_list = {len(count_hz_list)}"
                    
                # print(f"{process_name}: {str(round(avg_update_hz)).zfill(7)} Hz".rjust(0) + ' ' + avg_pub_hz_s, end = '\r')
            loop_start_time = time.time()

            
        except zmq.Again:
            pass

        except KeyboardInterrupt as ki:
            stop_event.set()

        # Upon exit, print some performance statistics. Optionally, write those metrics to a CSV file
        # for future analysis.
        finally:
            if stop_event.is_set():
                collate_queue.join()
                collator_thread.join()
                z_sub.close()
                z_context.term()

                p_col_names = ['Load Avg 1', 'Load Avg 2', 'Load Avg 3', 'Loop Rate', 'Msg Rate', 'Proc Time', 'Collate qsize', 'Count', 'Timestamp'] + list(pub_count_dict.keys())
                df = pd.DataFrame(count_ts_list, columns = p_col_names)
                
                if dc_cfg_d['cc_write_debug_log']:
                    df.to_csv(dc_cfg_d['cc_debug_log_path'])
                print(df)
                break

    print(f'[{process_name}] (collector) exiting cleanly...')