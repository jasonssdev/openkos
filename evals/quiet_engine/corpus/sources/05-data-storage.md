# Data storage

Readings from every station end up in InfluxDB on the club server. This page explains how they get there, how long they are kept, and how they are summarised.

## The bridge

A small Python service subscribes to `hwn/#` on the MQTT broker and writes each reading into InfluxDB. It drops duplicates (same station, measurement and reading time) because stations publish with at-least-once delivery. When InfluxDB is unreachable the bridge buffers up to an hour of readings in memory and then starts discarding the oldest; we accept that loss rather than writing to the SD card of the server.

## Why InfluxDB

We considered keeping readings in a plain relational database. InfluxDB won because it understands time ranges natively, downsampling is built in, and Grafana reads it without a plugin. Tomas Weber had also run it before, which mattered more than any benchmark.

## Buckets

- `raw` — every reading exactly as published, one point per minute per measurement per station.
- `hourly` — means, minima and maxima per hour, produced by a downsampling task.
- `daily` — the same per day, used for the yearly charts.

## Retention policy

Raw readings are kept for 90 days before downsampling. After that only the hourly and daily summaries remain. The hourly bucket is kept for five years and the daily bucket forever. Ninety days is enough to investigate any odd event in detail, such as a storm or a sensor fault, while keeping the server disk small.

## Downsampling

A scheduled task runs at five past every hour and computes the hourly mean, minimum and maximum for every measurement. A second task runs after midnight for the daily bucket. Wind gust is downsampled as a maximum only; a mean gust means nothing.

## Station names and tags

Every point carries the station name and the measurement as tags. When a station is moved to a new site it gets a new name, so that its history is not mixed with the old site's.

## Who looks after it

Tomas Weber owns the database. Ana Ruiz has read access for the reports she sends to the harbour office. The bridge's logs are kept for a week.

## Known gaps

- The breakwater station lost three days in March when its battery ran flat.
- The first two weeks of the school field station are uncalibrated and flagged in the dashboard.

## Querying the data

Most questions are answered from the hourly bucket. A typical question from the harbour office — "what was the strongest gust at the breakwater last month?" — is a maximum over the hourly gust maxima, which is exact because gusts are downsampled as maxima. Questions about a single storm use the raw bucket while it is still there.

## Disk

The club server has a 500 GB disk. Six stations at one reading per minute per measurement add roughly 200 MB a month to the raw bucket before compression; with the summaries, the database grows by a few gigabytes a year. The disk is not the constraint it looked like in March; the retention policy is kept anyway, because a smaller database makes backups and restores fast.

## Schema changes

Adding a measurement is free: a new measurement name simply appears. Renaming one is not: the old name stays in the history and every query and dashboard has to know both. Measurements are therefore never renamed; a replaced measurement keeps its old name and a new one is added beside it, as happened when the gust definition changed in May.
