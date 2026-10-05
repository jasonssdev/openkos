# Kickoff meeting — 2026-03-04

Present: Ana Ruiz (chair), Sam Patel, Priya Nair, Tomas Weber, Lena Okafor. Held at the Port Alder Makers workshop on Quay Street.

## Purpose

First meeting of the Harbor Weather Network as a project of the Port Alder Makers. Agree the scope for the first year, the technology, and who does what.

## Discussion

**Ana** opened with the incident at the sailing school: two dinghies capsized in a squall that the official forecast did not show. The sailing school and the harbour office both want local wind readings. Ana proposed six stations on the waterfront in the first year.

**Sam** presented two options for getting readings off the stations. Option one: every station on the town Wi-Fi. Option two: a radio link. Wi-Fi does not reach the breakwater or the lifeboat station. Sam suggested Wi-Fi where it exists and a LoRa radio link for the two remote sites.

**Priya** raised power. A Raspberry Pi Zero on the breakwater, with no mains, needs a solar panel and a battery that survives December. She will prepare a power budget.

**Tomas** proposed MQTT for the messages and InfluxDB for storage, with Grafana on top. He has run all three before. Nobody objected.

**Lena** asked whether students can take part. Ana said yes: build nights are open, and the school field can host a station.

## Decisions

1. **Use MQTT for station telemetry.** Every station publishes its readings to one broker; consumers subscribe. Agreed by all.
2. **Use LoRa for the remote sites.** The breakwater and the lifeboat station get a LoRa link to a gateway on the harbour office roof; the other sites use Wi-Fi.
3. **All stations identical.** Same parts list, same image, same calibration procedure.
4. **Open by default.** Code, wiring and data are published under an open licence.

## Actions

- Priya: power budget for a station with no mains, by the next weekly sync.
- Sam: first station image, including the MQTT publisher.
- Tomas: broker, database and a first dashboard on the club server.
- Ana: ask the harbour office for permission to use their roof for the gateway.
- Lena: ask the school about a station on the school field.

## Next meeting

Weekly sync every second Wednesday, starting 2026-03-18.

## Notes from the open discussion

These are the chair's notes of the longer discussion after the agenda, kept because several points came back later.

**On naming.** Tomas asked whether we call them stations, nodes or sensors. Agreed: a *station* is the whole box on a pole; a *sensor* is one measuring part inside it; a *node* is only used for the handheld LoRa test device. Station names are the site, lowercase with hyphens.

**On the sailing school's request.** Ana relayed that the sailing school does not want a forecast, it wants to know what the wind is doing at the breakwater right now. Lena pointed out that this is a measurement question, not a forecasting question, and that keeping it that way keeps the project honest. Nobody wanted to forecast.

**On open data.** Priya asked whether the harbour office would object to the data being public. Ana thinks not; the harbour master already shares the tide gauge readings. Sam noted that a public broker would be a security risk, so the data is public through the dashboard and a published archive, never through the broker.

**On cost.** The Port Alder Makers have a small budget from membership fees. Ana estimated six stations at roughly 240 euros each plus a gateway and a server disk. The club can fund three stations now; she will apply to the town's community fund for the rest.

**On what could go wrong.** Tomas listed the risks as he saw them: salt, gulls, power in winter, a volunteer falling off a pole, and the project dying when the founders lose interest. The last one prompted the decision that everything is documented on topic pages, so a newcomer can take over a station without asking anyone.

**On the school.** Lena described what students could do safely: build sensor boards, calibrate, analyse data. Ana asked her to bring a short proposal to the school head.

**On timing.** The sailing season starts in May. Ana wants at least the breakwater and the sailing school stations live by then, with the storm warning to follow. Sam thought May was tight but possible if the image is ready by the end of March.

## Open questions recorded at the kickoff

- Which broker? Tomas suggested Mosquitto; to be confirmed when he sets up the server.
- How long do we keep raw readings? To be decided once we know the disk size.
- Who owns the station on a site — the club, or the host? Agreed later: the club owns every station; the host owns the pole or roof.
- Do we need insurance? Ana will ask the club's treasurer.
