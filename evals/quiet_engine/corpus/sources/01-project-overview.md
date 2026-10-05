# Harbor Weather Network — project overview

The Harbor Weather Network is a volunteer project run by the Port Alder Makers to put a ring of low-cost weather stations around the town of Port Alder and publish what they measure. The town sits on an exposed stretch of coast, and the official forecast station is twenty kilometres inland, so local fishermen, the sailing school and the harbour master have long complained that the published wind numbers have nothing to do with what happens at the breakwater.

Ana Ruiz started the project in February 2026 after a squall flipped two dinghies at the sailing school. She leads the steering group and keeps the roadmap. The Port Alder Makers lend their workshop on Quay Street every Tuesday evening, and most of the build work happens there.

## What a weather station is, for us

A weather station in this project is a small, solar-powered box on a pole. Each one carries a temperature, humidity and pressure sensor, an anemometer for wind speed, a wind vane, and on some sites a tipping-bucket rain gauge. A single-board computer reads the sensors once a minute and sends the readings over the network. A station must survive salt spray, a winter with almost no sun, and the occasional gull.

We deliberately keep every station identical. A volunteer who has built one station should be able to repair any other one without reading new documentation.

## How the data flows

1. The station reads its sensors and publishes each reading as a small message over MQTT.
2. A broker on the club's server receives the messages.
3. A small bridge service writes the readings into InfluxDB, a time-series database.
4. A Grafana dashboard shows the live and historical readings to anyone with the link.

MQTT was chosen because it is light enough for a station that sleeps between readings, and because it lets the dashboard, the archive and any future consumer subscribe to the same stream without the station knowing about them.

## Goals for the first year

- Six stations on the waterfront by the end of summer: breakwater, sailing school, harbour office, lifeboat station, the old cannery roof, and the school field.
- A public dashboard that the harbour master can open on a phone.
- Storm alerts that reach the sailing school before a squall, not after.
- A data archive good enough that a student could use it for a science project.

## Non-goals

We are not trying to replace the national forecast, and we do not forecast at all. We measure. We also do not sell data or hardware; everything we write is published under an open licence and anyone in town can copy a station.

## Who is involved

- **Ana Ruiz** — project lead, roadmap, relationships with the harbour office.
- **Sam Patel** — firmware, the station software and over-the-air updates.
- **Priya Nair** — electronics, power budget, the solar charging circuit.
- **Tomas Weber** — the server, the database and the dashboard.
- **Lena Okafor** — science teacher at Port Alder High School, who brings students to the build nights.

## Where things live

Build notes, wiring diagrams and the meeting minutes are kept in the club's shared notes. Each topic has its own page: the hardware guide, sensor calibration, the MQTT topic layout, data storage, dashboards, the LoRa gateway, solar power, alerts, firmware updates and backups. Meeting notes are named by date.

## The Port Alder Makers

The Port Alder Makers are a community workshop founded in 2019 in a former chandlery on Quay Street. Members pay a small yearly fee and get access to the tools: a laser cutter, a pillar drill, soldering stations and a 3D printer. The club runs repair cafés on Saturdays and build nights on Tuesdays. The Harbor Weather Network is the club's largest project so far and the first one with a public service attached to it.

The club owns every station. Hosts — the sailing school, the harbour office, the lifeboat crew, the school and the cannery developer — own the pole or the roof, and can ask for a station to be removed at any time.

## How decisions are made

Small decisions are made by whoever is doing the work. Anything that affects more than one station, the public dashboard, or a host is raised at the weekly sync, which meets every second Wednesday. Decisions are recorded in the sync minutes and copied to the relevant topic page the same evening. When the minutes and a topic page disagree, the most recent minutes win until the page is fixed.

## Glossary

- **Station** — the whole box on a pole: sensors, computer, radio, battery, panel.
- **Sensor** — one measuring part in a station, for example the BME280 or the anemometer.
- **Gust** — the highest short-period wind speed within a minute.
- **Bucket** — a named store in the database with its own retention.
- **Flag** — a mark on a station's data saying it should not be trusted for a stated reason.
- **Build night** — the Tuesday evening session at the workshop.

## Timeline so far

- February 2026: the sailing school squall; Ana starts the project.
- March 2026: kickoff meeting; first bench station; broker and database running.
- April 2026: LoRa gateway on the harbour office roof; site survey; harbour committee approval for pole 14.
- May 2026: breakwater, sailing school and harbour office stations live; storm warning live.
- June 2026: lifeboat station, cannery and school field stations live.
