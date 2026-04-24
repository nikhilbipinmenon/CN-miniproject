import os
import ssl
import socket
import threading
import time
import struct
import hashlib
import random

SERVER_HOST = '0.0.0.0'
SERVER_PORT = 12000
SONGS_FOLDER = 'songs'
CHUNK_SIZES = [512, 1024, 2048, 4096]
DEFAULT_CHUNK_IDX = 3

# Frame layout: [1B type][4B seq][4B payload_len][payload][16B MD5]
# ACK/NAK reply: [1B type][4B seq][4B 0]  (no payload, no MD5)
TYPE_DATA = 0x01
TYPE_EOF  = 0x02
TYPE_ADJ  = 0x03
TYPE_ACK  = 0x10
TYPE_NAK  = 0x11
HEADER    = '!BII'
HEADER_LEN = struct.calcsize(HEADER)  # 9 bytes


def make_frame(ftype, seq, payload=b''):
    header = struct.pack(HEADER, ftype, seq, len(payload))
    return header + payload + hashlib.md5(payload).digest()


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


def recv_frame(sock):
    ftype, seq, plen = struct.unpack(HEADER, recv_exact(sock, HEADER_LEN))
    payload  = recv_exact(sock, plen) if plen else b''
    checksum = recv_exact(sock, 16)
    good = (checksum == hashlib.md5(payload).digest())
    return ftype, seq, payload, good


class QoSTracker:
    def __init__(self, addr):
        self.addr = addr
        self.start_time = time.time()
        self.total_bytes = 0
        self.total_chunks = 0
        self.retransmissions = 0
        self.chunk_times = []

    def record_chunk(self, size, elapsed):
        self.total_bytes += size
        self.total_chunks += 1
        self.chunk_times.append(elapsed)

    def record_retransmit(self):
        self.retransmissions += 1

    def report(self):
        elapsed = max(time.time() - self.start_time, 0.001)
        throughput = (self.total_bytes / 1024) / elapsed
        avg_ms = (sum(self.chunk_times) / len(self.chunk_times) * 1000) if self.chunk_times else 0
        total_tx = self.total_chunks + self.retransmissions
        loss = (self.retransmissions / total_tx * 100) if total_tx else 0
        print(f"\n[QoS] {self.addr}")
        print(f"  Sent        : {self.total_bytes/1024:.2f} KB")
        print(f"  Throughput  : {throughput:.2f} KB/s")
        print(f"  Avg latency : {avg_ms:.2f} ms/chunk")
        print(f"  Retransmits : {self.retransmissions}  |  Loss: {loss:.1f}%\n")


def send_chunk_reliable(conn, seq, data, qos, max_retries=5):
    frame = make_frame(TYPE_DATA, seq, data)
    for attempt in range(max_retries):
        t0 = time.time()
        if attempt == 0 and random.random() < 0.005:
            corrupt = frame[:-16] + bytes(16)  # replace MD5 with zeros
            conn.sendall(corrupt)
        else:
            conn.sendall(frame)
        try:
            conn.settimeout(3.0)
            raw = recv_exact(conn, HEADER_LEN)
            conn.settimeout(None)
        except socket.timeout:
            conn.settimeout(None)
            print(f"  [TIMEOUT] seq={seq} attempt={attempt+1}")
            qos.record_retransmit()
            continue
        ftype_r, seq_r, _ = struct.unpack(HEADER, raw)
        if ftype_r == TYPE_ACK and seq_r == seq:
            qos.record_chunk(len(data), time.time() - t0)
            return True
        print(f"  [NAK] seq={seq} attempt={attempt+1}")
        qos.record_retransmit()
    print(f"  [FAIL] seq={seq} dropped after {max_retries} attempts")
    return False


def negotiate_chunk_size(conn, current_idx, throughput_kbps):
    new_idx = current_idx
    if throughput_kbps > 200 and current_idx < len(CHUNK_SIZES) - 1:
        new_idx = current_idx + 1
    elif throughput_kbps < 50 and current_idx > 0:
        new_idx = current_idx - 1
    if new_idx == current_idx:
        return current_idx
    payload = struct.pack('!I', CHUNK_SIZES[new_idx])
    conn.sendall(make_frame(TYPE_ADJ, 0, payload))
    try:
        conn.settimeout(2.0)
        raw = recv_exact(conn, HEADER_LEN)
        conn.settimeout(None)
        ftype_r, _, _ = struct.unpack(HEADER, raw)
        if ftype_r == TYPE_ACK:
            print(f"  [ADAPTIVE] {CHUNK_SIZES[current_idx]} -> {CHUNK_SIZES[new_idx]} bytes")
            return new_idx
    except socket.timeout:
        conn.settimeout(None)
    return current_idx


def get_song_list():
    if not os.path.exists(SONGS_FOLDER):
        os.makedirs(SONGS_FOLDER)
    return [f for f in os.listdir(SONGS_FOLDER) if os.path.isfile(os.path.join(SONGS_FOLDER, f))]


def handle_client(conn, addr):
    print(f"[NEW CONNECTION] {addr}")
    qos = QoSTracker(addr)
    try:
        while True:
            data = conn.recv(1024).decode().strip()
            if not data:
                break
            print(f"[REQUEST] {addr}: {data}")

            if data == "LIST":
                songs = get_song_list()
                conn.send(("\n".join(songs) if songs else "No songs available.").encode())

            elif data.startswith("PLAY "):
                song_name = data[5:].strip()
                song_path = os.path.join(SONGS_FOLDER, song_name)
                if not os.path.exists(song_path):
                    conn.send("SONG_NOT_FOUND".encode())
                    continue
                conn.send("OK".encode())
                ack = conn.recv(1024).decode().strip()
                if ack != "READY":
                    continue

                chunk_idx = DEFAULT_CHUNK_IDX
                seq = 0
                sent_bytes = 0
                window_bytes = 0
                window_start = time.time()
                print(f"[STREAM] '{song_name}' ({os.path.getsize(song_path)} bytes)")

                with open(song_path, "rb") as f:
                    while True:
                        chunk = f.read(CHUNK_SIZES[chunk_idx])
                        if not chunk:
                            break
                        send_chunk_reliable(conn, seq, chunk, qos)
                        sent_bytes += len(chunk)
                        window_bytes += len(chunk)
                        seq += 1
                        if seq % 10 == 0:
                            elapsed = time.time() - window_start
                            kbps = (window_bytes / 1024) / elapsed if elapsed > 0 else 0
                            chunk_idx = negotiate_chunk_size(conn, chunk_idx, kbps)
                            window_bytes = 0
                            window_start = time.time()

                conn.sendall(make_frame(TYPE_EOF, 0))
                print(f"[DONE] '{song_name}' — {sent_bytes} bytes, {seq} chunks")
                qos.report()

            elif data == "QUIT":
                conn.send("Goodbye".encode())
                break
            else:
                conn.send("INVALID_COMMAND".encode())

    except Exception as e:
        print(f"[ERROR] {addr}: {e}")
    finally:
        conn.close()
        print(f"[DISCONNECTED] {addr}")
        qos.report()


def start_server():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile="server.crt", keyfile="server.key")
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((SERVER_HOST, SERVER_PORT))
    server_socket.listen(5)
    print(f"[STARTED] Server on port {SERVER_PORT}")
    with context.wrap_socket(server_socket, server_side=True) as secure_server:
        while True:
            conn, addr = secure_server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
            print(f"[ACTIVE] {threading.active_count() - 1} connections")


if __name__ == "__main__":
    start_server()