from aec import traffic_gen_utils
import socket

# Encourage AEC clients to reconnect to hostapd network earlier than default interval

def send_wifi_reconnect(STATIONS_MAC_DICT):
        bind_ip = traffic_gen_utils.get_my_addresses()['control']
        port = 42069
        tos = 255
        sock = traffic_gen_utils.create_UDP_socket(bind_ip, port, tos, listen = False)
        print_prefix = '[send_wifi_reconnect:' + str(bind_ip) + ':' + str(port) + ' = ' + str(tos) + ']'
        message = 'WIFI_RECONNECT'
        sock.settimeout(1.0)
        
        for station_mac, station_dict in STATIONS_MAC_DICT.items():
            station_control_ip = station_dict['control_ip']
            try:
                # to_send = ''.join(random.choices(string.ascii_letters + string.digits + string.punctuation + ' ', k=control_dict['msg_size']))
                to_send = message
                to_bytes = to_send.encode('ascii')
                # Send data
                # print(f'{print_prefix} msg: "{to_send}" len "{len(to_bytes)}"')
                # print(f'{print_prefix} msg: len "{len(to_bytes)}"', end = '\r')
                print(f"{print_prefix} {to_send} -> {station_control_ip}")
                if station_control_ip == '172.16.0.15':
                    sent = sock.sendto(to_bytes, (station_control_ip, 8006))
                else:
                    sent = sock.sendto(to_bytes, (station_control_ip, 9006))
                # control_dict['total_packets_sent_since_expt_start'] += 1

            except socket.timeout as ste:
                print(print_prefix + ": socket timeout = " + str(1.0))# + " reached; stopping target traffic sender threads")

            except Exception as e:
                print(print_prefix, " exception: " + str(e))

        sock.close()
        print(print_prefix, 'TERMINATING.')

if __name__ == '__main__':
    STATIONS_MAC_DICT = {
    '00:11:22:33:44:55': {
        'connected': False,
        'control_ip': '172.16.0.11',
        },
    '00:11:22:33:44:55': {
        'connected': False,
        'control_ip': '172.16.0.15',
        },
    '00:11:22:33:44:55': {
        'connected': False,
        'control_ip': '172.16.0.12',
        }, 
    '00:11:22:33:44:55': {
        'connected': False,
        'control_ip': '172.16.0.13',
        },
    }
    print("[send_wifi_reconnect] Trying to initiate traffic client reconnection...")
    send_wifi_reconnect(STATIONS_MAC_DICT)