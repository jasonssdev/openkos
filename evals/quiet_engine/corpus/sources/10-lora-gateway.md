# LoRa gateway

Two stations — the breakwater and the lifeboat station — are out of reach of the town Wi-Fi. They send their readings over LoRa, a long-range, low-power radio, to a gateway on the harbour office roof. The gateway forwards each reading to the MQTT broker, so nothing downstream knows that these stations are different.

## Why LoRa

- Range: a kilometre and a half over water is easy for LoRa at low data rates; Wi-Fi does not get past the end of the quay.
- Power: the radio draws very little while transmitting a small packet once a minute.
- No subscription: unlike a mobile data link, LoRa costs nothing per month.

The cost is bandwidth. A LoRa packet is small, so the remote stations send a compact binary reading rather than the JSON payload the Wi-Fi stations publish, and the gateway expands it.

## The gateway

The gateway is a Raspberry Pi with a LoRa concentrator board and an outdoor antenna, in an enclosure on the harbour office roof. It has mains power and the office's wired network. Priya Nair and Sam Patel installed it in April.

## Range test

Before installing the gateway we ran a range test with a handheld node:

| Location | Distance | Signal | Result |
| --- | --- | --- | --- |
| End of the breakwater | 1.2 km | strong | every packet received |
| Lifeboat station | 0.9 km | strong | every packet received |
| Cannery roof | 0.6 km | strong | not needed, has Wi-Fi |
| Lighthouse point | 2.8 km | weak | about one packet in five lost |

The lighthouse is out of scope for now.

## Packet format

Each remote station packs one minute of readings into a 24-byte packet: station number, temperature, humidity, pressure, wind speed, gust, direction and battery. The gateway decodes it and publishes one MQTT message per measurement on the usual topics.

## Duty cycle

The radio band has a duty-cycle limit. At one packet a minute we use well under one percent of the allowance, so there is room for a second packet if a station ever needs to send more.

## What goes wrong

- Salt on the antenna connector. Seal it with self-amalgamating tape.
- The gateway losing its network when the harbour office router restarts. The gateway now buffers an hour of packets.
- A station's clock drifting, so readings arrive with the wrong time. The gateway stamps the time on arrival when the station's time is more than two minutes off.

## Gateway software

The gateway runs a small packet forwarder and a decoder written by Sam Patel. The decoder knows the 24-byte layout and publishes to the broker with the gateway's own credentials, under each remote station's topics. It keeps a counter of packets received per station and publishes it hourly, so a slowly failing antenna shows up as a falling count before readings start to go missing.

## Security

LoRa packets from the stations are signed with a per-station key. The gateway drops any packet whose signature does not match, so nobody with a radio can inject a fake gale reading into the storm warning. The packets are not encrypted: the readings are public anyway.

## Adding a remote station

1. Assign the station a number and a signing key, and add both to the gateway's configuration.
2. Flash the station image with the LoRa option and the station's key.
3. Run a range test from the new site with the handheld node before mounting anything.
4. Mount the station and watch the gateway's per-station packet counter for a day.

## Range test repeated in summer

A second range test in July, with the harbour full of moored boats, showed the same results as in April for the breakwater and the lifeboat station. Masts and hulls between the sites and the roof made no measurable difference at these distances.
