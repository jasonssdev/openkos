# Data quality checklist

Tomas Weber's checklist for deciding whether a station's readings can be trusted. Run it weekly, and always before quoting a number to the harbour office.

## Weekly checks

1. **Gaps.** Does every station have a reading for every minute of the week? Gaps longer than ten minutes are listed with a cause.
2. **Flat lines.** Has any measurement stayed exactly constant for more than six hours? Wind speed at zero while the neighbours report wind means a broken anemometer; a constant temperature means a stuck sensor.
3. **Neighbour comparison.** Is each station's temperature within two degrees of the median of the other waterfront stations? A persistent offset suggests the sensor calibration is wrong or the radiation shield is damaged.
4. **Pressure agreement.** Do the stations agree on pressure within 1 hPa? Pressure varies very little over a few kilometres, so disagreement is almost always a calibration problem.
5. **Battery trend.** Is any battery voltage trending down over the week?

## Outlier detection

A reading is marked as an outlier when it jumps by more than five standard deviations from the previous hour and comes back within two minutes. Outliers stay in the raw bucket but are excluded from the hourly summaries. Gusts are never marked as outliers, because a real gust looks exactly like an outlier.

## Sensor calibration checks

The calibration log records each station's offsets. When the neighbour comparison flags a station, compare its current offset with the last calibration. If the station drifted by more than half a degree since its last calibration, schedule a recalibration rather than adjusting the offset remotely.

## Flags on the dashboard

A station that fails a check gets a flag on the dashboard, with the reason. Flags are cleared by hand after the fix, with an annotation.

## What we do not do

We never edit raw readings. Corrections are applied by flagging, by offsets in the station configuration going forward, or by excluding readings from summaries. The raw bucket stays exactly what the stations sent, so that any correction can be undone.
