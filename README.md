# Dynon EFIS-D100 Serial Data Output Parser

Parse a Dynon EFIS-D100 RS-232 serial dump into CSV, with optional unit
conversion.

## Overview

The Dynon EFIS-D100 avionics display can emit a continuous stream of flight
telemetry over its RS-232 serial port (115200 baud, 8 data bits, no parity,
1 stop bit). Each reading is a fixed-width record of 51 ASCII characters
terminated by CR/LF, packing attitude, airspeed, altitude, vertical speed,
G-loads, angle of attack, and a timestamp.

This tool reads a captured dump of those records and writes one CSV row per
valid record, optionally converting the raw device units into metric, imperial,
or a mixed system. It has **no third-party runtime dependencies** — only the
Python standard library.

## Requirements

- Python 3.14 or newer

## Installation

```bash
pip install -e .          # runtime
pip install -e ".[dev]"   # runtime + pytest for development
```

## Usage

```bash
python parse.py <input_file> [-o output.csv] [-m | -i | -c]
```

| Option            | Unit system | Description                                  |
| ----------------- | ----------- | -------------------------------------------- |
| *(default)*       | `RAW`       | Raw device units, no conversion              |
| `-m`, `--metric`  | `METRIC`    | km/h, m/s, m                                 |
| `-i`, `--imperial`| `IMPERIAL`  | knots, feet, ft/min                          |
| `-c`, `--custom`  | `CUSTOM`    | Custom mixed system (km/h + feet)            |
| `-o`, `--output`  | —           | Output CSV path (default: `output.csv`)      |

The unit-system flags are mutually exclusive.

### Example

```bash
python parse.py sample.bin -m -o flight_data.csv
```

A single record on the wire looks like:

```
00082119+058-00541301200+9141+011-01+15003EA0C701A4
```

which decodes to time 00:08:21, pitch +5.8°, roll −5.4°, heading 130°, and so
on across the remaining fields.

## Output columns

Column headers are generated from the parsed record fields and include the unit
of each converted field (e.g. `Airspeed (m/s)`). The parsed fields cover:

- **Time** — hour, minute, second, and 1/64-second fraction (Zulu)
- **Attitude** — pitch, roll, yaw (heading)
- **Airspeed**
- **Altitude**
- **Turn rate / vertical speed (VSI)**
- **Lateral and vertical G-load**
- **Angle of attack**

Bit 0 of the record's status bitmask multiplexes two fields: it selects whether
the altitude column carries displayed or pressure altitude, and whether the
turn-rate column carries turn rate or vertical speed. See the format reference
for the full bit-level meaning.

## Data format

The complete wire format — field offsets, the status bitmask, the checksum
algorithm, and the unit-conversion formulas — is documented in
[`docs/FORMAT.md`](docs/FORMAT.md).

## Error handling

Each input line is validated independently, so a single bad record never aborts
the run:

- Records of the wrong length (`InvalidRecordException`) are skipped and counted
  as *skipped lines*.
- Records that fail the checksum (`CorruptedRecordException`) are skipped and
  counted as *corrupted lines*.
- Records whose fields are otherwise unparseable — a non-hex checksum or status
  field, a non-numeric value, or an invalid sign character — raise a plain
  `ValueError` and are likewise skipped and counted as *corrupted lines*.

Valid, skipped, and corrupted line counts are reported at the end of the run.

## Testing

```bash
pytest -ra
```

The test suite lives in `test_parse.py`. Continuous integration runs it on
Python 3.14 via GitHub Actions (`.github/workflows/tests.yml`).

## License

Distributed under the GNU Affero General Public License v3.0. See
[`LICENSE.md`](LICENSE.md) for the full text.
