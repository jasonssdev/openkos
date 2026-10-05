# Hardware guide

This page lists what goes into one Harbor Weather Network station and why. Every station is built from the same parts list so that any volunteer can repair any station.

## Bill of materials

| Part | Qty | Notes |
| --- | --- | --- |
| Raspberry Pi Zero 2 W | 1 | The station computer. Runs the reader and the publisher. |
| BME280 sensor board | 1 | Temperature, humidity and barometric pressure on one I2C board. |
| Cup anemometer | 1 | Reed-switch type, one pulse per revolution. |
| Wind vane | 1 | Resistor-ladder type, read through an analogue-to-digital converter. |
| Tipping-bucket rain gauge | 0–1 | Only on sites with a clear sky view. |
| ADS1115 converter | 1 | Reads the wind vane. |
| 10 W solar panel | 1 | Mounted at 50 degrees, facing south. |
| Charge controller | 1 | Small MPPT controller. |
| LiFePO4 battery, 12 Ah | 1 | Chosen for cold-weather behaviour and cycle life. |
| IP66 enclosure | 1 | With a breathable vent plug. |
| Radiation shield | 1 | Louvred, white, for the BME280. |

## The station computer

The Raspberry Pi Zero 2 W was chosen over a microcontroller board because volunteers already know Linux and because it can run the same Python code we test on our laptops. The cost is power: it draws far more than a microcontroller, which is the whole reason the solar section of this guide exists. Sam Patel trimmed the image so that the board boots in under twenty seconds and spends most of each minute idle.

## The BME280 sensor

The BME280 sits inside a louvred radiation shield, never inside the main enclosure. Inside the box the electronics warm the air and the temperature reads two to four degrees high. The sensor is cheap and good, but each unit reads slightly differently, which is why every station goes through sensor calibration before it is mounted.

## The anemometer

The cup anemometer closes a reed switch once per revolution. The station counts pulses for a minute and converts the count to a wind speed with the manufacturer's factor. The cups are the part that breaks: gulls land on them and winter storms crack the plastic. Keep a spare cup assembly in the workshop.

Mount the anemometer at the top of the pole, at least one metre above anything else on the mast, or the readings are sheltered and low.

## The wind vane

The vane changes resistance with direction. The converter reads a voltage, and a lookup table maps the voltage to one of sixteen directions. The table is the same for every vane of this model.

## Rain gauge

The tipping bucket tips once per 0.28 mm of rain. It is only fitted where there is a clear view of the sky; on the cannery roof the wall shelters it and it under-reads badly.

## Enclosure and mounting

All electronics go in the IP66 enclosure. Cable glands face downwards so water drips off rather than in. The vent plug stops condensation from building up when the box cools at night. Use stainless fixings only — galvanised bolts rust through in one winter by the harbour.

## Power

The station runs from the solar panel through the charge controller into the battery. The power budget is documented on the solar power page. In short: the panel must refill in one bright day what the station spends in three dark ones.

## Assembly order

1. Flash the station image and set the station name.
2. Wire the BME280 and test it on the bench.
3. Calibrate the BME280 against the reference thermometer.
4. Wire the anemometer and vane; spin-test them with a fan.
5. Fit everything into the enclosure.
6. Bench-test for 24 hours on the battery before going up a pole.

## Wiring

| From | To | Notes |
| --- | --- | --- |
| BME280 VIN | Pi pin 1 (3.3 V) | Never 5 V; the board survives it but reads high. |
| BME280 GND | Pi pin 9 | |
| BME280 SDA | Pi pin 3 | Keep the cable under 40 cm, twisted with ground. |
| BME280 SCL | Pi pin 5 | |
| Anemometer reed switch | Pi pin 11 and ground | Internal pull-up enabled in the image. |
| Wind vane | ADS1115 channel A0 | 10 kΩ reference resistor to 3.3 V. |
| Rain gauge reed switch | Pi pin 13 and ground | Debounced in software, 50 ms. |
| ADS1115 | I2C bus, address 0x48 | Shares the bus with the BME280 at 0x76. |
| Charge controller load output | 5 V buck converter, then Pi | The controller cuts the load at 11.8 V. |

Label both ends of every cable. A volunteer opening the box in the rain will thank you.

## Troubleshooting

**No readings at all.** Check the battery voltage on the dashboard first. If it is below 11.8 V the controller has cut the load and the station is simply off. Look at the panel next: a gull nest, a pulled cable, or a cracked connector.

**Temperature reads high in the afternoon.** The radiation shield is cracked, missing a louvre, or the BME280 has slipped down onto the shield's base plate where it touches the warm pole. Re-seat it in the middle of the shield.

**Wind speed stuck at zero.** Spin the cups by hand. If the count does not change, the reed switch has failed or the cable is broken where it enters the pole. If the cups do not spin freely, the hub is full of grit or a cup is cracked; replace the whole cup assembly, not one cup.

**Wind direction stuck on one value.** The vane's resistor ladder is open. Measure it with a multimeter; it should show a different resistance for each of the sixteen directions.

**Pressure jumps by a fixed amount.** Somebody edited the station configuration and changed the pressure offset. Compare it with the calibration log.

**Readings arrive with the wrong time.** The station has no network time. Wi-Fi stations get the time from the club server; LoRa stations are stamped by the gateway.

## Spares to keep in the workshop

- Two BME280 boards, already calibrated against the reference thermometer, labelled with their offsets.
- Two cup assemblies for the anemometer.
- One complete wind vane.
- One Raspberry Pi Zero 2 W with the current image.
- A charge controller and a battery on a trickle charger.
- Stainless fixings, cable glands, vent plugs and self-amalgamating tape.

## Costs

A complete station costs about 240 euros in parts, of which the battery and the panel are half. The radiation shield is the part most people try to save money on, and it is the part that most affects the data.

## Station variants we rejected

- **A microcontroller station.** Much lower power, but every volunteer would have had to learn a new toolchain, and over-the-air updates would have been harder.
- **An all-in-one consumer station.** Cheap and tidy, but closed: no way to publish to our broker, no way to calibrate, and the anemometer is too low on the mast.
- **A heated rain gauge.** Snow is rare at the harbour, and the heater would double the power budget.

## Station build log

| Station | Built | Image at install | Link | Notes |
| --- | --- | --- | --- | --- |
| bench-1 | 2026-03-14 | 2026.03.16 | Wi-Fi | Workshop bench station; first station built. |
| bench-2 | 2026-03-24 | 2026.03.25 | Wi-Fi | Second bench station, for image testing. |
| breakwater | 2026-04-30 | 2026.04.12 | LoRa | Larger battery; cable later moved inside the pole. |
| sailing-school | 2026-05-02 | 2026.04.12 | Wi-Fi | Gable mast on the boathouse. |
| harbour-office | 2026-05-06 | 2026.04.12 | Wi-Fi | Pole 14, installed with the town engineer present. |
| lifeboat | 2026-06-03 | 2026.05.02 | LoRa | Slipway shelter; sheltered from the south. |
| cannery | 2026-06-10 | 2026.05.02 | Wi-Fi | No rain gauge; parapet wall. |
| school-field | 2026-06-17 | 2026.05.02 | Wi-Fi | Built with students; reference rain gauge. |

## Frequently asked questions

**Why not mount the BME280 in the main enclosure and save a part?** Because the enclosure heats up. The Pi and the converter warm the air inside by two to four degrees, more in sunshine. The radiation shield exists to measure the air, not the box.

**Can a station run without the battery, straight off the panel?** No. The Pi resets every time a cloud passes, and a reset in the middle of writing the configuration file has corrupted it once.

**Why stainless fixings everywhere?** Galvanised steel lasts one winter in salt spray. Stainless lasts for the life of the station. The difference in price for one station is about eight euros.

**Why is the panel so steep?** Winter sun is low, and winter is the season the battery is under pressure. A steep panel also stays cleaner.

**How high should the anemometer be?** As high as the mount allows, and at least a metre above anything else on the mast. The international standard is ten metres above open ground, which none of our sites can manage; we note each site's height on the dashboard so that readings are compared fairly.

**Can I add a different sensor?** Yes, on a bench station first. A new sensor gets a new measurement name on the MQTT topics and must not change any existing measurement.

**What does a station visit involve?** Check the cups spin freely and none is cracked, clean the radiation shield, wipe the panel, check the cable where it enters the pole, read the battery voltage, and check the enclosure seal and the vent plug. Write the visit into the station's log.

## Safety

Working on a pole at the harbour means working above water or hard ground, often in wind. Two people, always. Tie off the ladder. Do not open an enclosure in the rain unless the station is already broken. The battery stores enough energy to start a fire if it is short-circuited: disconnect it before touching the wiring, and never carry a battery loose in a bag with tools.
