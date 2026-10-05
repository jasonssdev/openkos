# Sensor calibration

Every BME280 reads a little differently. Before a station goes up a pole, its sensor is calibrated against the club's reference thermometer and the harbour office barometer. A station that skipped calibration is not allowed on the public dashboard.

## Why we calibrate

Two uncalibrated BME280 boards side by side on the bench typically disagree by 0.5 to 1.0 degrees and by one or two hectopascals. That is enough to make the waterfront look colder than the school field when it is not, and it makes the storm alert threshold, which watches the pressure drop, fire at different times on different stations.

## Equipment

- The reference thermometer: a calibrated digital probe thermometer, kept in a drawer in the workshop and used for nothing else.
- The harbour office barometer, which the harbour master checks against the national service every month.
- A styrofoam box with a small fan, so the air around both sensors is the same.

## Procedure: calibrating the sensors

1. Put the station's BME280 and the reference thermometer probe side by side in the styrofoam box with the fan running.
2. Let both settle for thirty minutes.
3. Record ten paired readings, one per minute.
4. Compute the mean difference between the station and the reference. That difference is the temperature offset.
5. Take the station to the harbour office and record the pressure next to the office barometer. The difference is the pressure offset.
6. Write both offsets into the station's configuration file under its station name.
7. Record the date and the offsets in the calibration log.

Humidity is not calibrated; we publish it with a warning that it is accurate to plus or minus five percent.

## The calibration log

The calibration log is a simple table: station name, date, temperature offset, pressure offset, who did it. Tomas Weber reads the log when a station's readings look strange: a station whose offset changed a lot between two calibrations probably has a failing sensor.

## Recalibration

Recalibrate every station once a year, and after any sensor replacement. A sensor that has been through a winter at the harbour usually drifts by a few tenths of a degree. Recalibration uses the same procedure, but the station has to come down from its pole, which is the expensive part.

## Calibration and the anemometer

We do not calibrate the anemometers ourselves; we have no wind tunnel. We use the manufacturer's conversion factor and compare against the sailing school's handheld meter on a steady day. If a station reads more than ten percent off the handheld, the cups are checked for damage first.

## Common mistakes

- Calibrating with the sensor inside the enclosure. The electronics heat it.
- Calibrating in sunshine. The reference probe and the BME280 heat differently.
- Forgetting to write the offset into the configuration file, so the station publishes raw readings.

## Worked example

The sailing school station was calibrated on 2026-04-28 by Priya Nair and two students.

| Minute | Station (°C) | Reference (°C) | Difference |
| --- | --- | --- | --- |
| 1 | 14.62 | 14.10 | +0.52 |
| 2 | 14.65 | 14.12 | +0.53 |
| 3 | 14.70 | 14.15 | +0.55 |
| 4 | 14.71 | 14.17 | +0.54 |
| 5 | 14.69 | 14.16 | +0.53 |
| 6 | 14.66 | 14.14 | +0.52 |
| 7 | 14.68 | 14.15 | +0.53 |
| 8 | 14.72 | 14.18 | +0.54 |
| 9 | 14.73 | 14.20 | +0.53 |
| 10 | 14.70 | 14.17 | +0.53 |

The mean difference is +0.53 degrees, so the temperature offset written into the configuration is −0.53. At the harbour office the station read 1014.9 hPa against the office barometer's 1013.6 hPa, so the pressure offset is −1.3.

## Calibrating a replacement sensor in the field

When a BME280 fails on site, swap in one of the pre-calibrated spares from the workshop. Each spare is labelled with its own offsets. Copy the label's offsets into the station configuration before closing the box, and write the swap into the calibration log with the spare's label number. Never move a sensor's offsets to a different sensor.

## Calibration and the published data

Readings published before a station's first calibration carry a flag on the dashboard and in the archive. Offsets are applied on the station, before publishing, so the database holds corrected values; the raw uncorrected value can always be recovered by subtracting the logged offset.
