# Backup and retention

What is backed up, how often, and for how long. Tomas Weber owns this page.

## What matters

The readings are the only thing we cannot rebuild. The broker, the bridge and the dashboards can be reinstalled from the notes in an evening; the history of the harbour's weather cannot.

## InfluxDB backups

- A nightly backup of all InfluxDB buckets runs at 02:30 and is written to an external disk on the club server.
- A weekly copy of the latest backup goes to a second disk that Ana Ruiz keeps at home.
- Nightly backups are kept for one month; weekly copies are kept for one year.

## Retention and backups are different things

The retention policy decides how long the database keeps readings. The backup schedule decides how long we can go back in time after a disaster. A raw reading that has aged out of the database is gone from the database, but it may still be in an old weekly copy until that copy is deleted. We do not promise that.

## Restore test

Once a quarter Tomas restores the latest backup to a laptop and checks that the hourly bucket for the last month matches the live database. The first restore test, in May, found that the downsampling tasks were not in the backup. They are now exported separately.

## Configuration backups

- The Grafana dashboards are exported as files after every change and kept in the shared notes.
- The broker configuration and the per-station passwords are kept in an encrypted file.
- The station configuration files, including calibration offsets, are kept in the shared notes; they are also on each station.

## What we do not back up

- The stations themselves. A station can be rebuilt from the image and its configuration file.
- The bridge's logs.

## Disaster scenarios

- **The club server's disk fails.** Restore the latest nightly backup to a new disk; at most one day of readings is lost, less if the bridge buffered them.
- **The workshop burns down.** Restore from Ana's weekly copy; at most a week is lost. Stations keep publishing, but nothing receives them until a new server is up.
- **Somebody deletes a bucket by mistake.** Restore that bucket alone from the nightly backup. This has happened once, during the first restore test, on the laptop and not on the server.

## The published archive

Once a month the daily summaries are exported as plain CSV files and published with the project's open licence. The archive is the copy we expect to outlive the project: anyone can download it, and other people's copies are backups we do not have to run.
