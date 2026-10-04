# MQTT topic layout

All stations talk MQTT to the Mosquitto broker on the club server. This page fixes the topic names so that every consumer — the database bridge, the dashboard, the alerting job — can rely on them.

## Topic structure

```
hwn/<station>/<measurement>
```

- `hwn` is the network prefix, so the broker can host other club projects.
- `<station>` is the station name: `breakwater`, `sailing-school`, `harbour-office`, `lifeboat`, `cannery`, `school-field`.
- `<measurement>` is one of `temperature`, `humidity`, `pressure`, `wind_speed`, `wind_gust`, `wind_dir`, `rain`, `battery`.

The payload is a small JSON object with the value, the unit and the time the reading was taken, not the time it was sent:

```json
{"v": 12.4, "u": "C", "t": "2026-03-20T10:41:00Z"}
```

## Quality of service

Stations publish readings with QoS 1. A reading may then arrive twice, but it will not be lost when the link drops for a moment, and the database bridge ignores a duplicate with the same station, measurement and time. We tried QoS 0 first and lost about two percent of readings on the breakwater link.

## Retained messages

The `battery` topic is published with the retain flag, so that the dashboard shows each station's last battery level immediately, even if the station is asleep. Nothing else is retained.

## Status topic

Each station also publishes `hwn/<station>/status` with `online` when it connects and sets a last-will message of `offline`. The dashboard uses this to show which stations are reachable.

## Who can publish

Each station has its own username and password on the broker and may only publish under its own station name. The bridge service may subscribe to `hwn/#`. Nobody else can publish.

## Changes to this page

Changing a topic name breaks every consumer at once. Any change goes through the weekly sync first, and the old topic is kept publishing in parallel for a month.

## Units

Every payload carries its unit, and the units never change: degrees Celsius, percent relative humidity, hectopascals, metres per second for wind, degrees from north for direction, millimetres for rain and volts for the battery. Conversions to knots or to Fahrenheit happen only in the dashboard.

## Timestamps

The `t` field is the time the reading was taken, in UTC, to the minute. Stations on Wi-Fi get their time from the club server. Stations on LoRa send a reading time that the gateway checks; when it is more than two minutes off, the gateway replaces it with the arrival time and sets a flag in the payload, `"tf": true`, so the data quality checklist can find those readings later.

## Testing a new consumer

Anyone writing a new consumer — a student project, say — can subscribe to the read-only mirror topic `hwn-public/#` on the club's second listener, which carries the same messages with a ten-second delay and accepts no publishing at all. The mirror is what the published archive is built from.
