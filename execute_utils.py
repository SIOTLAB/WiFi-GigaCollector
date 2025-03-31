import subprocess
from multiprocessing import Process, Lock
import signal
import readline
import sys
import time

# Trim dictionary based on defined maximum history length
def trim_dict(d, pname, max_length):
    if len(d) == max_length:
        try: 
            d.pop(next(iter(d.keys())))
        except KeyError as ke: 
            print(f"[trim_dict] [{pname}]: No key found when popping!:", str(ke))

# Acquire multiprocessing lock then print a message
def mpprint(l, m):
    l.acquire()
    try:
        print(m)
    finally:
        l.release()

# Execute a command in a new shell, read output of that command on stdout,
# then yield that output to calling Python code while application is still running in that shell
def execute(cmd, main_stop_event):
    popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.PIPE, universal_newlines=True, shell=True)
    for stdout_line in iter(popen.stdout.readline, ""):
        if main_stop_event and main_stop_event.is_set():
            break
        yield stdout_line
    popen.stdout.close()

# Check to see if dictionaries are populated when they should be
def sanity_check_dict(process_name, sanity_check_time, sanity_check_counter, input_dict):
    if sanity_check_counter < 1:
        if time.time() - sanity_check_time > 5: # dict sanity check runs every 5 seconds
            dict_keys = list(input_dict.keys())
            
            if len(dict_keys) == 0:
                print(f"[{process_name}] Empty keys in dict")
                return time.time(), 0

            if len(input_dict[dict_keys[0]]) == 0:
                print(f"[{process_name}] Empty values in dict")
                return time.time(), 0

            sanity_check_counter+=1

    return sanity_check_time, sanity_check_counter
