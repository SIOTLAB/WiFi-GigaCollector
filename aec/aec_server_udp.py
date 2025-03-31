from multiprocessing import Manager, Process, Lock, Queue, Event
import socket
import subprocess
import threading
import time
import numpy as np
from datetime import datetime
try: from aec.traffic_gen_utils import *
except ModuleNotFoundError: from traffic_gen_utils import *
import json
import atexit
import traceback

import os
import sys
import time
import signal
import subprocess
import threading
import queue
from simple_pid import PID

import zmq

# AEC server class. Takes EXPT_DEFs from the EC, sets up the two independent uplink and downlink
# PID controllers to adaptively generate traffic to match the desired channel utilization value,
# and uses that output to control both the server/AP-side (downlink) traffic generators and
# the client/station-side (uplink) traffic generators.

class BackgroundTrafficServer:
    def __init__(self, cfg_d):
        # Define the client Wi-Fi and ACP addresses here.
        # In this case, the 192.168.0.x subnet was used for the internet-connected Wi-Fi network
        # and 172.16.0.x was used for the local Ethernet connection between the server/AP and clients
        self.CLIENT_IPS = [
                        ('192.168.0.12', '172.16.0.12'),
                        ('192.168.0.13', '172.16.0.13'),
                        ('192.168.0.11', '172.16.0.11')
                    ]


        self.SENDER_SHUTDOWN_TIMEOUT = 10
        self.N_CLIENTS = len(self.CLIENT_IPS)
        self.REQUEST_DICT_INACTIVE = { # Default EXPT_DEF for turning all traffic off
            "bk": 0,
            "be": 0,
            "vi": 0,
            "vo": 0,
            "udb": 'B',
            "msg_size": 1400,
            "sleep_time": 0.01,
            "update": True,
        }

        self.control_dict = dict(self.REQUEST_DICT_INACTIVE)

        self.process_dicts = self.setup_processes(cfg_d.copy())
        self.controller()
        # atexit.register(self.clean_up)

    def controller(self):#, cfg_d):
        PRINT_INDENT = ' ' * 0
        # Init zmq control publishers and subscribers
        process_name = 'bts_controller_zmq'
        z_context = zmq.Context()
        # DC pub
        z_dc_pub = z_context.socket(zmq.PUB)
        z_dc_pub_bind_addr = cfg_d['dc_pub_bind_addr']
        z_dc_pub.bind(z_dc_pub_bind_addr)
        print(f"[{process_name}] zmq dc pub socket bound: {z_dc_pub_bind_addr}")
        
        
        # bts_recvr_stats_pub
        z_bts_recvr_stats_pub = z_context.socket(zmq.PUB)
        z_bts_recvr_stats_pub_bind_addr = cfg_d['aec_s_recvr_stats_pub_bind_addr']
        z_bts_recvr_stats_pub.bind(z_bts_recvr_stats_pub_bind_addr)
        print(f"[{process_name}] zmq aec_s_recvr_stats pub socket bound: {z_bts_recvr_stats_pub_bind_addr}")

        expt_d_lock = threading.Lock()
        chan_stats_lock = threading.Lock()
        chan_stats_ready = threading.Event()
        chan_stats_dict = {}
        expt_run = threading.Event()
        stop_event = threading.Event()
        safe_to_print = False
        pid_ul = PID(-0.003, 4 * -0.003, -0.0000005, setpoint=0.5) # Set PID constants for uplink controller
        pid_dl = PID(-0.003, 4 * -0.003, -0.0000005, setpoint=0.5) # Set PID constants for downlink controller
        pid_ul.output_limits = (0, 0.015)
        pid_dl.output_limits = (0, 0.02)

        # EC control message subscriber thread; relays information to other AEC subsystems when necessary
        def control_sub_thread(cfg_d, z_ctx, expt_d, expt_d_lock, expt_run, chan_stats_dict, chan_stats_ready, chan_stats_lock, stop_event):
            print('[control_sub_thread] start...')
            z_control_sub = z_ctx.socket(zmq.SUB)
            # EC sub
            z_ec_sub_addr = cfg_d['ec_sub_addr']
            z_control_sub.connect(z_ec_sub_addr)
            print(f"[{process_name}] zmq ec sub socket connected: {z_ec_sub_addr}")
            # chan_stats sub
            z_chan_stats_sub_addr = cfg_d['chan_stats_sub_addr']
            z_control_sub.connect(z_chan_stats_sub_addr)
            print(f"[{process_name}] zmq chan_stats sub socket connected: {z_chan_stats_sub_addr}")
            z_control_sub.subscribe('')

            exit_flag = False
            while not exit_flag:
                try:
                    message = z_control_sub.recv_string()

                    if '!!' == message[0:2]:
                        pass # ignore syncs
                    else:
                        message_split = message.split(';')

                        if message_split[0] == 'wifi_chan_stats' and message_split[2] == 'DATA':
                            new_chan_stats_dict = json.loads(message_split[3])
                            with chan_stats_lock:
                                for k, v in new_chan_stats_dict.items():
                                    chan_stats_dict[k] = v
                            chan_stats_ready.set()
                        elif message_split[0] == 'ExptController':
                            control_pub_queue.put('!!!synchronizer!!!')
                            if message_split[2] == 'QUIT':
                                stop_event.set()
                            elif message_split[2] == 'EXPT_DEF':
                                print(f"[bts_controller_zmq] GET NEW EXPT_D")
                                control_pub_queue.put(message) # pass it on from EC
                                new_expt_d = json.loads(message_split[3])
                                with expt_d_lock:
                                    for k, v in new_expt_d.items():
                                        expt_d[k] = v
                                    # expt_d['update'] = False
                                    # ul_pid.setpoint = expt_d['t_ch_util']
                            elif message_split[2] == 'BTS_START_TRAFFIC':
                                expt_run.set()
                                control_pub_queue.put(message) # pass it on from EC
                            elif message_split[2] == 'BTS_STOP_TRAFFIC':
                                expt_run.clear()
                                control_pub_queue.put(message) # pass it on from EC

                    if stop_event.is_set():
                        exit_flag = True

                except KeyboardInterrupt as ki:
                    exit_flag = True

                except zmq.error.ContextTerminated:
                    exit_flag = True

                finally:
                    if exit_flag:
                        z_control_sub.close()
                        break

        control_pub_queue = queue.Queue()
                    
        chutilito = threading.Thread(target = control_sub_thread, args = (cfg_d, z_context, expt_d, expt_d_lock, expt_run, chan_stats_dict, chan_stats_ready, chan_stats_lock, stop_event,))
        chutilito.start()

        def control_pubber():
            # btc_control_pub
            z_btc_control_pub = z_context.socket(zmq.PUB)
            z_btc_control_pub_bind_addr = cfg_d['aec_c_control_pub_bind_addr']
            z_btc_control_pub.bind(z_btc_control_pub_bind_addr)
            print(f"[{process_name}] zmq aec_c_control pub socket bound: {z_btc_control_pub_bind_addr}")
            # bts_sp_control_pub
            z_bts_sp_control_pub = z_context.socket(zmq.PUB)
            z_bts_sp_control_pub_bind_addr = cfg_d['aec_sp_control_pub_bind_addr']
            z_bts_sp_control_pub.bind(z_bts_sp_control_pub_bind_addr)
            print(f"[{process_name}] zmq aec_sp_control pub socket bound: {z_bts_sp_control_pub_bind_addr}")

            while not stop_event.is_set():
                try:
                    item = control_pub_queue.get(timeout=0.1)
                    # If it isn't a sleep time, then it was meant to be passed on
                    if type(item) == tuple:
                        header = f"{process_name};{time.time_ns()};"
                        if item[0] == 'u':
                            z_btc_control_pub.send_string(f"{header}ST;{item[1]}")
                        elif item[0] == 'd':
                            z_bts_sp_control_pub.send_string(f"{header}ST;{item[1]}")
                    else:
                        z_btc_control_pub.send_string(item)
                        z_bts_sp_control_pub.send_string(item)
                except queue.Empty as qe:
                    pass
            z_btc_control_pub.close()
            z_bts_sp_control_pub.close()

        control_pub_thread = threading.Thread(target = control_pubber, args = ())
        control_pub_thread.start()

        # Init sleep calcs
        prev_ul_sleep_time = ul_sleep_time = 0.0
        prev_dl_sleep_time = dl_sleep_time = 0.0
        local_avg_chutil_list = []
        local_avg_ul_util_list = []
        local_avg_dl_util_list = []

        local_avg_ul_sleep_time_list = []
        local_avg_dl_sleep_time_list = []

        ul_dl_ratio = 0.5
        prev_err = 0
        err = 0
        err_d = err-prev_err
        lr = .001
        lr2 = .001
        target_ch_util = 0.5

        use_gradient = True

        prev_lehist_filename = lehist_filename = expt_d['lehist_filename']

        def open_lehist_file(fn):
            try:
                lehist_file = open(fn, 'w')
                # lehist_file.write(write_string = f"ts,target_ch_util,ch_util_error,ch_util,l_avg_chutil,avg_ch_util,std_ch_util,udr,t_ul_util,t_dl_util,l_avg_ul_util,avg_ul_util,std_ul_util,l_avg_dl_util,avg_dl_util,std_dl_util,ul_sleep_time,l_avg_ul_sleep_time,dl_sleep_time,l_avg_dl_sleep_time,loop_freq,loop_time\n")
                return lehist_file
            except Exception as e:
                print(f"[{process_name}] Error opening AEC server log file at '{lehist_filename}': {str(e)}")
                return None

        lehist_file = open_lehist_file(lehist_filename)
        write_string = f"ts,target_cu,cu_error,cu,cu_avg0.02s,cu_avg3s,cu_std3s,ud_ratio,target_ul_util,target_dl_util,ulu_avg0.02s,ulu_avg3s,ulu_std3s,dlu_avg0.02s,dlu_avg3s,dlu_std3s,ul_st,ul_st_avg0.02s,dl_st,dl_st_avg0.02s,loop_slept_time,loop_time\n"
        lehist_file.write(write_string)

        # Start control loop
        max_freq = cfg_d['max_data_freq'] # hz
        if max_freq != 0:
            max_loop_time = 1.0 / max_freq
        else:
            max_loop_time = 0.0
        slept_loop_time = 0.0
        loop_sleep_diff = max_loop_time
        loop_time = 0.0
        loop_slept_freq = 0.0
        
        csd = {}

        exit_flag = False
        loop_start_time = time.time()
        while not exit_flag:
            try:
                if expt_run.is_set() and chan_stats_ready.is_set():
                    with expt_d_lock:
                        # Send EXPT_DEF if there's a new update
                        if expt_d['update']:
                            # lehist_filename = expt_d['lehist_filename'] TD
                            target_ch_util = expt_d['t_ch_util']
                            ul_dl_ratio = expt_d['ud_ratio']
                            target_ul_util = ul_dl_ratio * target_ch_util
                            target_dl_util = target_ch_util - target_ul_util
                            pid_ul.setpoint = target_ul_util
                            pid_dl.setpoint = target_dl_util
                                
                            expt_d['update'] = False

                    with chan_stats_lock:
                        for k, v in chan_stats_dict.items():
                                csd[k] = v
                    
                    if lehist_filename != prev_lehist_filename: # If filename changed, make new lehist for new experiment
                        lehist_file.close()
                        lehist_file = open_lehist_file(lehist_filename)
                        prev_lehist_filename = lehist_filename

                    local_avg_chutil_list.append(csd['ch_util'])
                    if len(local_avg_chutil_list) > 100:
                        del local_avg_chutil_list[0]
                    local_avg_chutil = sum(local_avg_chutil_list) / 100

                    local_avg_ul_util_list.append(csd['ch_util_uplink'])
                    if len(local_avg_ul_util_list) > 100:
                        del local_avg_ul_util_list[0]
                    local_avg_ul_util = sum(local_avg_ul_util_list) / 100

                    local_avg_dl_util_list.append(csd['ch_util_downlink'])
                    if len(local_avg_dl_util_list) > 100:
                        del local_avg_dl_util_list[0]
                    local_avg_dl_util = sum(local_avg_dl_util_list) / 100
                    
                    # Update sleep time and send if it's different from prev value
                    if use_gradient: # Use PID controllers
                        ul_sleep_time = pid_ul(csd['ch_util_uplink'])
                        dl_sleep_time = pid_dl(csd['ch_util_downlink'])
                            
                    else: # Use simple flat incremental controllers
                        # Do uplink util calcs
                        if local_avg_ul_util > target_ul_util:
                            ul_sleep_time += 0.00001
                            ul_sleep_time = min(0.5, ul_sleep_time)
                        else:
                            ul_sleep_time -= 0.00001
                            ul_sleep_time = max(0, ul_sleep_time)

                        # Do downlink util calcs
                        if local_avg_dl_util > target_dl_util:
                            dl_sleep_time += 0.00001
                            dl_sleep_time = min(0.5, dl_sleep_time)
                        else:
                            dl_sleep_time -= 0.00001
                            dl_sleep_time = max(0, dl_sleep_time)

                    # If sleep time updated, send it out
                    ts = time.time_ns()
                    if prev_ul_sleep_time != ul_sleep_time:
                        control_pub_queue.put(('u',ul_sleep_time))
                        prev_ul_sleep_time = ul_sleep_time
                    if prev_dl_sleep_time != dl_sleep_time:
                        control_pub_queue.put(('d',dl_sleep_time))
                        prev_dl_sleep_time = dl_sleep_time

                    # Calculate statistics
                    ch_util_error = csd['ch_util'] - target_ch_util

                    local_avg_ul_sleep_time_list.append(ul_sleep_time)
                    if len(local_avg_ul_sleep_time_list) > 100:
                        del local_avg_ul_sleep_time_list[0]
                    local_avg_ul_sleep_time = sum(local_avg_ul_sleep_time_list) / 100

                    local_avg_dl_sleep_time_list.append(dl_sleep_time)
                    if len(local_avg_dl_sleep_time_list) > 100:
                        del local_avg_dl_sleep_time_list[0]
                    local_avg_dl_sleep_time = sum(local_avg_dl_sleep_time_list) / 100

                    # Write info into lehist
                    write_string = f"{ts},{target_ch_util},{ch_util_error},{csd['ch_util']},{local_avg_chutil},{csd['avg_ch_util']},{csd['std_ch_util']},{ul_dl_ratio},{target_ul_util},{target_dl_util},{local_avg_ul_util},{csd['avg_ul_util']},{csd['std_ul_util']},{local_avg_dl_util},{csd['avg_dl_util']},{csd['std_dl_util']},{ul_sleep_time},{local_avg_ul_sleep_time},{dl_sleep_time},{local_avg_dl_sleep_time},{loop_slept_freq},{loop_time}\n"
                    lehist_file.write(write_string)
                    # Publish to CC? Not really needed
                    # z_dc_pub.send_string('')
  
                    # Print info
                    safe_to_print = True
                loop_calc_time = time.time()
                    

            except KeyboardInterrupt as ki:
                exit_flag = True
                stop_event.set()
                time.sleep(0.5)

            finally:
                try:
                    # Sleep to maintain consistent update frequency if needed
                    loop_stop_time = time.time()
                    loop_time = loop_stop_time - loop_start_time
                    loop_slept_freq = 1.0 / (loop_time)
                    if max_freq != 0:
                        loop_sleep_diff = max_loop_time - loop_time
                        loop_sleep_diff = max(0, loop_sleep_diff)
                        time.sleep(loop_sleep_diff)
                    if safe_to_print == True:
                        # if use_gradient:
                        #     status_string_debug = f"{PRINT_INDENT}BTS Upd({str(round(loop_slept_freq)).zfill(7)}) LSD({loop_sleep_diff:.8f}) T({round(target_ch_util, 1)},SP{round(ul_pid.setpoint, 1)}->LAv{local_avg_chutil:.2f};Av{csd['avg_ch_util']:.2f};Sd{csd['std_ch_util']:.2f}) Sl({sleep_time:.5f}) U({csd['avg_ul_util']:.2f}) D({csd['avg_dl_util']:.2f}) ||| "
                        # else:
                        status_string_debug = f"{PRINT_INDENT}BTS Upd({str(round(loop_slept_freq)).zfill(7)}) LSD({loop_sleep_diff:.8f}) T({round(target_ch_util, 1)}->LAv{local_avg_chutil:.2f};Av{csd['avg_ch_util']:.2f};Sd{csd['std_ch_util']:.2f}) SlU({ul_sleep_time:.5f} SlD{dl_sleep_time:.5f}) U({local_avg_ul_util:.2f},{csd['avg_ul_util']:.2f}) D({local_avg_dl_util:.2f},{csd['avg_dl_util']:.2f}) ||| "
                        print(status_string_debug, end='\r')
                    loop_start_time = time.time()
                    
                except KeyboardInterrupt as ki:
                    print(f"[{process_name}]: interrupt received, stopping: {str(ki)}")
                    stop_event.set()
                    exit_flag = True
                    
                if exit_flag:
                    time.sleep(2)
                    # Close all sockets
                    z_dc_pub.close()
                    # z_btc_control_pub.close()
                    # z_bts_sp_control_pub.close()
                    z_bts_recvr_stats_pub.close()
                    # close context
                    z_context.term()
                    # close file
                    lehist_file.close()
                    break

    def traffic_sender_manager_zmq(self, cfg_d, expt_d): # mt this in controller
        print_prefix = '[traffic_sender_manager_zmq]'
        pub_addr = cfg_d['bts_pub_base_bind_addr']
        # start the senders
        senders = {}
        for ac, port in cfg_d['bt_ports'].items():
            senders[f"{ac}_sender"] = {
                "process": None,
                "popen": None,
                "target": self.traffic_sender_zmq,
                "args":  {'pub_port': port, 'ac': ac, "tos": get_tos(ac), 'aec_sp_control_pub_bind_addr': cfg_d['aec_sp_control_pub_bind_addr'], "expt_d": expt_d.copy()},
            }

        for process_name, params in senders.items():
            process_obj = threading.Thread(target=params['target'], name=process_name, args=(arg for arg in params['args'].values()))
            senders[process_name]['process'] = process_obj
            process_obj.start()

        print(f"{print_prefix} Started BTS publishers...")

        # wait for join
        print(f"{print_prefix} Waiting for all senders to join...")
        for sender_name in senders:
            senders[sender_name]['process'].join()

    def traffic_sender_zmq(self, port, ac, tos, bts_sp_control_pub_bind_addr, expt_d):
        process_name = f'traffic_sender_zmq_{ac}'
        bind_ip = '192.168.0.4'#self.MY_ADDRESS['traffic']
        print(f"[{process_name}] starting for {bind_ip}:{port} @ {ac} = {tos}; control listening on {bts_sp_control_pub_bind_addr}")
        sock = create_UDP_socket(bind_ip, port, tos, listen = False)
        sock.settimeout(0.1)
        print_prefix = f'[{process_name} {bind_ip}:{port} = {tos} -> {ac}]'
        message = 'a'
        prev_sleep_time = 0
        print(f"{print_prefix} READY TO SEND")
        dest_addr_traffic = '192.168.0.15'#self.DEST_ADDRESS['traffic']
        
        stop_event = threading.Event()
        expt_d = expt_d
        expt_d_lock = threading.Lock()
        global sleep_time
        sleep_time = 0.0
        exit_flag = False
        send_stuff = threading.Event()
        
        def control_listener():
            z_context = zmq.Context()
            sub_socket = z_context.socket(zmq.SUB)
            sub_socket.setsockopt(zmq.RCVTIMEO, 100)
            sub_socket.connect(bts_sp_control_pub_bind_addr)
            sub_socket.subscribe('')
            global sleep_time
            print(f"{process_name}-control_listener starting: {bts_sp_control_pub_bind_addr}")

            while not stop_event.is_set():
                try:
                    message = sub_socket.recv_string()
                    # print(f"[traffic_sender_zmq-control_listener] RECVED: {message}")
                    if '!!' == message[0:2]:
                        pass # ignore syncs
                    else:
                        message_split = message.split(';')
                        # print(f"[traffic_sender_zmq-control_listener] {message_split}")
                        if message_split[0] == 'ExptController':
                            if message_split[2] == 'QUIT':
                                stop_event.set()
                            elif message_split[2] == 'EXPT_DEF':
                                print(f"[traffic_sender_zmq-control_listener] GOT NEW EXPT_D")
                                new_expt_d = json.loads(message_split[3])
                                with expt_d_lock:
                                    for k, v in new_expt_d.items():
                                        expt_d[k] = v
                                    expt_d['update'] = False
                                    if expt_d['ud_ratio'] < 0.95:
                                        # if send_stuff.is_set()
                                        send_stuff.clear()
                                    else:
                                        send_stuff.set()
                                    # if expt_d[ac] == 1:
                                    #     send_stuff.set()
                                    # else:
                                    #     send_stuff.clear()
                            elif message_split[2] == 'BTS_START_TRAFFIC':
                                send_stuff.set()
                            elif message_split[2] == 'BTS_STOP_TRAFFIC':
                                send_stuff.clear()
                        if message_split[0] == 'bts_controller_zmq':
                            if message_split[2] == 'ST':
                                sleep_time = float(message_split[3])
                except zmq.Again:
                    pass

                except KeyboardInterrupt as ki:
                    stop_event.set()
                finally:
                    if exit_flag:
                        sub_socket.close()
                        print('traffic_receiver_zmq (control_listener) exiting cleanly...')
                        break

        control_listener_thread = threading.Thread(target = control_listener, name=f"{process_name}_control_listener", args = ())
        control_listener_thread.start()
        send_stuff.set()
        
        while not stop_event.is_set():
            try:
                if send_stuff.is_set():
                    to_send = message * expt_d['packet_size']
                    to_bytes = bytes(to_send.encode('ascii'))
                    sock.sendto(to_bytes, (dest_addr_traffic, port))
                    
            except socket.timeout:
                pass

            except KeyboardInterrupt as ki:
                stop_event.set()

            finally:
                if ac[1] == 'o':
                    print(f"ST={sleep_time:.5f}, SToffset = ?", end='\r')
                if sleep_time > 0:
                    time.sleep(sleep_time)
                if exit_flag:
                    break
        
        control_listener_thread.join()
        pub_socket.close()
        z_context.term()
        print(f'{process_name} exiting cleanly...')

    def setup_processes(self, cfg_d):
        prefix = '[background_setup_processes]'
        process_dicts = {
            # "traffic_receiver_zmq": { # Save CPU resources by ignoring messages instead of handling them; can add back in if need to measure something
            #     "process": None,
            #     "popen": None,
            #     "type": "process",
            #     "target": self.traffic_receiver_zmq,
            #     "args":  {"cfg_d": cfg_d.copy()},
            # },
            "traffic_sender_manager_zmq": {
                "process": None,
                "popen": None,
                "type": "thread",
                "target": self.traffic_sender_manager_zmq,
                "args":  {"cfg_d": cfg_d.copy(), "expt_d": expt_d.copy()},
            },
        }
        for process_name, params in process_dicts.items():
            if params['type'] == 'process':
                process_obj = Process(target=params['target'], name=process_name, args=(arg for arg in params['args'].values()))
            elif params['type'] == 'thread':
                process_obj = threading.Thread(target=params['target'], name=process_name, args=(arg for arg in params['args'].values()))
            else:
                raise Exception(f"{prefix} '{process_name}' type '{params['type']}' is invalid!")
            process_dicts[process_name]['process'] = process_obj
            process_obj.start()  
        print(f"{prefix} All process gudo")
        return process_dicts

    # def clean_up(self):
    #     self.main_stop_event.set()
        # for process_name in self.process_dicts:
        #     self.process_dicts[process_name]['process'].join()

if __name__ == "__main__":
    def pauseroo(msg):
        input(f"\n\n[NOTE] {msg}\n\n")

    try:
        cfg_d = {
            'ec_sub_addr': 'tcp://localhost:5550',                        # ZMQ subscriber address for EC control messages
            'dc_pub_bind_addr': 'tcp://localhost:5600',                   # ZMQ publisher address for overall stats (unused)
            'chan_stats_sub_addr': 'ipc:///tmp/gigacol/5650',             # ZMQ subscriber address for Wi-Fi channel statistics (ds_chan_stats.py)
            'aec_c_control_pub_bind_addr': 'tcp://172.16.0.1:9001',       # ZMQ publisher address for publishing control messages to clients
            'aec_sp_control_pub_bind_addr': 'ipc:///tmp/gigacol/5601',    # ZMQ publisher address for server-side internal control messages
            'aec_s_recvr_stats_pub_bind_addr': 'ipc:///tmp/gigacol/5602', # ZMQ publisher address for receiver statistics (unused now)
            'bt_ports': {                            # Ports to send background traffic on
                'bk': 9002,                          # BacKground (BK)
                'be': 9003,                          # Best Effort (BE)
                'vi': 9004,                         # VIdeo (VI)
                'vo': 9005                          # VOice (VO)
            },
            'btc_sub_base_addrs': {                  # Addresses of client stations to transfer traffic with
                # 'btc_s1': 'tcp://192.168.0.11',    # name:address pair
                # 'btc_s2': 'tcp://192.168.0.12',    
                # 'btc_s3': 'tcp://192.168.0.13',
                'btc_s5': 'tcp://192.168.0.15',
            },
            'bts_pub_base_bind_addr': 'tcp://192.168.0.4', # Address on the server/AP for the AEC server to bind to
            'max_data_freq': 5000,                         # Frequency at which the PID controllers will run
        }

        expt_d = {
            'bk': False,         # Send BacKground (BK) access category packets
            'be': False,         # Send Best Effort (BE) access category packets
            'vi': False,         # Send VIdeo (VI) access category packets
            'vo': False,         # Send VOice (VO) access category packets
            'udb': 'U',          # 'U' for Uplink traffic only, 'D' for Downlink only, 'B' for Both
            't_ch_util': 0.3,    # Desired total channel utilization from all traffic on network
            'packet_size': 1400, # Packet sizes to generate
            'ud_ratio': 0.5,     # Uplink/downlink ratio: 0.0 for all downlink, 1.0 for all uplink, 0.5 for even mix of both
            'lehist_filename': f"./data/aecs_log_{datetime.now().strftime('%Y-%m-%d-%H.%M.%S')}.csv", # AEC server debug log filename
            'update': True       # Take effect immediately
        }

        bts = BackgroundTrafficServer(cfg_d)   

    except Exception as e:
        print(f"Error: unable to start processes: {str(e)}, {traceback.format_exc()}")

    pauseroo("Press ENTER at any time to cleanly exit!")
    # main_stop_event.set()
    # bts.clean_up()
