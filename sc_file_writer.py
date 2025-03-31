import zmq
import json
import time
from datetime import datetime
import pandas as pd
import multiprocessing
import sys

# Snapshot Consumer process. Subscribes to collator(s), saves collated snapshots, then writes all data to a file at exit.
# out_fname: output CSV filename
# l_publisher_addrs: list of collator addresses. For example, [tcp://localhost:6551', 'tcp://localhost:5550'].
# In this case:
# * 'tcp://localhost:6551' is the one collator process that will be used
# * 'tcp://localhost:5550' is the EC that sends EXIT signals.
def csv_writer(cfg_d):
    # Helper function to add new columns to a JSON string
    def get_add_col_str(col_name, col_value):
        if type(col_value) == str:
            return f', "{col_name}": "{col_value}"'
        else:
            return f', "{col_name}": {col_value}'

    out_fname = cfg_d['out_fname']
    process_name = cfg_d['process_name']

    
    print(f"[{process_name}] Starting...")
    z_context = zmq.Context()
    # collator sub
    z_sub = z_context.socket(zmq.SUB)
    z_sub.setsockopt(zmq.RCVTIMEO, 100)
    for name, addr in cfg_d['ccs'].items():
        z_sub.connect(addr)
    # z_sub.connect('tcp://localhost:6551') # collector 0
    # z_sub.connect('tcp://localhost:6552') # collector 1
    # z_sub.connect('tcp://localhost:5550')
    z_sub.connect(cfg_d['ec_sub_addr'])
    z_sub.subscribe('')

    # collated_list = list()
    exit_flag = False
    collated_count = 0
    indent = ' ' * 0
    collated_dict = dict()

    good_to_save_data = False

    while not exit_flag:
        try:
            message = z_sub.recv_string()
            if 'sync' in message or 'COLLATE' in message:
                continue
            elif 'ExptController' in message:
                message_split = message.split(';')
                if message_split[2] == 'EXPT_START_RECORD_CSV':
                    good_to_save_data = True
                elif message_split[2] == 'EXPT_STOP_RECORD_CSV' or 'QUIT' in message_split[2]: # a special signal that only stops instances of this subscriber
                    good_to_save_data = False
                    exit_flag = True
                    break
                    
            else:
                if good_to_save_data:
                    # Parse snapshot response from collator(s)
                    message_split = message.split(';')
                    # Can filter by requester_name of snapshot if desired; just going to catch all collations here
                    # if message_split[0] in ['DS_SimSniffer']: # or something like this
                    collator_id = message_split[1]
                    requested_ts = message_split[2]
                    
                    data = message_split[4]
                    data = data[1:-1] + (get_add_col_str(f'df_recv_ts_{collator_id}', time.time_ns()) + get_add_col_str('data_len', len(data)))
                    # If it's not the first time seeing this requested timestamp, append to existing data. This allows combining parallel CC data
                    if requested_ts in collated_dict:
                        collated_dict[requested_ts] += f",{data}"
                    else:
                        collated_dict[requested_ts] = data
                    collated_count += 1
                print(f"{indent}[{process_name}] Received snapshot count: {collated_count}", end = '\r')
        except zmq.Again:
            pass
        except json.decoder.JSONDecodeError as jde:
            print(jde)
            print(message)
            exit_flag = True
        except KeyboardInterrupt as ki:
            print(f"[{process_name}]: interrupt received, stopping: {str(ki)}")
            exit_flag = True
        finally:
            if exit_flag:
                print(f"[{process_name}] safely closing zmq stuff...")
                z_sub.close()
                z_context.term()
                break

    print(f"[{process_name}] Converting JSON snapshots to dictionaries for DataFrame initialization...")
    try:
        collated_dicts_list = []
        for ts, data in collated_dict.items():
            data2 = "{" + data + "}"
            collated_dicts_list.append(json.loads(data2))
    except json.decoder.JSONDecodeError as jde:
            print(jde)
            print(data)
    print(f"[{process_name}] Writing to DataFrame...")
    df = pd.DataFrame.from_records(collated_dicts_list)
    cols_to_keep = list(filter(lambda x: True if 'data' not in x and 'Unnamed' not in x else False, list(df.columns)))
    df = df[cols_to_keep]
    df.to_csv(out_fname, index=False)
    print(f"[{process_name}] Quitting.")
    return