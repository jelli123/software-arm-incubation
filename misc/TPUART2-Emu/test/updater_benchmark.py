#!/usr/bin/env python3
"""
Throughput of the Selfbus updater exchange through a KNX/IP interface and the TP-UART 2 emulator.

    test PC --KNXnet/IP tunnel--> KNX/IP interface ==KNX TP1== TPUART2-Emu --serial--> test PC
             plays the updater                                              plays the bootloader

Per cycle the tunnel side sends what the Selfbus updater (Calimero TL4) sends
for UPD_SEND_DATA: T_Data_Connected with A_UserMsg_Manufacturer_0, command
and 13 data octets (a 23 octet frame), waits for the T_ACK and the response
T_Data_Connected (A_UserMsg_Manufacturer_6, 2 octets, an 11 octet frame) and
acknowledges it. The serial side answers each data frame at once with T_ACK
and response, as the bootloader does.

The simulated bootloader is slower than a real one: its frames take the way
through the emulator's UART, and it can hand over the response only after
the L_Data.con of its T_ACK. --device-fast switches the emulator on the
serial side to its fastest rate without pacing, which shrinks that part.
--monitor reads the bus monitor of a Selfbus KNX/IP interface (ESP32) and
splits the cycle at its millisecond timestamps.

See README.md in this folder.
"""

import argparse
import json
import queue
import socket
import statistics
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request

import knx_function_test as k

UPD_SEND_DATA = 0x14          # command octet of the updater, value irrelevant for the timing
UPD_SEND_LAST_ERROR = 0x1C
APCI_USERMSG_MANUFACTURER_0 = 0x2F8
APCI_USERMSG_MANUFACTURER_6 = 0x2FE


def tpdu_kind(octet):
    """@return ("ack" | "data" | "connect" | "disconnect" | "other", sequence number)"""
    if octet & 0xC3 == 0xC2:
        return "ack", (octet >> 2) & 0x0F
    if octet & 0xC0 == 0x40:
        return "data", (octet >> 2) & 0x0F
    if octet == 0x80:
        return "connect", 0
    if octet == 0x81:
        return "disconnect", 0
    return "other", 0


def data_tpdu(seq, apci, data):
    return [0x40 | (seq << 2) | (apci >> 8), apci & 0xFF] + list(data)


def ack_tpdu(seq):
    return [0xC2 | (seq << 2)]


def cemi_individual_req(dst, tpdu):
    return bytes([k.L_DATA_REQ, 0x00, 0xBC, 0x60]) + struct.pack(">HH", 0, dst) \
        + bytes([len(tpdu) - 1]) + bytes(tpdu)


def tp_individual_frame(src, dst, tpdu):
    frame = bytes([0xBC]) + struct.pack(">HH", src, dst) + bytes([0x60 | (len(tpdu) - 1)]) + bytes(tpdu)
    return frame + bytes([k.checksum(frame)])


class EventTunnel(k.Tunnel):
    """Tunnel that hands every L_Data frame to a queue, with its arrival time."""

    def __init__(self, gateway):
        self.events = queue.Queue()
        super().__init__(gateway)

    def _receiver(self):
        while self.running:
            try:
                d = self.sock.recv(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            t = time.perf_counter()
            service = struct.unpack(">H", d[2:4])[0]
            body = d[6:]
            if service != k.TUNNELING_REQUEST:
                self.replies.put((service, body))
                continue
            self.sock.send(self._header(k.TUNNELING_ACK, bytes([4, body[1], body[2], 0])))
            if body[2] == self.rx_seq:
                continue
            self.rx_seq = body[2]
            self.events.put((t, body[4:]))

    def next_event(self, timeout):
        """@return (time, message code, fields) or None"""
        try:
            t, cemi = self.events.get(timeout=timeout)
        except queue.Empty:
            return None
        return t, cemi[0], k.cemi_fields(cemi)


class Bootloader(threading.Thread):
    """Answers every data frame to its address with T_ACK and an UPD response."""

    def __init__(self, tp, address):
        super().__init__(daemon=True)
        self.tp = tp
        self.address = address
        self.running = True
        self.reaction = []

    def run(self):
        seq = 0
        pending = None
        while self.running:
            for e in self.tp.poll(0.005):
                if e[0] == "con" and pending is not None:
                    # The emulator takes the next frame after the L_Data.con.
                    self.tp.send_frame(pending)
                    pending = None
                    continue
                if e[0] != "ind" or e[1][0] & 0x80 == 0:
                    continue
                frame = e[1]
                src, dst = struct.unpack(">HH", frame[1:5])
                if dst != self.address or frame[5] & 0x80:
                    continue
                kind, s = tpdu_kind(frame[6])
                if kind == "connect":
                    seq = 0
                elif kind == "data":
                    t = time.perf_counter()
                    self.tp.send_frame(tp_individual_frame(self.address, src, ack_tpdu(s)))
                    pending = tp_individual_frame(self.address, src,
                                                  data_tpdu(seq, APCI_USERMSG_MANUFACTURER_6,
                                                            [UPD_SEND_LAST_ERROR, 0x00]))
                    self.reaction.append(time.perf_counter() - t)
                    seq = (seq + 1) & 0x0F


class Monitor:
    """Bus monitor of the Selfbus KNX/IP interface, /api/monitor."""

    def __init__(self, host):
        self.base = f"http://{host}/api/monitor"

    def _post(self, path, fields=None):
        data = urllib.parse.urlencode(fields or {}).encode()
        urllib.request.urlopen(urllib.request.Request(self.base + path, data=data), timeout=5).read()

    def start(self):
        self._post("/clear")
        self._post("/start", {"sides": "tp,tunnel"})

    def stop_and_read(self):
        self._post("/stop")
        return json.loads(urllib.request.urlopen(self.base + "/frames?max=20000", timeout=10).read())


def monitor_breakdown(frames, peer, device):
    """
    Median of each step at the KNX/IP interface: t = 1 TP, 2 tunnel; o = 1 outgoing; c = 1 confirmation.
    A cycle starts with the data frame coming in from the tunnel.
    """
    frames = sorted((f for f in frames if {f["src"], f["dst"]} == {peer, device}), key=lambda f: f["s"])
    steps = {k_: [] for k_ in ("tp_out", "con", "dev_ack", "dev_resp", "pc_ack")}
    start = None
    seen = {}
    for f in frames:
        ack = f["a"] == "T_ACK"
        if f["t"] == 2 and f["o"] == 0 and not ack and f["src"] == peer:
            if start is not None and len(seen) == 5:
                for key, value in seen.items():
                    steps[key].append(value - start)
            start, seen = f["ms"], {}
        elif start is None:
            continue
        elif f["t"] == 1 and f["o"] == 1 and not ack and f["src"] == peer and f["c"] == 0:
            seen.setdefault("tp_out", f["ms"])
        elif f["t"] == 2 and f["o"] == 1 and f["c"] == 1 and not ack and f["src"] == peer:
            seen.setdefault("con", f["ms"])
        elif f["t"] == 1 and f["o"] == 0 and ack and f["src"] == device:
            seen.setdefault("dev_ack", f["ms"])
        elif f["t"] == 1 and f["o"] == 0 and not ack and f["src"] == device:
            seen.setdefault("dev_resp", f["ms"])
        elif f["t"] == 2 and f["o"] == 0 and ack and f["src"] == peer:
            seen.setdefault("pc_ack", f["ms"])
    return {key: statistics.median(v) for key, v in steps.items() if v}


def main():
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--port", default="/dev/ttyACM0", help="serial port of the emulator playing the bootloader")
    p.add_argument("--gateway", default="192.168.179.105", help="KNX/IP interface")
    p.add_argument("--gateway-port", type=int, default=3671)
    p.add_argument("--device", default="1.1.201", help="individual address of the simulated bootloader")
    p.add_argument("--cycles", type=int, default=100)
    p.add_argument("--payload", type=int, default=13, help="data octets per telegram, 13 as the updater")
    p.add_argument("--device-fast", action="store_true",
                   help="switch the serial side to its fastest rate without pacing (emulator 0.11+)")
    p.add_argument("--monitor", action="store_true", help="split the cycle with the bus monitor of the interface")
    p.add_argument("--boot", type=float, default=2.0, help="seconds to wait after opening the port")
    args = p.parse_args()

    device = k.parse_pa(args.device)
    tp = k.TpUart(args.port, args.boot)
    tp.write([k.U_RESET_REQ])
    tp.read_n(1)
    if args.device_fast:
        tp.write([k.U_EMU_IDENTIFY_REQ])
        r = tp.read_n(2)
        if len(r) == 2:
            r += tp.read_n(r[1] + 1)
        if len(r) >= 19 and r[2] >= 2 and r[16] & k.EMU_FEATURE_CONFIGURE:
            tp.configure(min(r[17], len(k.EMU_RATES) - 1), k.EMU_FLAG_BURST)
        else:
            print("Emulator ohne U_EmuConfigure, bleibt bei 19200 Baud")

    bootloader = Bootloader(tp, device)
    bootloader.start()
    tunnel = EventTunnel((args.gateway, args.gateway_port))
    peer = k.fmt_pa(tunnel.address)
    monitor = Monitor(args.gateway) if args.monitor else None
    print(f"Tunnel {peer} -> Bootloader {args.device} an {args.port} ({tp.s.baudrate} Baud), "
          f"{args.cycles} Zyklen à {args.payload} Byte")

    tunnel.send(cemi_individual_req(device, [0x80]))  # T_Connect
    time.sleep(0.5)
    while tunnel.next_event(0.01):
        pass
    if monitor:
        monitor.start()

    cycles = []
    seq = 0
    t_begin = time.perf_counter()
    for i in range(args.cycles):
        data = [UPD_SEND_DATA] + [(i + j) & 0xFF for j in range(args.payload)]
        t0 = time.perf_counter()
        if tunnel.send(cemi_individual_req(device, data_tpdu(seq, APCI_USERMSG_MANUFACTURER_0, data))) != 0:
            print(f"Zyklus {i}: keine Tunnel-Quittung")
            break
        t_tunnel_ack = time.perf_counter()
        got = {}
        while not {"con", "ack", "resp"} <= got.keys():
            e = tunnel.next_event(3.0)
            if e is None:
                break
            t, code, f = e
            kind, s = tpdu_kind(f["apdu"][0])
            if code == k.L_DATA_CON and f["dst"] == device and kind == "data":
                got.setdefault("con", t)
            elif code == k.L_DATA_IND and f["src"] == device:
                if kind == "ack" and s == seq:
                    got["ack"] = t
                elif kind == "data":
                    got["resp"] = t
                    got["resp_seq"] = s
        if not {"con", "ack", "resp"} <= got.keys():
            print(f"Zyklus {i}: Zeitüberschreitung, erhalten {sorted(got)}")
            break
        tunnel.send(cemi_individual_req(device, ack_tpdu(got["resp_seq"])))
        cycles.append(dict(t0=t0, tunnel_ack=t_tunnel_ack - t0, con=got["con"] - t0,
                           ack=got["ack"] - t0, resp=got["resp"] - t0, end=time.perf_counter() - t0))
        seq = (seq + 1) & 0x0F
    total = time.perf_counter() - t_begin

    frames = monitor.stop_and_read() if monitor else None
    tunnel.send(cemi_individual_req(device, [0x81]))  # T_Disconnect
    time.sleep(0.2)
    bootloader.running = False
    tunnel.close()
    bootloader.join(1)
    if args.device_fast:
        tp.set_baud(k.EMU_RATES[0])  # the emulator falls back on its own after 5 s
    tp.close()

    n = len(cycles)
    if n < 2:
        sys.exit("zu wenige Zyklen")
    period = [cycles[i + 1]["t0"] - cycles[i]["t0"] for i in range(n - 1)]

    def median_ms(key):
        return statistics.median(c[key] for c in cycles) * 1000

    print(f"\n{n} Zyklen in {total:.2f} s: {n * args.payload / total:.1f} Byte/s, Zyklus Median "
          f"{statistics.median(period) * 1000:.1f} ms (min {min(period) * 1000:.1f}, max {max(period) * 1000:.1f})")
    print("Am Test-PC, Median ab Senden des Datentelegramms:")
    print(f"  Tunnel-Quittung                {median_ms('tunnel_ack'):6.1f} ms")
    print(f"  L_Data.con des Datentelegramms {median_ms('con'):6.1f} ms")
    print(f"  T_ACK des Bootloaders          {median_ms('ack'):6.1f} ms")
    print(f"  Antwort des Bootloaders        {median_ms('resp'):6.1f} ms")
    print(f"  Zyklusende (eigenes T_ACK)     {median_ms('end'):6.1f} ms")
    print(f"  Reaktion der Simulation        {statistics.median(bootloader.reaction) * 1000:6.2f} ms")

    if frames is not None:
        b = monitor_breakdown(frames, peer, args.device)
        if b:
            print("Am KNX/IP-Interface (Busmonitor), Median ab Datentelegramm vom Tunnel:")
            labels = [("tp_out", "an den TP-UART übergeben"), ("con", "L_Data.con an den Tunnel"),
                      ("dev_ack", "T_ACK des Bootloaders vom TP"), ("dev_resp", "Antwort des Bootloaders vom TP"),
                      ("pc_ack", "T_ACK des Test-PC vom Tunnel")]
            for key, label in labels:
                if key in b:
                    print(f"  {label:32s} {b[key]:6.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
