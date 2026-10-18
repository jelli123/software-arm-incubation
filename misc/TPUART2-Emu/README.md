# TP-UART 2 Emulator für das Selfbus SB-Interface (LPC1115)

Macht aus einem Selfbus SB-Interface (LPC1115) einen KNX-TP1-Transceiver, der auf
seiner UART das **Host-Protokoll eines TP-UART 2** spricht. Damit lässt er
sich von beliebiger TP-UART-fähiger Software ansteuern:

* **knxd** – Treiber `tpuart`

* ETS über knxd, Calimero, u. a.

Die KNX-Seite (Bit-Timing, Kollisionsauflösung, ACK, Wiederholungen) übernimmt die
[Selfbus-Bibliothek](https://github.com/selfbus/software-arm-lib) (`sblib`).

---

## Hardware

| SB-Interface (LPC1115)    | Host |
| ------------------------- | ---- |
| `PIN_TX` (Default PIO1_7) | RX   |
| `PIN_RX` (Default PIO1_6) | TX   |
| `PIN_LPC_READY` (Default PIO2_6) | GPIO, optional, siehe *Busspannung* |
| GND                       | GND  |

| SB-Interface (LPC1115)       | STKNX    |
| ---------------------------- | -------- |
| `PIN_STKNX_VCC_OK` (Default PIO2_7)  | `VCC_OK` |
| `PIN_STKNX_KNX_OK` (Default PIO1_11) | `KNX_OK` |

Pegel: 3,3 V TTL. Pins sind in [src/config.h](src/config.h) änderbar.

Host-seitig: **19200 Baud, 8 Datenbits, gerade Parität, 1 Stoppbit (8E1)**.

---

## Build

Das Projekt ist eine reguläre sblib-Anwendung und wird wie die übrigen
Selfbus-Apps mit MCUXpresso gebaut:

1. `software-arm-incubation` klonen (inkl. Submodul `software-arm-lib`).
2. `misc/TPUART2-Emu` klonen.
3. Build-Symbole: identisch zu ft12, zusätzlich nichts erforderlich.`BUSMONITOR` darf **nicht** gesetzt sein.

---

## Test

[test/knx_function_test.py](test/knx_function_test.py) prüft den Emulator an
einer echten Linie gegen ein KNX/IP-Interface: Host-Services, Standard- und
Extended Frames in beide Richtungen, CRC-Modus und Last. Aufbau und Aufruf
stehen in [test/README.md](test/README.md).

---

## Unterstützte Services

### Host → Transceiver

| Code                   | Service                               | Verhalten                                |
| ---------------------- | ------------------------------------- | ---------------------------------------- |
| `0x01`                 | `U_Reset.req`                         | Reset, antwortet mit `0x03`              |
| `0x02`                 | `U_State.req`                         | antwortet mit `U_State.ind`              |
| `0x03` / `0x04`        | `U_SetBusy` / `U_QuitBusy` (NCN)      | ACK auf dem Bus aus/ein                  |
| `0x05`                 | `U_ActivateBusmon`                    | Busmonitor, ACK aus (Ende nur per Reset) |
| `0x08`–`0x0C`          | `U_L_DataOffset` (NCN)                | Offset für Frames > 64 Byte              |
| `0x0D`                 | `U_SystemState.req` (NCN)             | antwortet mit `U_SystemStat.ind`, siehe *Busspannung* |
| `0x0E` / `0x0F`        | `U_StopMode` / `U_ExitStopMode` (NCN) | `bus.pause()` / `bus.resume()`           |
| `0x10`–`0x17`          | `U_AckInformation`                    | konsumiert, siehe *ACK-Verhalten*; beendet den Busy-Modus |
| `0x20`                 | `U_ProductID.req`                     | antwortet mit `0x41` (TP-UART 2, Release a) |
| `0x21` / `0x22`        | `U_ActivateBusyMode` / `U_ResetBusyMode` | 700 ms kein ACK, siehe *Busy-Modus*   |
| `0x24` + 1 Byte        | `U_MxRstCnt`                          | Wiederholungszähler, wird konsumiert     |
| `0x25`                 | `U_ActivateCRC`                       | CRC16 an jedes `L_Data.ind` (nicht im Busmonitor) |
| `0x28` + 2 Byte        | `U_SetAddress` (knxd)                 | setzt die eigene IA                      |
| `0xF1` + 2 Byte        | `U_SetAddress` (NCN, OpenKNX)         | setzt die eigene IA                      |
| `0x40`–`0x7F` + 1 Byte | `U_L_DataEnd`                         | letztes Frame-Oktett, löst Senden aus    |
| `0x80`–`0xBF` + 1 Byte | `U_L_DataStart/Cont`                  | Frame-Oktett n                           |
| `0xE0`–`0xEE` + 3 Byte | `U_PollingState`                      | konsumiert, `sblib` kennt keine Poll-Frames |
| `0xFD` + 2 Byte        | `U_EmuConfigure.req` (Selfbus)        | Rate und Drosselung, siehe *Schnellere Rate* |
| `0xFE`                 | `U_EmuIdentify.req` (Selfbus)         | antwortet mit Version, siehe *Erkennung* |

Mit *(NCN)* markierte Services gibt es beim TP-UART 2 nicht, nur beim
NCN5120/5130. Der OpenKNX-Stack sendet sie aber auch dann, wenn er ohne
`NCN5120` für einen TP-UART gebaut ist (`requestBusy()`, `stop()`,
`requestConfig()`). Der Emulator versteht sie deshalb zusätzlich.

Alle anderen Codes beantwortet der Emulator wie ein TP-UART 2 mit einer
`U_State.ind` mit gesetztem Protocol-Error-Bit (`0x17`). Das gilt auch für die
übrigen NCN-Services `U_Configure`, `U_IntRegRd/Wr`
und `U_SetRepetition`. Ihre Datenbytes werden dabei verbraucht, damit der
Parser synchron bleibt.

### Erkennung

Auf `U_ProductID.req` antwortet der Emulator wie ein echter TP-UART 2 mit
`0x41`. Daran allein kann ein Host ihn also nicht vom Original unterscheiden.
Dafür gibt es den herstellerspezifischen Service `U_EmuIdentify.req` (`0xFE`),
den weder TP-UART 2 noch NCN512x kennen – beide antworten mit einem
Protocol-Error (`0x17`). Der Emulator antwortet am Stück, ohne dass ein
Telegramm vom Bus dazwischenkommt:

| Oktett | Inhalt                                                    |
| ------ | --------------------------------------------------------- |
| 0      | `0xFE`                                                    |
| 1      | Anzahl der Oktette 2 … n-2, derzeit `16`                  |
| 2      | Format der Antwort, derzeit `2`                           |
| 3, 4   | Firmware-Version Major, Minor (z. B. `0`, `10` = 0.10)    |
| 5, 6   | `SBLIB_VERSION`, High- und Low-Byte (z. B. `0x02 0x11`)   |
| 7      | längster Frame inkl. Prüfsumme (`64`)                     |
| 8 … 15 | Anwendungsname, 8 ASCII-Zeichen (`SBtpuart`)              |
| 16     | Features (ab Format 2): Bit 0 = `U_EmuConfigure`, Bit 1 = `/LPC_READY` wird getrieben, Bit 2 = `U_SystemState` (ab 0.12) |
| 17     | höchster Raten-Code von `U_EmuConfigure` (ab Format 2), `3` |
| n-1    | Prüfsumme: alle Oktette davor XOR, dann invertiert        |

Ein Host muss eine längere Antwort eines späteren Formats annehmen und die
Oktette überspringen, die er nicht kennt. Format 1 (Version 0.10) endet nach
Oktett 15. Die Version steht an einer Stelle, in
[src/emu_version.h](src/emu_version.h).

### Schnellere Rate

Ein TP-UART 2 spricht fest 19200 Baud, und jedes Frame-Oktett braucht vom Host
zwei UART-Oktette. Ein Datentelegramm mit 23 Oktetten liegt damit 26 ms auf der
UART, bevor es auf den Bus gehen kann. Beim Selfbus Updater ist das rund ein
Drittel jedes Zyklus aus Anfrage und Antwort. Ab Version 0.11 kann der Host den
Emulator deshalb umschalten:

```
Host  → 0xFD  Rate  Flags
Emu   ← 0xFD  Rate  Flags  Prüfsumme      (noch mit der alten Rate)
```

| Rate | Baud   |
| ---- | ------ |
| `0`  | 19200 (Vorgabe nach dem Einschalten) |
| `1`  | 38400  |
| `2`  | 57600  |
| `3`  | 115200 |

Flag Bit 0 schaltet die Drosselung ab (siehe Abweichung 6). Der Emulator
antwortet mit der alten Rate, nach allem, was schon in seiner Warteschlange
lag, und schaltet um, sobald die Antwort die UART verlassen hat. Der Host
schaltet um, wenn er die Antwort hat, und sendet bis dahin nichts. Ungültige
Werte beantwortet der Emulator mit einem Protocol-Error (`0x17`), es ändert
sich dann nichts.

`U_Reset.req` behält Rate und Drosselung, damit der Reset-Handshake des Hosts
die Verbindung nicht trennt. Zurück auf 19200 Baud mit Drosselung geht es

* bei einem Rahmen-, Paritäts- oder Break-Fehler. Genau das erzeugt ein Host,
  der nach einem Neustart wieder mit 19200 Baud anfragt, schon mit dem ersten
  Oktett.
* nach 5 s ohne ein einziges Oktett vom Host (`TPUART_EMU_RATE_IDLE_MS`).

Der Dienst ist nur zu senden, wenn `U_EmuIdentify` Feature-Bit 0 meldet. Ein
älterer Emulator und ein echter TP-UART würden die beiden Datenoktette als
eigene Dienste auswerten.

Mit `U_ActivateCRC` hängt der Emulator an jedes empfangene Telegramm eine
CRC16-CCITT über das komplette Telegramm inklusive Prüfsumme an (High-Byte
zuerst, Prüfwert für „123456789“: `0xE5CC`). Ein Reset schaltet sie wieder ab.

`U_State.req` und `U_SetAddress` sind **Pflicht**:
Der OpenKNX-Stack sendet beide sekündlich (`requestState()`/`requestConfig()`) und erklärt die Verbindung nach 5 s ohne Antwort für tot – ab dann verwirft er jedes
`L_Data.req` stillschweigend.
Knxd nutzt `U_State.req` als 10-s-Keepalive.

### Busspannung

Ab Version 0.12 wertet der Emulator die beiden Power-good-Ausgänge des STKNX
aus (CMOS, High = in Ordnung):

* `VCC_OK`: VCCCORE ist im Sollbereich, erst dann sind `KNX_RX` und `KNX_TX`
  des STKNX nutzbar.
* `KNX_OK`: die Busspannung ist da.

Beide werden 50 ms entprellt (`TPUART_STATUS_DEBOUNCE_MS`). Bereit ist der
Transceiver nur, wenn beide High sind. Solange er es nicht ist,

* reicht der Emulator nichts vom Bus weiter und quittiert nichts – was
  `sblib` beim Ab- oder Aufbau der Spannung vom Bus liest, ist Müll;
* bestätigt er jedes `L_Data.req` sofort negativ (`0x0B`) und setzt
  `TRANSMIT_ERROR` im nächsten `U_State.ind`, statt 400 ms auf das Timeout
  zu warten;
* beantwortet er `U_State.req` weiter. Ein Host hielte ihn sonst nach 5 s für
  tot und finge an, ihn neu zu initialisieren. Der LPC wird nicht aus dem Bus,
  sondern vom Host versorgt und läuft weiter.

`U_SystemState.req` (`0x0D`) beantwortet er wie ein NCN5120 mit `0x4B` und
einem Statusbyte:

| Bit | NCN5120 | Emulator                                   |
| --- | ------- | ------------------------------------------ |
| 7   | XTAL    | immer 1                                    |
| 6   | VFILT   | immer 1                                    |
| 5   | VBUS    | `KNX_OK`                                   |
| 4   | VDD2    | `VCC_OK`                                   |
| 3   | V20V    | immer 1                                    |
| 2   | TW      | immer 0                                    |
| 1–0 | Mode    | `00` nicht bereit, `10` Stop-Modus, `11` normal |

Zusätzlich treibt er die Leitung **`/LPC_READY`** (PIO2_6, active low): Low,
solange der Emulator läuft und der Transceiver bereit ist. Im Reset, im
ISP-Bootloader und beim Booten hält der Reset-Pull-up des LPC sie High, ein
Pull-up auf der Host-Seite schadet nicht. Ein Pull-down auf der Host-Seite
dagegen kämpft gegen diesen Pull-up und ergibt keinen gültigen Pegel. Der Host
erkennt so ohne Umweg über die UART, dass das LPC neu gestartet ist oder die
Busspannung fehlt.

Ohne STKNX `TPUART_TRANSCEIVER_STATUS` auf `0` setzen; unbeschaltete Eingänge
lesen über ihren Pull-up ohnehin „in Ordnung“. `TPUART_READY_OUTPUT` auf `0`
lässt PIO2_6 unberührt.

### Transceiver → Host

| Code            | Service                        |
| --------------- | ------------------------------ |
| `0x03`          | `U_Reset.ind`                  |
| `0x07` \| Flags | `U_State.ind`, bei Receive- oder Protocol-Error auch unaufgefordert |
| `0x2B`          | `U_StopMode.ind` (NCN)         |
| `0x4B` + 1 Byte | `U_SystemStat.ind` (NCN)       |
| `0x41`          | `U_ProductID.response`         |
| `0xFD` …        | `U_EmuConfigure.res` (Selfbus), 4 Byte |
| `0xFE` …        | `U_EmuIdentify.res` (Selfbus), 19 Byte (Format 1: 17) |
| `0x8B` / `0x0B` | `L_Data.con` positiv / negativ |
| Rohframe        | `L_Data.ind` inkl. Prüfsumme, nach `U_ActivateCRC` plus 2 Byte CRC |

---

## Bewusste Abweichungen von einem echten TP-UART

Diese Punkte ergeben sich aus der Architektur von `sblib` und sind für den
Betrieb relevant.

### 1. ACK-Verhalten: autonom, nicht host-gesteuert

Ein echter TP-UART streamt die ersten Frame-Oktette sofort zum Host, der
innerhalb des Frames mit `U_AckInformation` antwortet. `sblib` meldet ein
Telegramm erst, wenn es **vollständig** empfangen ist – zwischen
`Bus::handleTelegram()` und dem ACK-Slot liegen nur ca. 13 Bitzeiten (≈ 1,4 ms),
zu wenig für einen UART-Roundtrip.

Deshalb quittiert die Bibliothek selbst.
Konkret: mit deaktiviertem Transport Layer quittiert `sblib` **jedes** empfangene Telegramm (Gruppen- *und* Individualadressen). `U_AckInformation` vom Host wird gelesen und verworfen.

Praktische Folge: Ein Telegramm an eine physikalische Adresse, die es auf der
Linie gar nicht gibt, wird trotzdem quittiert. ETS erkennt dadurch „Gerät nicht
vorhanden" nicht mehr zuverlässig, wenn nur dieses Interface an der Linie hängt.
Das ist dasselbe Verhalten wie beim Selfbus-FT1.2-Interface.

### 2. Kein Echo gesendeter Telegramme

Ein echter TP-UART spiegelt gesendete Frames als `L_Data.ind` zurück. `sblib`
gibt gesendete Telegramme nicht heraus. Sowohl knxd (`recvecho`) als auch der
OpenKNX-Stack (`_isEcho`) verwerfen das Echo ohnehin.

### 3. `L_Data.con` immer positiv

`sblib` meldet das Sendeergebnis nicht nach außen
(`TLayer4::finishedSendingTelegram()` ist nicht virtuell). Nach Abschluss der
Übertragung wird `0x8B` gesendet. `sblib` wiederholt intern bereits bei NACK und
BUSY (je 3×). Nur bei Zeitüberschreitung (`TPUART_TX_CONFIRM_TIMEOUT_MS`, 400 ms)
kommt `0x0B` plus gesetztes `TRANSMIT_ERROR` im nächsten `U_State.ind`.

### 4. Extended Frames bis 64 Oktette

Standard- und Extended Frames werden in beide Richtungen übertragen. Ein Frame
darf einschließlich Prüfsumme höchstens 64 Oktette lang sein
(`TPUART_MAX_FRAME_SIZE`), ein Extended Frame trägt damit eine APDU von bis zu
**55 Oktetten**. Der Host muss seine maximale APDU-Länge entsprechend
begrenzen. Längere Frames vom Host werden mit negativem `L_Data.con` und
`PROTOCOL_ERROR` abgewiesen, längere Frames vom Bus werden nicht quittiert und
nicht weitergereicht.

Extended Frames setzen eine `sblib` mit Extended-Frame-Unterstützung im `Bus`
voraus (Branch `feature/extended-frames` in
[jelli123/software-arm-lib](https://github.com/jelli123/software-arm-lib),
Pull Request an `Darthyson/dev/lib-refactor`).

### 5. Quellandresse

`Bus::prepareTelegram()` überschreibt die Absenderadresse mit
`bcu->ownAddress()`. Der Emulator setzt deshalb vor jedem Senden
`setOwnAddress()` auf die Quelladresse des Host-Frames. So bleibt das Telegramm
byte-identisch. Dies ist eine Voraussetzung für Tunneling mit mehreren Individualadressen.

### 6. Gedrosselte Ausgabe

`sblib` liefert ein Telegramm am Stück, ein echter TP-UART im Bustakt. Die
Ausgabe wird deshalb auf **1 Oktett pro Millisekunde** gedrosselt
(`TPUART_TX_PACING_MS`). Den Durchsatz begrenzt das nicht, TP1 ist mit 9600 bit/s
langsamer. Die Emulation bleibt so dicht am Verhalten echter Hardware. Es kostet
aber Latenz: Eine Antwort mit 11 Oktetten kommt 11 ms nach ihrem Empfang beim
Host an statt nach 6 ms. Ein Host, der Bursts verträgt, schaltet die Drosselung
deshalb mit `U_EmuConfigure` ab.

Relevant ist das vor allem für die **alte** OpenKNX-Implementierung
(`OpenKNX/knx`, `src/knx/tpuart_data_link_layer.cpp`). Sie verwirft den
Frame-Start, wenn beim ersten Byte schon mehr als `OVERRUN_COUNT` (7) Oktette
im RX-Puffer liegen. Die aktuelle Implementierung von `thelsing/knx` kennt
diese Prüfung nicht mehr; sie arbeitet mit `TpFrame` und einer 2-ms-Lücke zur
Resynchronisation.

Auf `0` setzen für ungedrosselte Ausgabe, wenn der Host Bursts verträgt
(knxd und der aktuelle OpenKNX-Stack tun das; beim alten Stack zusätzlich
`-DOVERRUN_COUNT=64` setzen).

### 7. Busy-Modus ohne BUSY-Quittung

Ein echter TP-UART 2 quittiert nach `U_ActivateBusyMode` 700 ms lang
adressierte Telegramme mit BUSY. `sblib` kann keine BUSY-Quittung senden, der
Emulator quittiert in dieser Zeit deshalb gar nicht. Der Sender wiederholt das
Telegramm in beiden Fällen. Wie beim echten Baustein beenden
`U_ResetBusyMode`, `U_AckInformation` und `U_Reset` den Busy-Modus vorzeitig.

---

## Host-Konfiguration

### knxd

```ini
[B.tpuart]
device = /dev/ttyKNX1
driver = tpuart
```

Oder auf der Kommandozeile:

```
knxd -e 1.0.240 -E 1.0.241:8 -D -T -R -S -b tpuart:/dev/ttyKNX1
```

ttyKNX1 ist das per Regel gemappte serielle Interface (siehe knxd Doku).

knxd schickt alle 10 s ein `U_State.req` als Keepalive – das wird beantwortet.

### OpenKNX-Stack

In der `platformio.ini`:

* `-DNCN5120` **weglassen** (sonst erwartet der Stack `U_Configure`,
  Marker-Mode, Baudratenumschaltung auf 38400 und die Analogregister)
* `-DKNX_BAUDRATE` weglassen, der Default 19200 ist korrekt
* UART auf `19200, SERIAL_8E1`
