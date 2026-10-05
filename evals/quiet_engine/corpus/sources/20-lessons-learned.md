# Lessons learned — first season

A retrospective written by Ana Ruiz after the first six months of the Harbor Weather Network, with input from everyone on the steering group.

## What worked

**Identical stations.** Every weather station is built the same way, so any volunteer can fix any station. When the cannery station failed, a student who had only built the school field station found the fault in ten minutes.

**MQTT in the middle.** Because every station publishes to one MQTT broker, adding the history dashboard and the storm warning needed no change to a single station. The decision at the kickoff to put MQTT in the middle paid for itself.

**Sensor calibration before mounting.** The stations agree with each other to within a few tenths of a degree. The two times the data quality checklist flagged a station, the calibration log made the cause obvious: a cracked radiation shield in one case, a failing sensor in the other.

**LoRa for the remote sites.** The breakwater and the lifeboat station have been as reliable as the Wi-Fi stations, apart from one connector full of salt.

## What did not

**Power in March, not December.** We planned for December and were caught out in March, when a gull nest pulled a cable out of the breakwater controller. The station went flat for three days. Cables now run inside the poles.

**Anemometer cups.** Three cup assemblies broke in six months, all on harbour-front stations. Gulls are the main suspect. We now keep spares and check the cups at every visit.

**One-minute gusts.** Measuring the gust as the highest one-second wind made the storm warning fire on single spikes. Since the May image, the gust is the highest three-second wind, which matches how the national service reports gusts.

**Documentation drift.** Pages written in March described decisions that changed in April. The weekly sync minutes were right; the topic pages were not. We now update the topic page in the same evening as the decision.

## What we would do differently

- Ask the town about street furniture before the site survey, not after.
- Buy a spare of every part from the start.
- Write the retention policy before the database fills, not after.

## Numbers

- Six stations live: breakwater, sailing school, harbour office, lifeboat station, cannery, school field.
- Thirty-one volunteers have attended at least one build night.
- The storm warning has fired eleven times; eight were real squalls, three were false alarms.
- No capsize at the sailing school since the warning went live.

## Next season

A second school station, the lighthouse site if a relay can be powered, and a public archive that other towns can copy.

## Notes by topic

### Weather stations

The station design held up. Of the six weather stations, only the breakwater station needed more than routine visits, and every repair used a part from the workshop spares. The radiation shield matters more than the sensor: the one station that read badly all season had a cracked shield, not a bad BME280.

### MQTT and the broker

The broker never went down. The one outage of the data flow was the bridge losing its connection to InfluxDB during a server update; it buffered the readings as designed and wrote them when the database came back. Publishing at-least-once and dropping duplicates in the bridge was the right split of responsibility.

### InfluxDB and retention

We decided the retention period in April, when the disk was already a third full. With the summaries in place, the database is now smaller than it was in April, and the history dashboard reads only summaries.

### Grafana dashboards

The harbour office uses the main dashboard every day; the sailing school uses the waterfront overview. Nobody uses the history dashboard except students and us. Annotations were the most useful addition of the season: every step in a chart now has an explanation next to it.

### Sensor calibration

Calibrating before mounting worked. Recalibrating after a winter will be the real test, because it means taking every station down. We plan to do it in rotation, one station per month, so no site is dark for long.

### LoRa

The gateway on the harbour office roof restarted twice when the office router did. Buffering on the gateway fixed it. The lighthouse site is still out of range without a relay.

### Solar power

The power budget was right for December and wrong about what kills stations. Physical damage to cables and panels — gulls, wind, salt — caused every power outage. None was caused by too little sun.

### Firmware updates

The staged rollout caught one bad image: the May image's first version mis-read the wind vane on the bench station. It never reached the harbour front.

### People

Thirty-one volunteers came to at least one build night; about eight come every week. The students from Port Alder High School built half the sensor boards. The project would have stalled in April without the students and without the harbour committee's quick approval of pole 14.

## Things to write down next season

- The recalibration rota.
- A checklist for a station visit: cups, shield, panel, cable, battery, enclosure seal.
- How to add a station at a new site, end to end, for other towns.
