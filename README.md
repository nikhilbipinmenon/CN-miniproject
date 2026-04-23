This project demonstrates Software Defined Networking (SDN) using Mininet and a POX controller. It shows how network behavior can be controlled using OpenFlow rules and how bandwidth is affected.

Start Controller
cd pox
python3 pox.py controller
Start Mininet
sudo mn --topo single,2 --mac --switch ovsk --controller remote

Scenario 1: Normal Network
pingall → Successful (0% loss)
iperf → High bandwidth

Before starting scenario 2, do the following command sh ovs-ofctl del-flows s1 since the flow rules made by the controller takes priority over manually added rules
Scenario 2: Blocked Traffic
sh ovs-ofctl add-flow s1 priority=100,actions=drop
pingall → Fails
iperf → No connection

Mininet
POX Controller
iperf (throughput)
ping (latency)

Flow table using:
sh ovs-ofctl dump-flows s1
ping results
iperf results

The project shows how SDN enables centralized control of network behavior and how flow rules can dynamically allow or block traffic.

