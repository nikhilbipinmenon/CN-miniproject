from pox.core import core
import pox.openflow.libopenflow_01 as of

log = core.getLogger()

class SimpleController(object):
    def __init__(self, connection):
        self.connection = connection
        connection.addListeners(self)
        self.mac_to_port = {}

    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed:
            return

        src = packet.src
        dst = packet.dst
        in_port = event.port

        # Learn MAC address
        self.mac_to_port[src] = in_port
        log.info(f"Learned {src} on port {in_port}")

        # Decide output port
        if dst in self.mac_to_port:
            out_port = self.mac_to_port[dst]
        else:
            out_port = of.OFPP_FLOOD

        # Create flow rule
        msg = of.ofp_flow_mod()
        msg.match.dl_src = src
        msg.match.dl_dst = dst
        msg.actions.append(of.ofp_action_output(port=out_port))

        self.connection.send(msg)

        # Send packet out immediately
        packet_out = of.ofp_packet_out()
        packet_out.data = event.ofp
        packet_out.actions.append(of.ofp_action_output(port=out_port))
        self.connection.send(packet_out)

def launch():
    def start_switch(event):
        log.info("Controller connected to switch")
        SimpleController(event.connection)

    core.openflow.addListenerByName("ConnectionUp", start_switch)
