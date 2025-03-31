import socket
import re
import subprocess

def get_ports(traffic_type: str) -> dict:#[str, int]:
    traffic_type = traffic_type.strip()
    if traffic_type == 'background':
        return {'control': 9006,
                'sleeper': 9001,
                'be'     : 9002,
                'bk'     : 9003,
                'vi'     : 9004,
                'vo'     : 9005}
    elif traffic_type == 'target':
        return {'control': 8006,
                'be'     : 8002,
                'bk'     : 8003,
                'vi'     : 8004,
                'vo'     : 8005}
    else:
        raise Exception(f"[socket_utils] Invalid traffic_type = '{traffic_type}'! Choose from 'target' and 'background'.")

def get_tos(ac: str) -> int:
    ac = ac.strip()
    # tos_dict = {'be': 0,
    #             'bk': 72,
    #             'vi': 144,
    #             'vo': 220,
    #             'control': 255}
    tos_dict = {'bk': 0b0010_0000,
                'be': 0b0000_0000, # you'd expect 0110_0000 (3) to be best effort lol
                'vi': 0b1010_0000,
                'vo': 0b1110_0000,
                'control': 0b1110_0000}
    if ac in tos_dict:
        return tos_dict[ac]
    else:
        raise Exception(f"[socket_utils] Invalid ac = '{ac}'! Choose from {tos_dict.keys()}.")

def create_UDP_socket(address: str, port: int, tos: int, listen: bool = False):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, int(tos))
	# print >>sys.stderr, 'starting up on %s port %s' % server_address
    if listen: 
        listen_address = (address, int(port))
        sock.bind(listen_address)

    return sock

def create_TCP_socket(address: str, port: int, tos: int, listen: bool = False):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, int(tos))
    
	# print >>sys.stderr, 'starting up on %s port %s' % server_address
    if listen: 
        listen_address = (address, int(port))
        sock.bind(listen_address)

    return sock

def get_my_addresses() -> dict:#[str, str]:
    # get traffic ip address
    ipa_output = subprocess.run(['ip', 'a'], capture_output = True, text = True)
    traffic_ip = re.search(r'(192\.168\.0\.[0-9]{1,3})/', ipa_output.stdout)[1]
    control_ip = re.search(r'(172\.16\.0\.[0-9]{1,3})/', ipa_output.stdout)[1]
    return {'traffic': traffic_ip,
            'control': control_ip}

def get_server_addresses() -> dict:#[str, str]:
    return {'traffic': '192.168.0.4',
            'control': '172.16.0.1'}