# Solar power notes

Priya Nair's notes on keeping a station alive on sunlight alone.

## Power budget

The station draws about 0.6 W on average: the Raspberry Pi Zero idling, the sensors, and the Wi-Fi or LoRa radio. That is roughly 14.4 Wh a day.

In December, Port Alder gets on average a little under one peak-sun hour a day, and some weeks much less. A 10 W panel at 50 degrees gives perhaps 6–8 Wh on a typical December day, so the battery carries the deficit.

The 12 Ah LiFePO4 battery stores about 150 Wh, of which we allow ourselves 80 percent. That is roughly eight days of running from a full battery with no sun at all, or about four days of a typical December deficit before it needs a bright day.

## Why LiFePO4

Lead-acid batteries lose capacity in the cold and hate being left half-charged, which is exactly how a winter station lives. LiFePO4 tolerates partial charge, lasts for thousands of cycles, and is safe. Its weakness is charging below freezing; the charge controller we use refuses to charge below zero degrees, which at the harbour happens on only a few nights a year.

## The charge controller

The MPPT controller gets noticeably more out of the panel on overcast days than a simple controller. It also reports battery voltage, which the station publishes on the battery topic, so a failing panel shows up on the dashboard as a slow slope.

## Panel angle

The panel is mounted at 50 degrees, steeper than the summer optimum, because winter is the season that matters. A steep panel also sheds snow and gull droppings better.

## Lessons from the breakwater

The breakwater station went flat for three days in March. The panel was fine; a gull had nested behind it and the cable had been pulled out of the controller. We now route the cable inside the pole.

## Solar power for the gateway?

The gateway on the harbour office roof has mains power, so it needs no panel. If the lighthouse site is ever added, its relay would need a larger panel than a station, because a gateway's concentrator draws several watts.

## Measuring the budget

The figures above were measured, not taken from datasheets. Priya ran a bench station from a fully charged battery with the panel covered, logging the battery voltage on the battery topic, until the controller cut the load. It ran for seven days and fourteen hours, a little under the eight days the arithmetic promised, because the buck converter loses more than its datasheet says when the load is this small.

## Cold

LiFePO4 capacity drops in the cold. At the harbour the battery box rarely falls below three degrees, because the enclosure sits in the sun for part of the day and the sea keeps the air mild. A station inland or on a hill would need a battery heater or a bigger battery.
