# Breakwater gust incident — 2026-05-09

Incident note by Sam Patel and Priya Nair.

## What happened

On the night of 2026-05-09 a north-westerly gale crossed the bay. The breakwater weather station recorded a peak gust of 131 km/h at 03:12, the highest reading the Harbor Weather Network has ever published. The storm warning fired at 02:40 and stayed on until 06:30. The sailing school had no boats out.

At 03:20 the breakwater station's wind speed dropped to zero and stayed there while the lifeboat station still reported a gale. The anemometer health check alerted Sam and Tomas Weber at 09:20.

## What we found

Priya and Sam went out on the breakwater the next afternoon. One of the three cups had snapped off the anemometer, and the hub was jammed with grit. The sensor calibration of the BME280 was unaffected; temperature and pressure kept publishing all night.

## Repair

The anemometer cup assembly was replaced with part number HX-7745, a reinforced polycarbonate cup set from the same manufacturer. The old plastic cups had been part number HX-7700. The hub was cleaned and greased. The station was back online at 16:05 on 2026-05-10.

## Follow-up

- Order two more HX-7745 cup sets as workshop spares.
- Check the cups on the lifeboat station, the other exposed site.
- Annotate the outage on the dashboards.
