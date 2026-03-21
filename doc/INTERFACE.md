# USR-R16-T Relay Control Guide

This repository contains the technical documentation and command set for the **USR-R16-T** 16-channel Ethernet Relay Module.

---

## 🛠 Technical Specifications

* **Power Supply:** DC 12V / 24V (Check your device label).
* **Relays:** 16 Channels (10A 250VAC / 10A 30VDC).
* **Interface:** RJ45 Ethernet Port (TCP/IP).
* **Default Port:** `8899`
* **Default Login:** `admin` / `admin`

---

## 🔌 Wiring Diagram

Each relay has a 3-pin terminal:
1.  **NO (Normally Open):** Circuit is open by default. Closes when "ON" command is sent.
2.  **COM (Common):** The main input line.
3.  **NC (Normally Closed):** Circuit is closed by default. Opens when "ON" command is sent.

---

## 💻 Control Commands (Hexadecimal)

The module uses the **Lonhand Protocol**. Each command frame must be exactly **18 bytes** long. The last byte is a **Checksum** (sum of all previous bytes).


# Lonhand Protocol Specification (USR-R16)

The Lonhand protocol is a lightweight, 18-byte fixed-length binary protocol used for industrial relay control over TCP/UDP.

## 1. Frame Structure
Every data packet must strictly follow this 18-byte layout:

| Byte | Field | Value / Description |
| :--- | :--- | :--- |
| 0 | Header | `0x00` (Always constant) |
| 1 | Control Code | `0x55` (ON), `0xAA` (OFF), `0xFF` (Query), `0xBB` (Rename) |
| 2 | Data 1 | Relay ID (`0x01` to `0x10`) or Global Command (`0x00`) |
| 3 | Data 2 | State (`0x01` for ON, `0x00` for OFF) |
| 4 - 16 | Reserved | `0x00` (Padding bytes) |
| 17 | Checksum | Sum of bytes 0 to 16 (8-bit truncation) |

---

## 2. Command Codes Reference

### A. Individual Control
To trigger a specific relay, set Byte 2 to the hex value of the relay (1-16).
* **Switch ON:** Byte 1 = `0x55`, Byte 3 = `0x01`
* **Switch OFF:** Byte 1 = `0xAA`, Byte 3 = `0x00`

### B. Global Control
To trigger all relays at once, set Byte 2 to `0x00`.
* **All ON:** `00 55 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 55`
* **All OFF:** `00 AA 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 AA`

### C. Status Query
To read the current state of all relays, send `0xFF` in Byte 1.
* **Query Frame:** `00 FF 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 FF`
* **Response:** The module returns 18 bytes.
    * **Byte 2:** Status of Relays 9 to 16 (Bitmask)
    * **Byte 3:** Status of Relays 1 to 8 (Bitmask)


### Individual Relay Control

| Relay | Action | Hexadecimal Command (18 Bytes) |
| :--- | :--- | :--- |
| **Relay 1** | **ON** | `00 55 01 01 00 00 00 00 00 00 00 00 00 00 00 00 00 57` |
| **Relay 1** | **OFF** | `00 aa 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ab` |
| **Relay 2** | **ON** | `00 55 02 01 00 00 00 00 00 00 00 00 00 00 00 00 00 58` |
| **Relay 2** | **OFF** | `00 aa 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ac` |
| **Relay 16** | **ON** | `00 55 10 01 00 00 00 00 00 00 00 00 00 00 00 00 00 66` |
| **Relay 16** | **OFF** | `00 aa 10 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ba` |

### Batch & Status Commands

* **All Relays ON:** `00 55 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 55`
* **All Relays OFF:** `00 aa 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 aa`
* **Read Status (Query):** `00 ff 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ff`

---

## 🚀 Quick Start (Python Example)

```python
import socket

# Configuration
IP = "192.168.0.7"
PORT = 8899

# Command to turn Relay 1 ON
CMD_ON = bytes.fromhex("005501010000000000000000000000000057")

try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect((IP, PORT))
        s.sendall(CMD_ON)
        print("Command sent successfully!")
except Exception as e:
    print(f"Error: {e}")