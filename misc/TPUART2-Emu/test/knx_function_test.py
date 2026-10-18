#!/usr/bin/env python3
"""
Functional test of the TP-UART 2 emulator on a real KNX TP1 line.

    test PC --serial 19200 8E1--> TPUART2-Emu ==KNX TP1== KNX/IP interface <--KNXnet/IP tunnel-- test PC

The script drives the emulator as a TP-UART host on one side and opens a
KNXnet/IP tunnel to any KNX/IP interface on the same line on the other, then
sends group telegrams both ways and compares them octet by octet.

See README.md in this folder for the setup and what each section checks.
"""

import argparse
import queue
import socket
import struct
import sys
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is missing: pip install pyserial")

# TP-UART host services, see ../src/tpuart_defs.h
U_RESET_REQ        = 0x01
U_STATE_REQ        = 0x02
U_SYSTEM_STATE_REQ = 0x0D
U_PRODUCT_ID_REQ   = 0x20
U_ACTIVATE_CRC     = 0x25
U_SET_ADDRESS_REQ  = 0x28
U_EMU_CONFIGURE_REQ = 0xFD
U_EMU_IDENTIFY_REQ = 0xFE

EMU_FEATURE_CONFIGURE = 0x01
EMU_FEATURE_SYSTEM_STATE = 0x04
EMU_FLAG_BURST     = 0x01
EMU_RATES          = [19200, 38400, 57600, 115200]  # rate codes of U_EmuConfigure

U_RESET_IND        = 0x03
U_STATE_IND        = 0x07
U_STATE_PE         = 0x10  # protocol error bit of U_State.ind
U_SYSTEM_STAT_IND  = 0x4B
SYSTEM_STATE_READY = 0xFB  # XTAL VFILT VBUS VDD2 V20V, no TW, mode normal
TPUART_PRODUCT_ID  = 0x41
L_DATA_CON_POS     = 0x8B
L_DATA_CON_NEG     = 0x0B

TP_MAX_FRAME       = 64    # TPUART_MAX_FRAME_SIZE of the emulator
LPDU_STD_OVERHEAD  = 8
LPDU_EXT_OVERHEAD  = 9

# KNXnet/IP services
CONNECT_REQUEST          = 0x0205
CONNECT_RESPONSE         = 0x0206
CONNECTIONSTATE_REQUEST  = 0x0207
CONNECTIONSTATE_RESPONSE = 0x0208
DISCONNECT_REQUEST       = 0x0209
DISCONNECT_RESPONSE      = 0x020A
TUNNELING_REQUEST        = 0x0420
TUNNELING_ACK            = 0x0421

L_DATA_REQ = 0x11
L_DATA_CON = 0x2E
L_DATA_IND = 0x29


def parse_pa(text):
    area, line, dev = (int(x) for x in text.split("."))
    return (area << 12) | (line << 8) | dev


def parse_ga(text):
    main, middle, sub = (int(x) for x in text.split("/"))
    return (main << 11) | (middle << 8) | sub


def fmt_pa(a):
    return f"{a >> 12}.{(a >> 8) & 15}.{a & 0xFF}"


def hexs(data):
    return " ".join(f"{x:02X}" for x in data)


def checksum(data):
    """TP1 frame checksum: all octets XOR, inverted."""
    x = 0
    for v in data:
        x ^= v
    return (~x) & 0xFF


def crc16(data, crc=0x1D0F):
    """CRC16-CCITT as appended after U_ActivateCRC (check value 0xE5CC)."""
    for v in data:
        crc ^= v << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


class Results:
    def __init__(self):
        self.items = []

    def check(self, name, ok, info=""):
        self.items.append((name, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({info})" if info else ""))

    def summary(self):
        passed = sum(1 for _, ok in self.items if ok)
        print(f"\n== Ergebnis: {passed}/{len(self.items)} bestanden ==")
        for name, ok in self.items:
            if not ok:
                print(f"  FAIL: {name}")
        return passed == len(self.items)


# --------------------------------------------------------------------------
# TP-UART host side
# --------------------------------------------------------------------------

class TpUart:
    def __init__(self, port, boot_s):
        s = serial.Serial()
        s.port = port
        s.baudrate = 19200
        s.bytesize = serial.EIGHTBITS
        s.parity = serial.PARITY_EVEN
        s.stopbits = serial.STOPBITS_ONE
        s.timeout = 0.02
        # A Flash Magic style adapter drives /RESET and PIO0_1 with DTR/RTS.
        # Both inactive means: run the application, not the ISP bootloader.
        s.dtr = False
        s.rts = False
        s.open()
        # Opening the port usually resets the LPC anyway; give it time to boot.
        time.sleep(boot_s)
        s.reset_input_buffer()
        self.s = s
        self.buf = bytearray()
        self.crc = False

    def close(self):
        self.s.close()

    def set_baud(self, baud):
        self.s.baudrate = baud

    def configure(self, rate, flags):
        """
        U_EmuConfigure. The answer comes at the old rate, then both sides switch.

        @return the answer octets; the port follows only on a valid answer
        """
        self.flush()
        self.write([U_EMU_CONFIGURE_REQ, rate, flags])
        r = self.read_n(4)
        if len(r) == 4 and r[:3] == bytes([U_EMU_CONFIGURE_REQ, rate, flags]) and r[3] == checksum(r[:3]):
            time.sleep(0.01)  # the emulator reopens its UART after the last octet
            self.set_baud(EMU_RATES[rate])
        return r

    def flush(self):
        time.sleep(0.05)
        self.s.reset_input_buffer()
        self.buf.clear()

    def write(self, data):
        self.s.write(bytes(data))

    def read_for(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.buf += self.s.read(256)
        out = bytes(self.buf)
        self.buf.clear()
        return out

    def read_n(self, n, seconds=0.5):
        end = time.time() + seconds
        while len(self.buf) < n and time.time() < end:
            self.buf += self.s.read(256)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def send_frame(self, frame):
        """L_Data.req as U_L_DataStart/Cont ... U_L_DataEnd, one octet each."""
        req = bytearray()
        for i, b in enumerate(frame):
            code = 0x40 if i == len(frame) - 1 else 0x80
            req += bytes([code | (i & 0x3F), b])
        self.write(req)

    def _fill(self, n, deadline):
        while len(self.buf) < n and time.time() < deadline:
            self.buf += self.s.read(256)
        return len(self.buf) >= n

    def poll(self, seconds):
        """
        Parse what the emulator sends for the given time.

        @return list of events: ("con", positive), ("reset",), ("state", code),
                ("ind", frame), ("trunc", octets), ("junk", octet)
        """
        events = []
        end = time.time() + seconds
        while True:
            self.buf += self.s.read(256)
            if not self.buf:
                if time.time() >= end:
                    break
                continue

            b = self.buf[0]
            if b in (L_DATA_CON_POS, L_DATA_CON_NEG):
                events.append(("con", b == L_DATA_CON_POS))
                del self.buf[0]
            elif b == U_RESET_IND:
                events.append(("reset",))
                del self.buf[0]
            elif (b & 0x07) == U_STATE_IND:
                events.append(("state", b))
                del self.buf[0]
            elif (b & 0xD3) in (0x90, 0x10):
                ext = (b & 0x80) == 0
                header = 7 if ext else 6
                if not self._fill(header, end + 0.5):
                    events.append(("trunc", bytes(self.buf)))
                    self.buf.clear()
                    break
                length = self.buf[6] if ext else (self.buf[5] & 0x0F)
                total = length + (LPDU_EXT_OVERHEAD if ext else LPDU_STD_OVERHEAD)
                total += 2 if self.crc else 0
                if not self._fill(total, end + 0.5):
                    events.append(("trunc", bytes(self.buf)))
                    self.buf.clear()
                    break
                events.append(("ind", bytes(self.buf[:total])))
                del self.buf[:total]
            else:
                events.append(("junk", b))
                del self.buf[0]

            if time.time() >= end and not self.buf:
                break
        return events


def tp_group_frame(src, dst, apdu, ext=False):
    """TP1 group frame, priority low, hop count 6. apdu starts with the TPCI octet."""
    length = len(apdu) - 1
    if ext:
        frame = bytes([0x3C, 0xE0]) + struct.pack(">HH", src, dst) + bytes([length])
    else:
        frame = bytes([0xBC]) + struct.pack(">HH", src, dst) + bytes([0xE0 | length])
    frame += bytes(apdu)
    return frame + bytes([checksum(frame)])


def tp_frame_fields(frame, crc):
    """@return (body without CRC, extended, source, destination, apdu)"""
    body = frame[:-2] if crc else frame
    ext = (body[0] & 0x80) == 0
    o = 1 if ext else 0
    src, dst = struct.unpack(">HH", body[1 + o:5 + o])
    apdu = body[(7 if ext else 6):-1]
    return body, ext, src, dst, apdu


def group_value_write(data=None, short=None):
    """A_GroupValue_Write. short = 6 bit value inside the APCI octet."""
    if short is not None:
        return bytes([0x00, 0x80 | (short & 0x3F)])
    return bytes([0x00, 0x80]) + bytes(data)


# --------------------------------------------------------------------------
# KNXnet/IP tunnel side
# --------------------------------------------------------------------------

class Tunnel:
    """
    Minimal KNXnet/IP tunnelling client (link layer, UDP).

    A background thread receives and acknowledges every TUNNELING_REQUEST at
    once, so a test waiting on the serial port never lets the server time out.
    """

    def __init__(self, gateway):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect(gateway)
        self.ip, self.port = self.sock.getsockname()
        self.sock.settimeout(0.05)
        self.replies = queue.Queue()
        self.running = True
        self.seq = 0
        self.rx_seq = None
        self.inds = []
        self.cons = []
        threading.Thread(target=self._receiver, daemon=True).start()

        body = self._hpai() + self._hpai() + bytes([4, 4, 2, 0])  # CRI: tunnel, link layer
        self.sock.send(self._header(CONNECT_REQUEST, body))
        r = self._wait(CONNECT_RESPONSE, 3)
        if r is None:
            raise RuntimeError("no CONNECT_RESPONSE")
        self.channel, status = r[0], r[1]
        if status != 0:
            raise RuntimeError(f"CONNECT_RESPONSE status 0x{status:02X}")
        self.address = struct.unpack(">H", r[-2:])[0]

    def _hpai(self):
        return struct.pack(">BB4sH", 8, 1, socket.inet_aton(self.ip), self.port)

    @staticmethod
    def _header(service, body):
        return struct.pack(">BBHH", 6, 0x10, service, 6 + len(body)) + body

    def _receiver(self):
        while self.running:
            try:
                d = self.sock.recv(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            service = struct.unpack(">H", d[2:4])[0]
            body = d[6:]
            if service != TUNNELING_REQUEST:
                self.replies.put((service, body))
                continue
            channel, seq = body[1], body[2]
            self.sock.send(self._header(TUNNELING_ACK, bytes([4, channel, seq, 0])))
            if seq == self.rx_seq:
                continue  # repetition
            self.rx_seq = seq
            cemi = body[4:]
            if cemi[0] == L_DATA_IND:
                self.inds.append((time.time(), cemi))
            elif cemi[0] == L_DATA_CON:
                self.cons.append((time.time(), cemi))

    def _wait(self, service, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                r = self.replies.get(timeout=max(0.001, end - time.time()))
            except queue.Empty:
                return None
            if r[0] == service:
                return r[1]
        return None

    def send(self, cemi):
        """@return status of the TUNNELING_ACK, None without one"""
        for _ in range(2):
            self.sock.send(self._header(TUNNELING_REQUEST, bytes([4, self.channel, self.seq, 0]) + cemi))
            r = self._wait(TUNNELING_ACK, 1.0)
            if r is not None and r[2] == self.seq:
                self.seq = (self.seq + 1) & 0xFF
                return r[3]
        self.seq = (self.seq + 1) & 0xFF
        return None

    def heartbeat(self):
        self.sock.send(self._header(CONNECTIONSTATE_REQUEST, bytes([self.channel, 0]) + self._hpai()))
        r = self._wait(CONNECTIONSTATE_RESPONSE, 2)
        return r is not None and r[1] == 0

    def close(self):
        self.sock.send(self._header(DISCONNECT_REQUEST, bytes([self.channel, 0]) + self._hpai()))
        self._wait(DISCONNECT_RESPONSE, 1)
        self.running = False


def cemi_group_req(dst, apdu, ext=False):
    ctrl1 = 0x3C if ext else 0xBC
    ctrl2 = 0x80 | 0x60  # group address, hop count 6
    return bytes([L_DATA_REQ, 0x00, ctrl1, ctrl2]) + struct.pack(">HH", 0, dst) \
        + bytes([len(apdu) - 1]) + bytes(apdu)


def cemi_fields(cemi):
    o = 2 + cemi[1]
    src, dst = struct.unpack(">HH", cemi[o + 2:o + 6])
    length = cemi[o + 6]
    return dict(ctrl1=cemi[o], src=src, dst=dst, apdu=bytes(cemi[o + 7:o + 8 + length]))


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

class FunctionTest:
    def __init__(self, args, tp, tunnel):
        self.args = args
        self.tp = tp
        self.tun = tunnel
        self.res = Results()
        self.src = parse_pa(args.source)
        self.ga = parse_ga(args.group)

    def host_services(self):
        tp, check = self.tp, self.res.check
        print("\n== 1. Host-Services ==")
        tp.flush()
        tp.write([U_RESET_REQ])
        r = tp.read_n(1)
        check("U_Reset.req -> U_Reset.ind 0x03", r == bytes([U_RESET_IND]), hexs(r))

        tp.write([U_STATE_REQ])
        r = tp.read_n(1)
        check("U_State.req -> U_State.ind", len(r) == 1 and (r[0] & 0x07) == U_STATE_IND, hexs(r))

        tp.write([U_PRODUCT_ID_REQ])
        r = tp.read_n(1)
        check("U_ProductID.req -> 0x41", r == bytes([TPUART_PRODUCT_ID]), hexs(r))

        tp.write([U_EMU_IDENTIFY_REQ])
        r = tp.read_n(2)
        if len(r) == 2 and r[0] == U_EMU_IDENTIFY_REQ:
            r += tp.read_n(r[1] + 1)
        ok = len(r) >= 17 and r[1] >= 14 and len(r) == r[1] + 3 and checksum(r[:-1]) == r[-1]
        info = hexs(r)
        if ok:
            info = (f"Format {r[2]}, Firmware {r[3]}.{r[4]:02d}, sblib {r[5]:02X}{r[6]:02X}, "
                    f"max. Frame {r[7]}, '{r[8:16].decode(errors='replace')}'")
            self.max_frame = r[7]
            if r[2] >= 2 and r[1] >= 16:
                self.features, self.max_rate = r[16], r[17]
                info += f", Features 0x{r[16]:02X}, max. Rate {EMU_RATES[r[17]] if r[17] < len(EMU_RATES) else r[17]}"
        check("U_EmuIdentify.req -> Antwort mit gültiger Prüfsumme", ok, info)

        tp.write([U_SYSTEM_STATE_REQ])
        if self.features & EMU_FEATURE_SYSTEM_STATE:
            r = tp.read_n(2)
            check("U_SystemState -> 0x4B, Bus und VCC ok, Mode normal",
                  r == bytes([U_SYSTEM_STAT_IND, SYSTEM_STATE_READY]), hexs(r))
        else:
            r = tp.read_n(1)
            check("U_SystemState (NCN) -> Protocol Error 0x17", r == bytes([U_STATE_IND | U_STATE_PE]), hexs(r))

        tp.write(bytes([U_SET_ADDRESS_REQ]) + struct.pack(">H", self.src))
        r = tp.read_for(0.2)
        check(f"U_SetAddress {self.args.source} ohne Antwort", r == b"", hexs(r))

        tp.write([U_STATE_REQ])
        r = tp.read_n(1)
        check("U_State.req danach -> 0x07 (keine Fehlerbits)", r == bytes([U_STATE_IND]), hexs(r))

    def emu_to_ip(self, name, dst, apdu, ext):
        tp, tun = self.tp, self.tun
        tun.inds.clear()
        tp.flush()
        frame = tp_group_frame(self.src, dst, apdu, ext)
        t0 = time.time()
        tp.send_frame(frame)
        events = tp.poll(0.6)
        time.sleep(0.4)

        cons = [e[1] for e in events if e[0] == "con"]
        got = [(t, cemi_fields(c)) for t, c in tun.inds]
        got = [(t, g) for t, g in got if g["dst"] == dst]
        ok_con = cons == [True]
        ok_ind = (len(got) == 1 and got[0][1]["apdu"] == bytes(apdu) and got[0][1]["src"] == self.src
                  and ((got[0][1]["ctrl1"] & 0x80) == 0) == ext)
        info = f"con={cons}, empfangen={len(got)}"
        if got:
            info += f", {(got[0][0] - t0) * 1000:.0f} ms"
            if not ok_ind:
                info += f", APDU={hexs(got[0][1]['apdu'])}"
        extra = [e for e in events if e[0] != "con"]
        if extra:
            info += f", außerdem={extra}"
        self.res.check(f"{name}: Emu -> IP ({len(frame)} Okt.)", ok_con and ok_ind, info)

    def ip_to_emu(self, name, dst, apdu, ext):
        tp, tun = self.tp, self.tun
        tun.cons.clear()
        tp.flush()
        status = tun.send(cemi_group_req(dst, apdu, ext))
        events = tp.poll(0.8)
        time.sleep(0.1)

        mine = [e[1] for e in events if e[0] == "ind" and tp_frame_fields(e[1], tp.crc)[3] == dst]
        ok = False
        info = f"ack={status}, empfangen={len(mine)}"
        if len(mine) == 1:
            frame = mine[0]
            body, f_ext, f_src, _, f_apdu = tp_frame_fields(frame, tp.crc)
            ok = (f_apdu == bytes(apdu) and checksum(body[:-1]) == body[-1]
                  and f_ext == ext and f_src == tun.address)
            if tp.crc:
                crc_ok = struct.unpack(">H", frame[-2:])[0] == crc16(body)
                ok = ok and crc_ok
                info += f", CRC {'ok' if crc_ok else 'FALSCH'}"
            info += f", Quelle {fmt_pa(f_src)}"
            if not ok:
                info += f", Frame={hexs(frame)}"

        confirmed = [cemi_fields(c) for _, c in tun.cons]
        confirmed = [c for c in confirmed if c["dst"] == dst]
        ok_con = len(confirmed) == 1 and (confirmed[0]["ctrl1"] & 0x01) == 0
        info += ", L_Data.con " + ("pos." if ok_con else str([hex(c["ctrl1"]) for c in confirmed]))
        extra = [e for e in events if e[0] != "ind"]
        if extra:
            info += f", außerdem={extra}"
        self.res.check(f"{name}: IP -> Emu", status == 0 and ok and ok_con, info)

    def host_rate(self):
        tp, check = self.tp, self.res.check
        print("\n== 8. Schnellere Rate (U_EmuConfigure) ==")
        if not self.features & EMU_FEATURE_CONFIGURE:
            print("  übersprungen: der Emulator meldet U_EmuConfigure nicht (vor 0.11)")
            return
        fast = min(self.max_rate, len(EMU_RATES) - 1)
        baud = EMU_RATES[fast]

        r = tp.configure(len(EMU_RATES), 0)
        check("ungültige Rate -> Protocol Error, Rate bleibt", r[:1] == bytes([U_STATE_IND | U_STATE_PE]), hexs(r))
        tp.write([U_STATE_REQ])
        r = tp.read_n(1)
        check("danach U_State.req bei 19200", r == bytes([U_STATE_IND]), hexs(r))

        r = tp.configure(fast, EMU_FLAG_BURST)
        check(f"Umschalten auf {baud} Baud ohne Drosselung", tp.s.baudrate == baud, hexs(r))
        tp.write([U_STATE_REQ])
        r = tp.read_n(1)
        check(f"U_State.req bei {baud}", r == bytes([U_STATE_IND]), hexs(r))
        tp.write([U_RESET_REQ])
        r = tp.read_n(1)
        tp.write([U_STATE_REQ])
        r += tp.read_n(1)
        check(f"U_Reset behält {baud} Baud", r == bytes([U_RESET_IND, U_STATE_IND]), hexs(r))
        tp.write(bytes([U_SET_ADDRESS_REQ]) + struct.pack(">H", self.src))

        self.ip_to_emu(f"{baud} Baud: 2 Byte", self.ga + 11, group_value_write([0x56, 0x78]), False)
        if not self.args.no_extended:
            ext_max = self.max_frame - LPDU_EXT_OVERHEAD - 1
            self.ip_to_emu(f"{baud} Baud: Extended max.", self.ga + 12,
                           group_value_write(bytes(range(ext_max))), True)
            self.emu_to_ip(f"{baud} Baud: Extended max.", self.ga + 13,
                           group_value_write(bytes(range(ext_max))), True)

        # A host that talks at 19200 again: framing errors, back to 19200.
        tp.set_baud(EMU_RATES[0])
        back = b""
        for _ in range(5):
            tp.flush()
            tp.write([U_RESET_REQ])
            back = tp.read_n(1, 0.3)
            if back == bytes([U_RESET_IND]):
                break
        check("Host bei 19200 -> Rückfall auf 19200 Baud", back == bytes([U_RESET_IND]), hexs(back))

        # A host that goes quiet: back to 19200 after 5 s.
        tp.configure(fast, EMU_FLAG_BURST)
        switched = tp.s.baudrate == baud
        time.sleep(6)
        tp.set_baud(EMU_RATES[0])
        tp.write([U_STATE_REQ])
        r = tp.read_n(1)
        check("5 s ohne Anfrage -> Rückfall auf 19200 Baud", switched and r == bytes([U_STATE_IND]), hexs(r))
        tp.write(bytes([U_SET_ADDRESS_REQ]) + struct.pack(">H", self.src))

    def run(self):
        tp, tun, check = self.tp, self.tun, self.res.check
        self.max_frame = TP_MAX_FRAME
        self.features = 0
        self.max_rate = 0
        self.host_services()

        print("\n== 2. Tunnel ==")
        check("KNXnet/IP CONNECT", True,
              f"Kanal {tun.channel}, Tunnel-PA {fmt_pa(tun.address)}, lokal {tun.ip}:{tun.port}")
        check("CONNECTIONSTATE", tun.heartbeat())
        time.sleep(0.5)
        tun.inds.clear()

        ext_max = self.max_frame - LPDU_EXT_OVERHEAD - 1  # data octets after TPCI/APCI
        ga = self.ga
        cases = [
            ("1 Bit (DPT 1)",                 ga + 1, group_value_write(short=1), False),
            ("1 Byte (DPT 5)",                ga + 2, group_value_write([0xA5]), False),
            ("2 Byte (DPT 9)",                ga + 3, group_value_write([0x0C, 0x1A]), False),
            ("14 Byte (DPT 16)",              ga + 4, group_value_write(b"Selfbus TPUART"), False),
            ("Extended 20 Byte",              ga + 5, group_value_write(bytes(range(20))), True),
            ("Extended 40 Byte",              ga + 6, group_value_write(bytes(range(40, 80))), True),
            (f"Extended max. ({ext_max} Byte)", ga + 7,
             group_value_write(bytes((i * 7) & 0xFF for i in range(ext_max))), True),
        ]
        if self.args.no_extended:
            cases = [c for c in cases if not c[3]]

        print("\n== 3. Emu -> Bus -> IP-Interface ==")
        for c in cases:
            self.emu_to_ip(*c)

        print("\n== 4. IP-Interface -> Bus -> Emu ==")
        for c in cases:
            self.ip_to_emu(*c)

        print(f"\n== 5. Frame länger als {self.max_frame} Oktette vom Host ==")
        tp.flush()
        frame = tp_group_frame(self.src, ga + 8, group_value_write(bytes(ext_max + 2)), True)
        tp.send_frame(frame)
        events = tp.poll(0.6)
        time.sleep(0.3)
        on_ip = [c for _, c in tun.inds if cemi_fields(c)["dst"] == ga + 8]
        check(f"{len(frame)}-Oktett-Frame abgewiesen (neg. con, nichts auf dem Bus)",
              ("con", False) in events and not on_ip, f"{events}, am Tunnel: {len(on_ip)}")
        tp.write([U_STATE_REQ])
        r = tp.read_n(1)
        check("U_State.req danach -> Antwort", len(r) == 1, hexs(r))

        print("\n== 6. CRC-Modus ==")
        tp.flush()
        tp.write([U_ACTIVATE_CRC])
        time.sleep(0.05)
        tp.crc = True
        self.ip_to_emu("CRC 2 Byte", ga + 9, group_value_write([0x12, 0x34]), False)
        if not self.args.no_extended:
            self.ip_to_emu("CRC Extended 30 Byte", ga + 10, group_value_write(bytes(range(30))), True)
        tp.write([U_RESET_REQ])
        r = tp.read_n(1)
        tp.crc = False
        check("U_Reset schaltet CRC ab", r == bytes([U_RESET_IND]), hexs(r))
        tp.write(bytes([U_SET_ADDRESS_REQ]) + struct.pack(">H", self.src))

        n = self.args.burst
        print(f"\n== 7. Last: {n} Telegramme je Richtung ==")
        tun.inds.clear()
        tp.flush()
        t0 = time.time()
        positive = 0
        for i in range(n):
            # Like knxd: the next frame only after the L_Data.con of the last.
            tp.send_frame(tp_group_frame(self.src, ga + 20, group_value_write([i & 0xFF])))
            end = time.time() + 1.0
            while time.time() < end:
                events = tp.poll(0.02)
                if ("con", True) in events:
                    positive += 1
                    break
                if ("con", False) in events:
                    break
        time.sleep(0.5)
        got = [cemi_fields(c)["apdu"][2] for _, c in tun.inds if cemi_fields(c)["dst"] == ga + 20]
        expected = [i & 0xFF for i in range(n)]
        check(f"Emu -> IP {n} Telegramme", got == expected and positive == n,
              f"{len(got)} empfangen, {positive} pos. con, {time.time() - t0:.1f} s")

        tp.flush()
        acks = [tun.send(cemi_group_req(ga + 21, group_value_write([i & 0xFF]))) for i in range(n)]
        events = tp.poll(3.0)
        values = [tp_frame_fields(e[1], False)[4][2] for e in events
                  if e[0] == "ind" and tp_frame_fields(e[1], False)[3] == ga + 21]
        check(f"IP -> Emu {n} Telegramme", values == expected and acks.count(0) == n,
              f"{len(values)} empfangen, Reihenfolge {'ok' if values == expected else values}, "
              f"Tunnel-Acks {acks.count(0)}/{n}")

        self.host_rate()
        return self.res.summary()


def main():
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--port", default="/dev/ttyACM0", help="serial port of the emulator")
    p.add_argument("--gateway", default="192.168.179.105", help="KNX/IP interface on the same line")
    p.add_argument("--gateway-port", type=int, default=3671)
    p.add_argument("--source", default="1.1.200", help="individual address the emulator sends with")
    p.add_argument("--group", default="15/7/0", help="first of the group addresses the test uses (+0 .. +21)")
    p.add_argument("--burst", type=int, default=30, help="telegrams per direction in section 7")
    p.add_argument("--boot", type=float, default=2.0, help="seconds to wait after opening the port")
    p.add_argument("--no-extended", action="store_true", help="skip extended frames")
    args = p.parse_args()

    tp = TpUart(args.port, args.boot)
    try:
        tunnel = Tunnel((args.gateway, args.gateway_port))
    except (RuntimeError, OSError) as e:
        tp.close()
        sys.exit(f"tunnel to {args.gateway}: {e}")
    try:
        ok = FunctionTest(args, tp, tunnel).run()
    finally:
        tunnel.close()
        tp.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
