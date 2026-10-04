# Weekly sync — 2026-04-01

Present: Ana Ruiz, Sam Patel, Tomas Weber, Lena Okafor. Priya Nair joined by phone for the power item.

## Status

- **Delivery.** Sam switched the publisher to at-least-once delivery. No readings lost on the bench station in a week of Wi-Fi drops.
- **Sleep versus idle.** Sam and Priya measured it: sleeping the Pi between one-minute readings saves almost nothing, because booting costs as much as a minute of idling. Stations will stay awake.
- **Retention.** Tomas proposed a retention period for raw readings; details are on the data storage page.
- **Harbour office.** The written permission for the gateway arrived. The LoRa gateway can go up.
- **School.** Lena reports that Port Alder High School agreed to a station on the school field, provided students help build it.

## Discussion

Lena brought three students to observe. They asked why the dashboard shows knots. Ana explained the harbour master's request.

Tomas raised that the first stations will all go live in the same month, so a fault in the image would hit all of them at once. Sam proposed a staged rollout for firmware updates: one station first, the rest a week later.

Ana asked for a storm alert before summer. The sailing school wants a warning when the pressure drops fast or the gusts rise. Tomas will draft thresholds.

## Decisions

1. **Stations stay awake between readings.** No deep sleep; the power budget assumes it.
2. **Firmware updates are staged.** One station first, the rest after a week without problems.
3. **School field station approved**, built with students during build nights.

## Actions

- Tomas: draft alert thresholds for the storm warning.
- Sam: staged update mechanism.
- Lena and Ana: schedule the student build nights.
- Priya: install the LoRa gateway on the harbour office roof with Sam.

## Any other business

- Tomas showed the second dashboard for the boathouse tablet; the sailing school will try it for a fortnight.
- Ana is writing to the town's harbour committee about mounting a station on a lamp post by the harbour office, after the site survey next week.
- Lena asked whether students can name the school field station. Agreed, as long as the station name used in topics stays `school-field`.
- Sam reported that the second bench station is running the long battery test.

## Next weekly sync

2026-04-15.
