# Weekly sync — 2026-03-18

Present: Ana Ruiz, Sam Patel, Priya Nair, Tomas Weber. Lena Okafor sent apologies.

## Status

- **Broker and database.** Tomas has the Mosquitto broker and InfluxDB running on the club server. The bridge service writes readings and drops duplicates. A first Grafana dashboard shows the bench station.
- **Station image.** Sam has a station image that reads the BME280 and the anemometer and publishes over MQTT. Boot takes eighteen seconds. Over-the-air updates are not done yet.
- **Power budget.** Priya presented the power budget: the Raspberry Pi Zero averages about 0.6 W with the radio idle. A 10 W panel and a 12 Ah LiFePO4 battery give roughly four days of autonomy in December. She recommends the bigger battery for the breakwater.
- **Harbour office roof.** Ana has verbal permission for the LoRa gateway on the harbour office roof; the harbour master wants it in writing.

## Discussion

The bench station was publishing with QoS 0 and lost readings when the Wi-Fi dropped. Tomas asked for at-least-once delivery; the bridge already handles duplicates. Sam will change it.

Priya asked whether the breakwater station could sleep the Pi between readings to save power. Sam thinks waking up costs more than idling, for a one-minute interval. They will measure it.

Tomas wants to decide how long raw readings are kept, because the disk on the club server is small.

## Decisions

1. **Store readings in InfluxDB, with downsampling.** Raw readings are summarised hourly and daily; the retention period is to be fixed by Tomas.
2. **Use the larger battery on the breakwater station.**

## Actions

- Sam: switch the publisher to at-least-once delivery; measure sleep versus idle power.
- Tomas: propose a retention period for raw readings.
- Ana: get the harbour office permission in writing.
- Priya: order panels and batteries for three stations.

## Any other business

- Tomas asked that every topic page say who owns it. Agreed; owners are added this week.
- Priya found a cheaper charge controller but it does not report battery voltage. Rejected: the battery topic is how we spot a failing panel.
- Ana reported that the sailing school would like the waterfront overview on a tablet in the boathouse. Tomas will make a second dashboard rather than change the main one.
- Sam asked for a second bench station, so that firmware can be tested while the first one runs a long battery test. Approved from the club budget.

## Next weekly sync

2026-04-01.
