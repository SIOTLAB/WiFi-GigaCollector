# GigaCollector (GC)
GigaCollector is a real-time, temporally-coherent framework for Wi-Fi environment control, data collection, and ML inference. It collects data from many distinct data sources and collates them into a temporally-coherent data snapshot containing all data that occurred in or near a given moment in time.

# Quickstart
1. Clone the repository
2. Install Python 3.8+, create a virtual environment if desired
3. Install Python 3 requirements: `pip install pyzmq sortedcontainers pandas numpy simple_pid pyroute2 pyshark`
4. Run `python3 ec_sim_tester.py`
5. 5 Data Sources will be simulated with random data, collation requests will be generated sporadically to simulate packets of interest coming through a Wi-Fi sniffer interface for 5 seconds (request timestamp is set 1 second in the past), and a Snapshot Consumer simply writes to a `collated_data_{timestamp}.csv` file in the `./sim_data/` directory. Two experiments are performed, defined in the `list_of_expt_defs`.
6. Look at the freshly-generated `collated_data_{timestamp}.csv` files and examine the snapshots (rows) of all data from all data sources. All timestamps are included for latency analysis.

# Next Steps
1. **Add your own Data Sources** by examining and modifying the existing Data Source code prefixed with `ds_` (comments are included as a guide), or try the included real Data Sources (they do not include `sim` in the filename). Note that an ath9k Wi-Fi interface is needed for `ds_chan_stats.py` and `ds_hwq_stats.py` to function as they query driver-specific information over `ioctl` and `debugfs`.
    1. To see how to query driver information using ioctl > ethtool > pyroute2, take a look at `ds_chan_stats.py`. This DS queries ath9k channel time registers to calculate and publish channel utilization statistics. Speeds can reach around 1,000 data points/second on a single core of a 2013 Xeon.
    2. To efficiently query debugfs, take a look at `ds_hwq_stats.py`. This DS queries ath9k per-access category queue sizes and related properties from debugfs, parses it, then publishes it. Speeds can reach 90,000 data points/second on a single core of a 2013 Xeon processor.
    3. To wrap Python 3 standard library system information queries, take a look at `ds_load_avg_stats.py`. This DS packages 1-minute, 5-minute, and 15-minute averages of CPU load into the CSV/JSON hybrid standard ZeroMQ message format used by this reference implementation of GigaCollector.
    4. To optimize the number of messages the Collector/Collator needs to process, add logic inside your new DS to only publish data when noticeable changes have occurred in the data values. For example, a CPU load of 0.1111 and 0.1121 probably does not mean much, so set thresholds comparing last-published data and current measured data.
2. **Examine the Experiment Controller (EC) in `ec_sim_tester.py`.**
    1. The `list_of_expt_defs` list contains a set of dictionaries that define AEC configuration, such as the access categories, direction, target channel utilization, and packet sizes of adaptively-generated background traffic. Note that the AEC configuration is only shown for demonstration purposes only--to have properly-functioning traffic control, refer to item 3 below.
    2. The `gc_config` dictionary defines the configuration dictionaries that will be passed to each DS, CC, SC, or AEC instance. You can put whatever information you want in here for your custom DSs.
        1. Multiple CC instances can be run in parallel if higher throughput is needed. A reference point is about 13,000 total messages between DSs and collation requests on a 2013 Xeon processor. Add a new CC dictionary, call it "CC1" or whatever you'd like, and put an *exclusive, completely separate* set of `attached_dss` onto it (there is no point in duplicating data across the two instances). `event_sources` can be the same if the same events should trigger both (or more) CC instances.
        2. Multiple Snapshot Consumers (SCs) can be used in parallel. The included SC writes snapshots to a CSV file. For example, a PyTorch or TensorFlow SC could be implemented to predict a value based on the information in a snapshot.
    3. The last section at the bottom sends messages to control all the other subsystems of GigaCollector. New message types can be added as necessary; just follow the `message_source;publish_timestamp;message_type;optional_parameters` message format. Or, make your own message format if it will be more efficient for your use case.
3. **Run the Active Environment Control system:**
    1. Either an ath9k Wi-Fi interface used as the AP for hostapd must be installed and configured with `ds_chan_stats.py`, or
    2. A modified `ds_chan_stats.py` made to provide the same information from a different Wi-Fi interface model used as the AP Wi-Fi interface must be created.
    3. The AEC server currently does not exit cleanly, and as such requires a few Ctrl+C keyboard inputs in its terminal. It is recommended to run the AEC server separately.
    4. The Wi-Fi network should be on the 192.168.0.x subnet, and the Ethernet network between the AEC server and the AEC client should be on the 172.16.0.x subnet.
        1. Set the server to 192.168.0.4 on the Wi-Fi AP interface and 172.16.0.1 on the ACP Ethernet interface.
        2. Set the client to 192.168.0.15 on the Wi-Fi AP interface and 172.16.0.15 on the ACP Ethernet interface.
        3. These addresses can be changed in `aec_server_udp.py` and `aec_client_udp.py` if desired.
    5. From the `aec` directory, copy `aec_client_udp.py` and `traffic_gen_utils.py` to a second system, such as a Raspberry Pi.
    6. Start the AEC client on the second system and the AEC server on the primary/AP system.
    7. Start an EC, such as `ec_sim_tester.py`. The EC will send EXPT_DEFs and traffic start/stop messages to the AEC Server to control its traffic generation system.
    8. The AEC server and client do not currently exit cleanly, so a few Ctrl+C keyboard inputs are needed to exit. The exceptions encountered at exit time do not affect performance of the AEC system.
4. **For a brief overview of the architecture of GigaCollector, check out the guide below.**
5. **For a more in-depth look at the architecture and throughput/latency/AEC control results, consider checking out the MS thesis at {TBD}, and soon, a paper at {TBD}.**

# Details
GigaCollector consists of five main components:
1. Data Sources (DSs)
2. Collector/Collator (CC)
    1. Supporting data structures (TIML, SB BT)
3. Experiment Controller (EC)
4. Snapshot Consumers (SCs)
5. Active Environment Control (AEC)
    1. Server
    2. Clients

Comments explaining the specific implementation of each script is provided within each script.
## Data Sources (DSs)
Data Sources are applications or scripts that provide access to information of interest. Polling-type DSs publish information at a fixed rate, whereas event-type DSs publish information when a change is measured in the data. 

In this repository, Python scripts prefixed with `ds_` are Data Sources. To facilitate testing without the specific hardware some DSs were built for, simulation versions are provided as well, with the `sim` prefix.

`ds_chan_stats.py` provides Wi-Fi total, uplink, and downlink channel utilization values from an ath9k-based Wi-Fi interface. `ds_sim_chan_stats.py` random generates those values as a simulated demostration version.

`ds_eth_stats.py` provides Ethernet RX/TX packet count and bitrate. `ds_sim_eth_stats.py` provides randomly generated values.

`ds_hwq_stats.py` provides information about the per-access category queues of an ath9k device such as the number of packets waiting in each queue. `ds_sim_hwq_stats.py` randomly generates those values.

`ds_loadavg_stats.py` provides 1-minute, 5-minute, and 15-minute averages of CPU load on the local system.

`ds_sim_sniffer_event_gen.py` sporadically generates packets between 0.1-0.4 seconds apart to simulate packet capture by a Wi-Fi sniffer interface. These "events of interest" are used to trigger data collation for capture by a Snapshot Consumer (SC).

The text protocol used in the ZeroMQ messages of DSs in this implementation is as follows:

```data_source_name; publish_timestamp; message_type; json_data```

The `data_source_name` is used as part of the column name in the final collated snapshot (explained later in the CC section). `message_type` is `DATA` for DSs. The `json_data` body contains a `data_timestamp` field which allows for closest-timestamp searching regardless of the delivery delay between DSs and the CC. Latency analysis of the DS is possible when combined with the `publish_timestamp`--they are t_0 and t_1, respectively. To reduce unnecessary consumption of system resources, it is preferred that DSs only publish data when there is a change in the data.

An example of a DS is the Wi-Fi Channel Statistics module `ds_chan_stats.py`, which is intended to be located on an AP using a Wi-Fi interface compatible with the ath9k driver (AR9462 was tested). This DS accesses raw statistics used for calculating the total, uplink, and downlink channel utilization values over time.

## Collector/Collator (CC)
`cc_collector_collator.py` is the Collector/Collator. It subscribes to DSs and creates comprehensive data snapshots at given moment in time upon request for SCs to use. Internally, it uses one TIML or SortedDict data structure per DS to save collected data points. The architecture is shown in the diagram below:

<img src="./images/Collector+Collator Process Diagram.svg">

Then, when a collation request is received, it searches through each TIML/SortedDict to find the data point with the closest timestamp to that of the collation request and concatenates all of the information into one large JSON-formatted string--a "snapshot". TIMLs and SortedDicts are used because they provide fast insertion and search performance. TIMLs should be used when deletion is not necessary, as deletions take $O(n)$-time whereas SortedDicts take $O(log(n))$ time. Parallel instances can be used on exclusive sets of Data Sources to increase collection and collation performance:

<img src="./images/Collector+Collator Parallel Operation.svg">

When tested on a single core of a dual-Xeon E5-2690v3 workstation, it is able to process 13,000 messages combined between new data points and snapshot collation requests while retaining sub-millisecond latency, with an end-to-end (DS to SC) latency of around 600 microseconds and a CC latency of around 60-80 microseconds.

Collation calls follow a similar format as data source publications, where requester_name is substituted by the name of the application which sent the collation request:
```{requester_name}, request_publish_timestamp, COLLATE```
Or, if the requesting application has existing JSON_data to append to the collated data:
```{requester_name}, request_publish_timestamp, COLLATE_WITH, JSON_data```
The Collator waits for collation requests in the collate_queue. When a new request is received, the Collator records a collate_start_timestamp. It then searches through all of the TIMLs to find a temporally-coherent data set covering all data sources, joins them into one JSON string, and then records a collate_stop_timestamp. The result is published from a single ZeroMQ socket to any subscribed SCs.

Snapshots follow a similar format as that of data sources, where the requester_name field may be filtered by multiple parallel Snapshot Consumers to only use results that are relevant to them:
```{requester_name}, publish_timestamp, COLLATE_RESULT, JSON_data```

## Snapshot Consumers (SCs)
Snapshot Consumers use the collated snapshot from the CC. It can be a machine learning model predicting some target parameter, taking advantage of the real-time and low-latency capabilties of the DS to CC pipeline. Or, it may be as simple as a process that saves snapshots to a file. 

`sc_file_writer.py` is an example SC that first saves all snapshots from the CC in memory while running an experiment, then writes it to a CSV file at the end of that experiment.

## Active Environment Control (AEC)
Active Environment Control aims to control the Wi-Fi wireless environment using Proportional-Integral-Derivative (PID) controllers so that experimentation and data collection may be performed under various known conditions. This provides a more diverse and likely more statistically-sound dataset for model training purposes. In this reference implementation, uplink and downlink channel utilization values are controlled so that its effects on contention may be studied in future work. Its architecture is shown below:

<img src="./images/Active Environment Control System Diagram - Alternate Layout.svg">

AEC requires both the server and client components to be running properly to generate meaningful, controlled traffic. To control the client in real-time to generate uplink traffic, an Alternate Control Path (ACP) must be used. As shown in the diagram above, this implementation uses Ethernet, and as such an Ethernet connection between the server and the client must be established. The system configuration in the EC must be changed to match the specific systems AEC runs on. The AEC server is dependent on channel utilization metrics from the ath9k driver through the `ds_chan_stats.py` DS. If another Wi-Fi interface is used, the `ds_chan_stats.py` DS must be modified to provide total, uplink, and downlink channel utilization statistics in the same format.

`aec_server_udp.py` contains the server intended for use on the same system/AP as the rest of the data collection subsystems. `aec_client_udp.py` should run on a separate system connected to the Wi-Fi network hosted by the GC AP. These two scripts work together to adaptively control channel utilization by varying the rate of generated traffic with user-defined parameters.

## Experiment Controller (EC)
`ec_sim_tester.py` is an example EC that (1) configures and starts all DSs, the CC, the example SC, and the AEC server; sweeps through three experiments defined in Experiment Definition dictionaries with varying channel utilization values to be controlled by the AEC system; then (3) cleanly shuts down all components. For demonstration purposes, the AEC server may be used with the simulated Wi-Fi channel statistics data source and no clients. No meaningful traffic will be generated, but an essentially complete GC-based data collection system can be explored with (randomly) simulated data.

All configuration is performed inside the script in the `gc_config` dictionary. Each sub-dictionary is provided to the corresponding subsystem to be used during initialization. Explanations of each configuration item are explained in comments in either the DS script or the EC.

## ds_hostapd
ds_hostapd.py sets up a Wi-Fi network for experimentation, tested with hostapd 2.7. It provides Automatic Channel Selection (ACS) status and Wi-Fi station/client association, authentication, and disconnection signals by reading the contents of stdout. This may be used by an EC to determine when an experiment should start and when it should be paused due to unintended station disconnections. The ACS status may be used to automatically configure a sniffer Wi-Fi interface to use the correct channel.

As the sniffing functionality is outside the current scope of GigaCollector and the full setup currently requires specific hardware, it is provided as-is with comments in the code to guide its full integration in future work. An example `hostapd_config.conf` configuration file is provided as well.
