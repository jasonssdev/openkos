# Firmware updates

Sam Patel's notes on how station software is built, versioned and updated over the air.

## The station image

Every station runs the same image: a trimmed Linux for the Raspberry Pi Zero 2 W, the reader that samples the BME280, the anemometer and the vane once a minute, and the MQTT publisher. The station's name and its calibration offsets live in a small configuration file outside the image, so one image serves every station.

## Versioning

Images are numbered by date, for example `2026.04.12`. The station publishes its image version on `hwn/<station>/status` when it connects, so the dashboard can show which station runs what.

## Over-the-air updates

Stations on Wi-Fi pull updates from the club server. Stations on LoRa cannot download an image over the radio; their updates are done on site with a laptop.

The update process on a Wi-Fi station:

1. The station checks the club server once a day for a newer image.
2. It downloads the image to the spare partition.
3. It reboots into the new partition.
4. If the new image does not publish a reading within five minutes, the station reboots into the old partition and reports the failed update.

## Staged rollout

Agreed at the weekly sync of 2026-04-01: a new image goes to one station first — the bench station in the workshop, then the school field station — and only after a week without problems to the rest. The harbour-front stations go last, because they are the ones the sailing school relies on.

## Changelog highlights

- `2026.03.16` — first image; BME280 and anemometer; QoS 0.
- `2026.03.25` — at-least-once delivery; duplicate-safe timestamps.
- `2026.04.12` — wind vane via the ADS1115 converter; battery topic retained.
- `2026.05.02` — gust measured as the highest three-second wind in each minute, instead of the highest one-second wind.

## Rolling back

Any station can be rolled back by pointing it at an older image on the server. The old image stays on the server for six months.

## Security

The update server only serves images signed with the project key. A station refuses an unsigned image. The key lives on Sam's laptop and in a sealed envelope with Ana Ruiz.

## Testing an image before release

1. Build the image on the club server from the tagged source.
2. Flash it onto the second bench station and leave it for 48 hours next to the first bench station, which runs the current image.
3. Compare every measurement between the two bench stations on the dashboard; they share one radiation shield, so they should agree within the calibration offsets.
4. Check that the status topic shows the new version and that a forced failed update rolls back by itself.
5. Only then publish the image to the update server for the staged rollout.

## The May image incident

The first build of the May image read the wind vane through the wrong converter channel, so every station would have reported a constant north wind. The bench comparison caught it within an hour, because the second bench station's vane pointed east the whole afternoon. The image never left the workshop. The fix was a one-line change to the channel number, and the test above gained step 3.

## Updating a LoRa station

LoRa stations get the same image with the radio option enabled. Updating one means a visit: a volunteer connects a laptop over USB, copies the image, and checks the status topic through the gateway before leaving the site. Because of the visit, LoRa stations are updated twice a year at most, and only for fixes that matter to them.
