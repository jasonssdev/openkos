# Breakwater gust incident — 2026-05-09

Incident note by Sam Patel and Priya Nair. Revised on 2026-05-16 after the data quality review.

## What happened

On the night of 2026-05-09 a north-westerly gale crossed the bay. The breakwater weather station first published a peak gust of 131 km/h at 03:12. The storm warning fired at 02:40 and stayed on until 06:30. The sailing school had no boats out.

At 03:20 the breakwater station's wind speed dropped to zero and stayed there while the lifeboat station still reported a gale. The anemometer health check alerted Sam and Tomas Weber at 09:20.

## Correction after the data quality review

The 131 km/h reading was one second long and came from the old one-second gust rule, while the cups were already breaking up. Re-computed with the three-second gust rule from the data quality review, the corrected peak gust was 127 km/h. The dashboard annotation and the published archive now show the corrected value, and the original reading is flagged rather than deleted.

## What we found

Priya and Sam went out on the breakwater the next afternoon. One of the three cups had snapped off the anemometer, and the hub was jammed with grit. The sensor calibration of the BME280 was unaffected; temperature and pressure kept publishing all night.

## Repair

The anemometer cup assembly was replaced with part number HX-7745, a reinforced polycarbonate cup set from the same manufacturer. The old plastic cups had been part number HX-7700. The hub was cleaned and greased. The station was back online at 16:05 on 2026-05-10.

## Decision

Replace the cups on every harbour-front station with HX-7745 sets at the next visit, starting with the lifeboat station.

## Follow-up

- Order four more HX-7745 cup sets as workshop spares.
- Annotate the outage and the correction on the dashboards.
