# Elfin EW11 setup (RS485 to Wi-Fi Modbus TCP)

These are the settings of the EW11 on this working system, taken from its
exported config. Set them in the EW11's web page.

## System settings

| Setting | Value | Note |
|---|---|---|
| WiFi Mode | STA | joins your home Wi-Fi |
| DHCP | Enable | give it a fixed address with a DHCP reservation on your router |
| Telnet | Disable | |
| Web | Enable, port 80 | change the default admin password |

## Serial port settings

Must match the inverter's RS485 port.

| Setting | Value |
|---|---|
| Baud rate | 9600 |
| Data bits | 8 |
| Stop bits | 1 |
| Parity | None |
| Flow control | Half-Duplex |
| Software flow control | Disable |
| **Protocol** | **Modbus** |
| Buffer size | 512 |
| Gap time | 50 ms |

With Protocol set to **Modbus**, the EW11 converts between Modbus TCP on the
network side and Modbus RTU on the RS485 side.

## Communication settings (socket `netp`)

| Setting | Value |
|---|---|
| Protocol | TCP-SERVER |
| Local port | **8899** (the port you give the integration) |
| Buffer size | 512 |
| Keep alive | 60 s |
| Timeout | 0 (never drop an idle connection) |
| Max accept | 3 |
| Security | None |
| Route | uart |

## Integration side

In the Growatt_ModbusTCP integration, set the EW11's IP address and port 8899.

> **Port 502 vs 8899.** Most guides say to use port **502**, the standard
> Modbus TCP port. On this system the integration only worked on **8899**,
> the port the EW11's `netp` socket listens on. If 502 doesn't connect, try
> 8899, or whatever Local port your EW11 socket shows.

## Wiring

Connect the EW11's RS485 A and B to the inverter's RS485 terminals (A to A,
B to B). Which terminals or pins those are **varies between SPH models and
hardware revisions**. Check your inverter's manual or the label by the port
before connecting, rather than copying another installation. If nothing
reads, swapping A and B is the usual first fix.

## Tips

- Only one thing should poll the inverter at a time. Max accept 3 allows extra
  connections, but two pollers at once make reads and writes collide.
- If the connection keeps dropping, check the Wi-Fi signal where the EW11 is
  mounted. Inverters are often in garages or lofts with poor coverage.
- Keep the EW11's web page off any network you don't trust. It has no
  encryption (Security: None).
