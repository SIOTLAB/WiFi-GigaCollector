from multiprocessing import Manager, Process, Lock, Queue, Event
import socket
from socket import timeout
import sys
import _thread as thread
import time
from traffic_gen_utils import *
import json
import atexit
import traceback
import threading
import zmq

# AEC client class. Takes EXPT_DEFs and sleep time (IPSI) messages from the AEC server over ACP (Ethernet)
# and generates traffic. ACP helps isolate control traffic from experimental Wi-Fi traffic.
# Runs standalone on a separate system, such as a Raspberry Pi. Needs pyzmq to be installed, and only
# tested on Python 3.8+.

class BackgroundTrafficClientZMQ:
    def __init__(self, cfg_d, expt_d):
        self.PORTS = get_ports('background')

        # Get ACP (Ethernet) IP address
        self.MY_ADDRESS = get_my_addresses()

        self.DEST_ADDRESS = get_server_addresses()
        self.SENDER_SHUTDOWN_TIMEOUT = 10.0

        self.process_dicts = self.setup_processes(cfg_d.copy(), expt_d.copy())
        atexit.register(self.clean_up)

    # Encourage Wi-Fi adapter to reconnect faster than the default interval after losing connection;
    # useful when hostapd is restarted and the wait period takes too long
    def wifi_reconnect(self):
        prefix = "[target_wifi_reconnect]"
        print(f"{prefix} RECONNECTING WIFI!!!!!")
        try:
            subprocess.run("sudo ifconfig wlan0 down", shell = True, capture_output = True, check = True)
            subprocess.run("sudo ifconfig wlan0 up", shell = True, capture_output = True, check = True)
            subprocess.run("sudo iw wlan0 set power_save off", shell = True, capture_output = True, check = True)
        except subprocess.CalledProcessError as cpe:
            print(f"{prefix} Error while reiniting wlan0: {cpe.returncode}, {cpe.output}")

    # Uplink traffic generation process. Sends UDP packets so that traffic is purely unidirectional; TCP still has a
    # reverse-direction ACK which affects channel utilization
    def traffic_sender_zmq(self, port, ac, tos, btc_control_pub_bind_addr, expt_d):
        process_name = f'traffic_sender_zmq_{ac}'
        bind_ip = self.MY_ADDRESS['traffic']
        print(f"[{process_name}] starting for {bind_ip}:{port} @ {ac} = {tos}; control listening on {btc_control_pub_bind_addr}")
        sock = create_UDP_socket(bind_ip, port, tos, listen = False)
        sock.settimeout(0.1)
        print_prefix = f'[{process_name} {bind_ip}:{port} = {tos} -> {ac}]'
        message = 'a'
        prev_sleep_time = 0
        print(f"{print_prefix} READY TO SEND")
        dest_addr_traffic = self.DEST_ADDRESS['traffic']
        
        stop_event = threading.Event()
        expt_d = expt_d
        expt_d_lock = threading.Lock()
        global sleep_time
        sleep_time = 0.0
        exit_flag = False
        send_stuff = threading.Event()
        
        # Control listener thread to listen to the AEC server over ACP.
        # Depending on the access category key in the EXPT_DEF, enables or disables traffic from the respective sender.
        # Also listens for sleep times (IPSIs) for inter-packet pauses from the AEC server.
        def control_listener():
            z_context = zmq.Context()
            sub_socket = z_context.socket(zmq.SUB)
            sub_socket.setsockopt(zmq.RCVTIMEO, 100)
            sub_socket.connect(btc_control_pub_bind_addr)
            sub_socket.subscribe('')
            global sleep_time
            print(f"{process_name}-control_listener starting: {btc_control_pub_bind_addr}")

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
                                    if expt_d['ud_ratio'] < 0.05:
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

    # Configure and start all sender processes. Each process has its own control listener which subscribes to 
    # the AEC server client control publisher. Can switch between multithreading and multiprocessing by
    # changing the "type" value in the corresponding process_dict. For example, if the VO packet sender
    # should be multiprocessed, then "type": "process".
    def setup_processes(self, main_stop_event, control_dict) -> dict:
        prefix = '[background_setup_processes]'
        process_dicts = {
            "bk_sender": {
                "process": None,
                "popen": None,
                "type": "thread",
                "target": self.traffic_sender_zmq,
                "args":  {"port": self.PORTS['bk'], "ac": "bk", "tos": get_tos('bk'), "btc_control_pub_bind_addr": cfg_d['btc_control_pub_bind_addr'], "expt_d": expt_d.copy()},
            },
            "be_sender": {
                "process": None,
                "popen": None,
                "type": "thread",
                "target": self.traffic_sender_zmq,
                "args":  {"port": self.PORTS['be'], "ac": "be", "tos": get_tos('be'), "btc_control_pub_bind_addr": cfg_d['btc_control_pub_bind_addr'], "expt_d": expt_d.copy()},
            },
            "vi_sender": {
                "process": None,
                "popen": None,
                "type": "thread",
                "target": self.traffic_sender_zmq,
                "args":  {"port": self.PORTS['vi'], "ac": "vi", "tos": get_tos('vi'), "btc_control_pub_bind_addr": cfg_d['btc_control_pub_bind_addr'], "expt_d": expt_d.copy()},
            },
            "vo_sender": {
                "process": None,
                "popen": None,
                "type": "thread",
                "target": self.traffic_sender_zmq,
                "args":  {"port": self.PORTS['vo'], "ac": "vo", "tos": get_tos('vo'), "btc_control_pub_bind_addr": cfg_d['btc_control_pub_bind_addr'], "expt_d": expt_d.copy()},
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
        print("[setup_processes] All processes ready.")
        return process_dicts

    def clean_up(self):
        for process_name in self.process_dicts:
            self.process_dicts[process_name]['process'].join()


# Set up standalone AEC client. Note the required ports and the 172.16.0.x subset addresses for ACP traffic
# Currently does not cleanly exit unless a control message is relayed from the EC to this client instance; TODO item
if __name__ == '__main__':
    try:
        # l = Lock()
        # manager = Manager()
        # main_stop_event = manager.Event()

        control_dict = dict(
            {
                "bk": 0,
                "be": 0,
                "vi": 0,
                "vo": 0, 
                "msg_size": 100, # Bytes
                "udb": 'B', # (U)p/(D)own/(B)oth
                "sleep_time": 1.0,
                "last_sleep_update": time.time(),
            }
        )

        cfg_d = {
            'ec_sub_addr': 'tcp://172.16.0.1:5550',
            'btc_control_pub_bind_addr': 'tcp://172.16.0.1:9001',

            'bt_ports': {
                'bk': 9002,
                'be': 9003,
                'vi': 9004,
                'vo': 9005
                # 'vo': 9002,
            },
        }

        expt_d = {
            'bk': True,
            'be': True,
            'vi': True,
            'vo': True,
            'udb': 'U',
            't_ch_util': 0.3,
            'packet_size': 1400,
            'ud_ratio': 0.5,
            'update': True
        }
        
        btc = BackgroundTrafficClientZMQ(cfg_d.copy(), expt_d.copy())


    except Exception as e:
        print("Error: unable to start processes", traceback.format_exc())

    btc.clean_up()