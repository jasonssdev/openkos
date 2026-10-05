# Dashboards

The public face of the Harbor Weather Network is a Grafana dashboard. Anyone with the link can open it; nobody needs an account to read it.

## The main dashboard

The main Grafana dashboard has one row per station and three panels per row:

- **Now** — current temperature, wind speed, gust and direction, as large numbers that are readable on a phone in the sun.
- **Last 24 hours** — temperature and pressure lines, wind speed and gust as an area.
- **Battery** — the battery voltage over the last week, so a failing panel shows up as a slow downward slope.

The harbour master asked for the wind panels to be in knots, not kilometres per hour. The dashboard shows knots; the database stores metres per second; nothing else is ever converted.

## The waterfront overview

A second dashboard shows only the four waterfront stations side by side, with a single wind panel per station and a compass rose. The sailing school keeps it open on a tablet in the boathouse. It refreshes every minute.

## The history dashboard

A third dashboard reads the hourly and daily buckets for long-range charts: monthly temperature, yearly rainfall, the windiest days. Students from Port Alder High School use it for coursework.

## Annotations

Station work is annotated on the dashboards: a vertical marker when a station was recalibrated, moved, or repaired. Without the markers, a step in the temperature line after a recalibration looks like weather.

## Access

The dashboards are public and read-only. Only Tomas Weber and Ana Ruiz can edit them. Changes to the main dashboard are discussed at the weekly sync, because the harbour office has learned where to look and moving a panel confuses them.

## Things we tried and dropped

- A map panel with all stations: pretty, but unreadable on a phone.
- Per-minute rain: too noisy; rain is shown hourly.
- A feels-like temperature: nobody agreed which formula to use, so we publish what we measure.

## Panel conventions

- Wind speed is a line, gust is a lighter area above it, direction is a row of arrows underneath. Every wind panel in every dashboard uses the same three.
- Temperature axes never start at zero; pressure axes always show at least 20 hPa so a normal day does not look like a storm.
- Missing data is shown as a gap, never joined up. A joined line across a three-day outage once convinced the harbour office that the breakwater had been calm all weekend.
- A flagged station's panels get a grey background and the reason in the panel title.

## Performance

The main dashboard reads the raw bucket for the last 24 hours and nothing older. Anything longer reads the hourly or daily bucket. That keeps the phone view fast on the harbour office's slow connection.
