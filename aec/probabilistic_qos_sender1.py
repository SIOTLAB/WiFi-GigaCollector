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
import random
import pandas as pd

# Simplified version of the standard AEC client that purely focuses on sending
# probabilistic access category (AC) packets. This allows modification of the AC part of 
# the generated traffic profile

PORTS = get_ports('background')

# get control ip address
MY_ADDRESS = get_my_addresses()

DEST_ADDRESS = get_server_addresses()
SENDER_SHUTDOWN_TIMEOUT = 10.0

bind_ip = MY_ADDRESS['traffic']
sock_vo = create_UDP_socket(bind_ip, 9002, get_tos('vo'), listen = False)
sock_vi = create_UDP_socket(bind_ip, 9003, get_tos('vi'), listen = False)
sock_be = create_UDP_socket(bind_ip, 9004, get_tos('be'), listen = False)
sock_bk = create_UDP_socket(bind_ip, 9005, get_tos('bk'), listen = False)


sock_vo.settimeout(0.1)
sock_vi.settimeout(0.1)
sock_be.settimeout(0.1)
sock_bk.settimeout(0.1)
print_prefix = f'[probabilistic_qos_sender]'
message = 'a'
prev_sleep_time = 0
print(f"{print_prefix} READY TO SEND")
dest_addr_traffic = DEST_ADDRESS['traffic']

start_time = time.time()
sleep_time = 0.01
send_counts = {'vo': 0, 'vi': 0, 'be': 0, 'bk': 0}

vo_prob = 0.25 * 100
vi_prob = vo_prob + 0.50 * 100
be_prob = vi_prob + 0.10 * 100
bk_prob = be_prob + 0.15 * 100

send_count_history = []

while sum(send_counts.values()) < 10_000:
    try:
        to_send = message * 100
        to_bytes = bytes(to_send.encode('ascii'))

        pkt_prob = random.randint(0, 100)
        if 0 <= pkt_prob < vo_prob:
            send_counts['vo'] += 1
            sock_vo.sendto(to_bytes, (dest_addr_traffic, 9002))
        elif vo_prob <= pkt_prob < vi_prob:
            send_counts['vi'] += 1
            sock_vi.sendto(to_bytes, (dest_addr_traffic, 9003))
        elif vi_prob <= pkt_prob < be_prob:
            send_counts['be'] += 1
            sock_be.sendto(to_bytes, (dest_addr_traffic, 9004))
        else:
            send_counts['bk'] += 1
            sock_bk.sendto(to_bytes, (dest_addr_traffic, 9005))
        
        send_count_history.append([sum(send_counts.values())] + list(send_counts.values()))
            
    except socket.timeout:
        pass

    except KeyboardInterrupt as ki:
        break

    finally:
        print(f"ST={sleep_time:.5f} === {send_counts}", end='\r')
        if sleep_time > 0:
            time.sleep(sleep_time)

print(send_counts)
df = pd.DataFrame(send_count_history, columns=['ts', 'vo', 'vi', 'be', 'bk'])
print(df)
df.to_csv('proba_qos_sender1_results.csv')