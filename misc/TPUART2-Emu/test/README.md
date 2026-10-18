# Tests auf dem Bus

| Skript | Prüft |
| --- | --- |
| [knx_function_test.py](knx_function_test.py) | Funktion: Host-Services, Telegramme in beide Richtungen, CRC, Last, Ratenumschaltung |
| [updater_benchmark.py](updater_benchmark.py) | Durchsatz im Ablauf des Selfbus Updaters, Zerlegung der Zykluszeit |

Beide brauchen denselben Aufbau, siehe unten. `updater_benchmark.py` nutzt die
Klassen von `knx_function_test.py` und muss im selben Ordner liegen.

## Funktionstest

[knx_function_test.py](knx_function_test.py) prüft den TP-UART-2-Emulator an
einer echten KNX-TP1-Linie. Das Skript spricht den Emulator als TP-UART-Host
an. Über einen KNXnet/IP-Tunnel zu einem KNX/IP-Interface an derselben Linie
liest es mit, was auf dem Bus ankommt, und sendet selbst. Die Telegramme
vergleicht es in beiden Richtungen Oktett für Oktett.

```
             seriell 19200 8E1                KNX TP1
Test-PC ───────────────────────── TPUART2-Emu ═══════════ KNX/IP-Interface
   │                                                              │
   └───────────────────── KNXnet/IP-Tunnel (UDP 3671) ────────────┘
```

Das KNX/IP-Interface kann jedes beliebige sein. Getestet ist es mit dem
Selfbus KNX/IP-Interface (ESP32), das selbst einen TPUART2-Emu enthält.

## Voraussetzungen

* Python 3.8 oder neuer mit `pyserial` (`pip install pyserial`)
* der Emulator an einem USB-Seriell-Adapter, 3,3 V TTL
* ein KNX/IP-Interface mit freiem Tunnel an derselben Linie
* Busspannung an beiden Geräten, **richtig gepolt**. Bei falscher Polung
  meldet der Emulator trotzdem positive `L_Data.con` (siehe README, Abweichung 3),
  aber keine Seite empfängt etwas, auch nicht im Busmonitor.

Ein Adapter für Flash Magic steuert mit DTR und RTS die Leitungen `/RESET` und
`PIO0_1`. Das Skript öffnet den Port mit beiden Leitungen inaktiv und wartet
danach 2 s (`--boot`), denn schon das Öffnen setzt den LPC meist zurück.

## Aufruf

```
./knx_function_test.py --port /dev/ttyACM0 --gateway 192.168.179.105
```

| Option           | Vorgabe           | Bedeutung                                          |
| ---------------- | ----------------- | -------------------------------------------------- |
| `--port`         | `/dev/ttyACM0`    | serieller Port des Emulators                       |
| `--gateway`      | `192.168.179.105` | KNX/IP-Interface                                   |
| `--gateway-port` | `3671`            | UDP-Port des Interfaces                            |
| `--source`       | `1.1.200`         | physikalische Adresse, mit der der Emulator sendet |
| `--group`        | `15/7/0`          | erste Gruppenadresse; benutzt werden +0 bis +21    |
| `--burst`        | `30`              | Telegramme je Richtung in Abschnitt 7              |
| `--boot`         | `2.0`             | Wartezeit nach dem Öffnen des Ports in Sekunden    |
| `--no-extended`  | aus               | Extended Frames auslassen                          |

Die Gruppenadressen sollten auf der Linie frei sein: Das Skript schreibt
Testwerte darauf. Der Exit-Code ist 0, wenn alle Prüfungen bestehen.

## Was geprüft wird

1. **Host-Services:** `U_Reset`, `U_State`, `U_ProductID` (`0x41`),
   `U_EmuIdentify` mit Prüfsumme, Firmware- und sblib-Version und längstem
   Frame, `U_SystemState` (ab 0.12 `4B FB`: Bus und VCC in Ordnung, Mode
   normal; davor Protocol Error), `U_SetAddress`.
2. **Tunnel:** CONNECT und CONNECTIONSTATE zum KNX/IP-Interface.
3. **Emu → Bus → IP:** Gruppentelegramme mit 1 Bit, 1, 2 und 14 Byte als
   Standard Frame, Extended Frames mit 20 und 40 Byte und bis zum längsten
   Frame, den `U_EmuIdentify` meldet (64 Oktette = 54 Byte Daten). Geprüft
   werden ein positives `L_Data.con`, die Daten, die Quelladresse und der
   Frametyp.
4. **IP → Bus → Emu:** dieselben Telegramme in der Gegenrichtung. Geprüft
   werden die Prüfsumme des TP-Frames, die Quelladresse (= Tunnel-PA) und das
   positive `L_Data.con` am Tunnel.
5. **Zu langer Frame vom Host:** wird mit `U_State` Protocol Error und
   negativem `L_Data.con` abgewiesen und nicht gesendet.
6. **CRC-Modus:** Nach `U_ActivateCRC` muss die CRC16 an jedem `L_Data.ind`
   stimmen, `U_Reset` schaltet sie wieder ab.
7. **Last:** je 30 Telegramme direkt hintereinander. In Richtung Emu → IP
   kommt das nächste Frame erst nach dem `L_Data.con` des vorigen, wie bei
   knxd. Alle müssen ankommen, in Richtung IP → Emu auch in der richtigen
   Reihenfolge.
8. **Schnellere Rate** (ab Emulator 0.11, sonst übersprungen): ein ungültiger
   `U_EmuConfigure` wird abgewiesen. Umschalten auf die höchste Rate ohne
   Drosselung; `U_Reset` behält sie. Telegramme in beide Richtungen bis zum
   längsten Extended Frame. Der Rückfall auf 19200 Baud erfolgt, sobald der
   Host wieder mit 19200 anfragt und nach 5 s ohne Anfrage. Der Abschnitt
   dauert gut 6 s.

## Ergebnisse deuten

| Bild                                                         | Ursache                                                       |
| ------------------------------------------------------------ | ------------------------------------------------------------- |
| alle Telegramme fehlen, `L_Data.con` trotzdem positiv          | Bus ohne Spannung, verpolt oder nicht verbunden                  |
| `U_SystemState` → `4B C8`, `L_Data.con` sofort negativ         | ab 0.12: `KNX_OK`/`VCC_OK` des STKNX low – Bus ohne Spannung, oder Board ohne STKNX, dann `TPUART_TRANSCEIVER_STATUS=0` |
| `U_ProductID`/`U_EmuIdentify` stumm, `U_SystemState` → `4B 00` | Emulator-Firmware älter als 0.10                               |
| nur Extended Frames IP → Emu fehlen, `L_Data.con` negativ      | das KNX/IP-Interface sendet keine Extended Frames auf TP         |
| erste Antwort nach dem Öffnen fehlt                          | LPC startet noch, `--boot` erhöhen                             |

## Updater-Benchmark

```
./updater_benchmark.py --cycles 100 --monitor
```

Der Test-PC spielt auf beiden Seiten mit. Über den Tunnel sendet er, was der
Selfbus Updater (Calimero) für `UPD_SEND_DATA` sendet: `T_Data_Connected` mit
`A_UserMsg_Manufacturer_0`, Befehl und 13 Datenoktetten. Dann wartet er auf
`T_ACK` und Antwort und quittiert die Antwort. Am Emulator spielt er den
Bootloader. Der beantwortet jedes Datentelegramm sofort mit `T_ACK` und
einer Antwort mit 11 Oktetten.

| Option          | Vorgabe           | Bedeutung |
| --------------- | ----------------- | --------- |
| `--port`        | `/dev/ttyACM0`    | Emulator, der den Bootloader spielt |
| `--gateway`     | `192.168.179.105` | KNX/IP-Interface |
| `--device`      | `1.1.201`         | Adresse des simulierten Bootloaders |
| `--cycles`      | `100`             | Anzahl Zyklen |
| `--payload`     | `13`              | Datenoktette pro Telegramm, wie beim Updater |
| `--device-fast` | aus               | Emulator am Test-PC auf seine höchste Rate ohne Drosselung (ab 0.11) |
| `--monitor`     | aus               | Zyklus mit dem Busmonitor des Selfbus KNX/IP-Interface zerlegen |

Ausgegeben werden Byte/s und der Median der Zeitpunkte im Zyklus. Mit
`--monitor` kommen die Zeitpunkte am ESP32 hinzu, gemessen ab dem Moment, in
dem das Datentelegramm vom Tunnel kommt.

**Die Simulation ist langsamer als ein echter Bootloader.** Ihre Telegramme
nehmen den Weg über die UART des Emulators, und ihre Antwort kann sie erst
nach dem `L_Data.con` ihres `T_ACK` übergeben. Bei 19200 Baud kostet das rund
50 ms pro Zyklus. Mit `--device-fast` schrumpft dieser Anteil. Für den
Vergleich zwischen zwei Firmware-Ständen des Interfaces genügt das, solange die
Geräteseite gleich bleibt. Die Zeit vom Datentelegramm bis zu seinem
`L_Data.con` hängt nur am Interface.

Gemessen am 18.10.2026, ESP32-Interface und Emulator 0.10, beide mit 19200 Baud:
63,6 Byte/s mit Simulation, Zyklus 202 ms. Rechnet man den Anteil der Simulation
heraus, ergeben sich rund 145 ms. Das sind rund 90 Byte/s, so viel, wie der
Updater mit einem echten Gerät gemessen hat. Die Buszeit allein beträgt rund
97 ms (≈ 130 Byte/s).

Nach der Umstellung des Interfaces auf 115200 Baud ohne Drosselung
(Emulator 0.11 auf beiden Seiten):

| Messung | Byte/s | Zyklus | `L_Data.con` am Interface |
| --- | --- | --- | --- |
| Simulation bei 19200 Baud, wie oben | 77,9 | 162 ms | 53 ms (vorher 84 ms) |
| Simulation mit `--device-fast` | 106,8 | 122 ms | 54 ms |

Bei gleicher Geräteseite spart das Interface 40 ms pro Zyklus. Für ein echtes
Gerät ergibt das geschätzt rund 120 Byte/s.
