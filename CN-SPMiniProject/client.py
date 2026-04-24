import ssl
import socket
import os
import threading
import queue
import time
import struct
import hashlib

SERVER_NAME = 'localhost'
SERVER_PORT = 12000

BUFFER_CAPACITY    = 32
BUFFER_LOW_WATERMARK = 4

TYPE_DATA = 0x01
TYPE_EOF  = 0x02
TYPE_ADJ  = 0x03
TYPE_ACK  = 0x10
TYPE_NAK  = 0x11
HEADER    = '!BII'
HEADER_LEN = struct.calcsize(HEADER)  # 9 bytes

chunk_buffer: queue.Queue = queue.Queue(maxsize=BUFFER_CAPACITY)


def make_reply(ftype, seq):
    return struct.pack(HEADER, ftype, seq, 0)


def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        part = sock.recv(n - len(buf))
        if not part:
            raise ConnectionError("Socket closed")
        buf += part
    return buf


class ClientQoS:
    def __init__(self):
        self.start_time = None
        self.total_bytes = 0
        self.chunks_received = 0
        self.nak_sent = 0
        self.buffer_underruns = 0
        self.chunk_latencies = []

    def start(self):
        self.start_time = time.time()

    def record(self, size, latency_ms):
        self.total_bytes += size
        self.chunks_received += 1
        self.chunk_latencies.append(latency_ms)

    def record_nak(self):
        self.nak_sent += 1

    def record_underrun(self):
        self.buffer_underruns += 1

    def report(self):
        elapsed = max(time.time() - self.start_time, 0.001) if self.start_time else 0.001
        throughput = (self.total_bytes / 1024) / elapsed
        avg_lat = (sum(self.chunk_latencies) / len(self.chunk_latencies)) if self.chunk_latencies else 0
        print("\n[QoS Client Report]")
        print(f"  Received    : {self.total_bytes/1024:.2f} KB")
        print(f"  Throughput  : {throughput:.2f} KB/s")
        print(f"  Avg latency : {avg_lat:.2f} ms/chunk")
        print(f"  Chunks      : {self.chunks_received}")
        print(f"  NAKs sent   : {self.nak_sent}")
        print(f"  Underruns   : {self.buffer_underruns}")
        print(f"  Time        : {elapsed:.2f}s\n")

def write_chunks(output_path, qos):
    import time
    chunk_count = 0 
    
    with open(output_path, "wb") as f:
        while True:
            try:
                chunk = chunk_buffer.get(timeout=5)
            except queue.Empty:
                qos.record_underrun()
                print("  [BUFFER] Underrun — waiting for data")
                continue

            if chunk is None:  
                break

            f.write(chunk)
            chunk_count += 1
            if chunk_count % 1000 == 0:
                print(f"  [SIMULATION] Consumer sleeping to trigger backpressure...")
                time.sleep(1.0) # Increased sleep to make it noticeable

def receive_chunks(sock, qos):
    low_watermark = False 
    while True:
        ftype, seq, payload, good = recv_frame(sock)

        if ftype == TYPE_EOF:
            chunk_buffer.put(None)
            break

        if ftype == TYPE_ADJ:
            new_size = struct.unpack('!I', payload)[0]
            print(f"  [ADAPTIVE] Server set chunk size to {new_size} bytes")
            sock.sendall(make_reply(TYPE_ACK, 0))
            continue

        if ftype == TYPE_DATA:
            t0 = time.time()
            if not good:
                sock.sendall(make_reply(TYPE_NAK, seq))
                qos.record_nak()
                print(f"  [CHECKSUM FAIL] seq={seq} NAK sent")
                continue

            sock.sendall(make_reply(TYPE_ACK, seq))
            qos.record(len(payload), (time.time() - t0) * 1000)

            buffer_put_with_backpressure(chunk_buffer, payload)

            if chunk_buffer.qsize() < BUFFER_LOW_WATERMARK and not low_watermark:
                low_watermark = True
                print(f"  [BUFFER] Low watermark ({chunk_buffer.qsize()}/{BUFFER_CAPACITY})")

def buffer_put_with_backpressure(buffer, payload):
    import time

    if buffer.full():
        print(f"  [BACKPRESSURE] Buffer full ({buffer.qsize()})")

    while buffer.full():
        time.sleep(2.0)

    buffer.put(payload)


def recv_frame(sock):
    ftype, seq, plen = struct.unpack(HEADER, recv_exact(sock, HEADER_LEN))
    payload  = recv_exact(sock, plen) if plen else b''
    checksum = recv_exact(sock, 16)
    good = (checksum == hashlib.md5(payload).digest())
    return ftype, seq, payload, good


def receive_song(sock, song_name, qos):
    global chunk_buffer
    chunk_buffer = queue.Queue(maxsize=BUFFER_CAPACITY)
    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "received_" + song_name)
    writer = threading.Thread(target=write_chunks, args=(output_file, qos), daemon=True)
    writer.start()
    receive_chunks(sock, qos)
    writer.join()
    print(f"[DONE] Saved: {output_file}")
    qos.report()


def start_client():
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    with context.wrap_socket(raw_socket, server_hostname=SERVER_NAME) as sock:
        sock.connect((SERVER_NAME, SERVER_PORT))
        print("Connected to music server.\n")
        qos = ClientQoS()

        while True:
            print("Commands:\n  1. LIST\n  2. PLAY <song_name>\n  3. QUIT")
            command = input("Enter command: ").strip()
            sock.send(command.encode())

            if command == "LIST":
                print("\nAvailable Songs:\n" + sock.recv(4096).decode())

            elif command.startswith("PLAY "):
                response = sock.recv(1024).decode().strip()
                if response == "OK":
                    sock.send("READY".encode())
                    qos = ClientQoS()
                    qos.start()
                    receive_song(sock, command[5:].strip(), qos)
                else:
                    print("Server:", response)

            elif command == "QUIT":
                print("Server:", sock.recv(1024).decode())
                break
            else:
                print("Server:", sock.recv(1024).decode())


if __name__ == "__main__":
    start_client()