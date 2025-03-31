import zmq
import time
from bisect import bisect_left
from sortedcontainers import SortedDict

# ds_utils.py: Data Source (DS) utilities

# Listen to control messages from the Experiment Controller (EC).
# process_name: specify name of data source for more informative debug info printing
# z_context: the ZeroMQ context of the data source to be shared with this control message listener
# z_ec_sub_addr: address of publish socket of the EC
# stop_event: threading (or multiprocessing.Manager).Event signal used to stop all modules within this DS
def control_listener(process_name, z_context, z_ec_sub_addr, stop_event):
    z_ec_sub = z_context.socket(zmq.SUB)
    z_ec_sub.setsockopt(zmq.RCVTIMEO, 100)
    z_ec_sub.connect(z_ec_sub_addr)
    z_ec_sub.subscribe('')
    print(f"[{process_name}] ZMQ EC subscribed: {z_ec_sub_addr}")

    while not stop_event.is_set():
        try:
            message = z_ec_sub.recv_string()
            if 'QUIT' in message: # When QUIT signal received from EC, send stop signal to all modules in this DS
                stop_event.set()
        except zmq.Again:
            pass

    z_ec_sub.close()
    print(f'[{process_name}] (control_listener) exiting cleanly...')

# Add new data to a list then resize it if the maximum list length limit was reached
# lst: list to be appended to/resized
# max_size: maximum size limit for the list
# new_data: new data to be appended
def append_and_resize_list(lst, max_size, new_data):
    lst.append(new_data)
    if len(lst) > max_size:
        del lst[0]
    return lst

# Exception thrown when no new updates to data are detected in each DS loop
class NoUpdateException(Exception):
    pass

# Collector/Collator Data Storage Interface class
class CCDSI:
    def __init__(self, ds, max_size):
        self.ds = ds
        self.MAX_SIZE = max_size

    def trim(self):
        pass

    def append(self, ts, data):
        pass

    def find_by_ts(self, ts):
        pass

# sortedcontainers SortedDict (SB BT) CCDSI implementation class
class SCSD(CCDSI):
    def __init__(self, ds, max_size):
        super().__init__(ds, max_size)
        self.sd = SortedDict()
        self.counter = 0

    def trim(self):
        if self.counter > self.MAX_SIZE:
            self.sd.popitem(index = 0)
            self.counter = len(self.sd)
    
    def append(self, ts, data):
        self.sd[ts] = data + get_add_col_str(f"{self.ds}_clctr_trim_ts", time.time_ns())

    def find_by_ts(self, ts):
        index = self.sd.bisect_left(ts) - 1
        index = max(0, index)
        key = self.sd.keys()[index]
        value = self.sd[key]
        print(index, key, value)
        return value


# Temporal Index-Matched List (TIML) CCDSI implementation class
class TIML(CCDSI):
    # ds: name of the data source for appending data storage timestamp
    # max_size: define maximum history size of each TIML instance
    def __init__(self, ds, max_size):
        super().__init__(ds, max_size)
        self.ts_list = []   # timestamp list
        self.data_list = [] # data list
        self.counter = 0

    # Resize TIML lists once maximum history limit is exceeded
    def trim(self):
        if self.counter > self.MAX_SIZE:
            del self.ts_list[0]
            del self.data_list[0]
            self.counter = len(self.ts_list)

    # Append new timestamped data to the TIML then add data storage timestamp
    def append(self, ts, data):
        self.ts_list.append(ts)
        self.data_list.append(data)
        self.trim()
        self.data_list[-1] += get_add_col_str(f"{self.ds}_clctr_trim_ts", time.time_ns())
        self.counter += 1

    # Find closest data point by timestamp in history. bisect_left() finds the latest data point recorded on or before the event timestamp
    def find_by_ts(self, ts):
        index = bisect_left(self.ts_list, ts) - 1
        index = max(0, index)
        return self.data_list[index]

    def find_by_ts_pm1(self, ts): # Get past, present, and "future" data; may fail because of index - 1 going negative if beginning of history and index + 1 being in the future.
        index = bisect_left(self.ts_list, ts) - 1
        # if index <= 0: print(f"[find_by_ts] {self.ds} idx = {index} for ts = {ts} -> delta: {time.time_ns() - ts}")
        index = max(0, index)
        return self.data_list[index-1], self.data_list[index], self.data_list[index + 1]

# Generate JSON-formatted string to append a generic data point/"column" of data
def get_add_col_str(col_name, col_value):
    if type(col_value) == str:
        return f', "{col_name}": "{col_value}"'
    else:
        return f', "{col_name}": {col_value}'

# Generate JSON-formatted string for a generic data point/"column" of data at the beginning of the data entry (no preceding comma)
def get_add_col_str_nocomma(col_name, col_value):
    if type(col_value) == str:
        return f'"{col_name}": "{col_value}"'
    else:
        return f'"{col_name}": {col_value}'

# AutotrimDict is inefficient older method
class AutotrimDict:
    def __init__(self, max_size):
        self.dict = {}
        self.MAX_SIZE = max_size
        self.counter = 0

    def trim(self):
        if self.counter > self.MAX_SIZE:
            self.dict.pop(next(iter(self.dict)))
            self.counter = len(self.dict)

    def append(self, k, v):
        self.dict[k] = v
        self.trim()
        self.counter += 1

    def get(self, k):
        return self.dict['k']

    def keys(self):
        return self.dict.keys()