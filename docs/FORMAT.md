# EFIS-D100 Serial Data Format

Reference for the RS-232 serial output of the Dynon **EFIS-D100**, as defined in
the *EFIS-D100 Pilot's User Guide*, Appendix A. This document is self-contained:
it fully describes the wire format, how to parse it, and how to convert every
field to metric or imperial units, with a complete reference parser included
inline. No other files are required.

Serial port settings: **115200 baud, 8 data bits, no parity, 1 stop bit, no flow
control.** All values are output in decimal as standard ASCII.

---

## 1. The wire format

The EFIS emits one **fixed-width record per data frame**. Each record is exactly
**51 characters** followed by a CR/LF (`0x0D 0x0A`). Fields are positional — there
are no delimiters. Numeric fields are zero-padded; sign fields are a literal `+`
or `-`.

Positions below are **1-indexed start / width**, with the equivalent 0-indexed
Python slice.

| # | Field | Start | Width | Slice | Range / encoding |
|---|---|---|---|---|---|
| 1 | Hour | 1 | 2 | `[0:2]` | 00–23, Zulu time (internal clock) |
| 2 | Minute | 3 | 2 | `[2:4]` | 00–59 |
| 3 | Second | 5 | 2 | `[4:6]` | 00–59 |
| 4 | Fractions | 7 | 2 | `[6:8]` | 00–63, free-running 1/64 s frame counter (see *Time fields* note) |
| 5 | Pitch sign | 9 | 1 | `[8]` | `+`/`-` (`+` = pitched up) |
| 6 | Pitch | 10 | 3 | `[9:12]` | 000–900, units of 1/10° |
| 7 | Roll sign | 13 | 1 | `[12]` | `+`/`-` (`+` = banked right) |
| 8 | Roll | 14 | 4 | `[13:17]` | 0000–1800, units of 1/10° |
| 9 | Yaw / heading | 18 | 3 | `[17:20]` | 000–359°, 0=N, 90=E, 180=S, 270=W |
| 10 | Airspeed | 21 | 4 | `[20:24]` | 0000–9999, units of 1/10 m/s |
| 11 | Altitude sign | 25 | 1 | `[24]` | `+`/`-` (`+` = above sea level) |
| 12 | Altitude | 26 | 4 | `[25:29]` | 0000–9999, units of metres |
| 13 | Turn/VSI sign | 30 | 1 | `[29]` | `+`/`-` (`+` = turning right / climbing) |
| 14 | Turn rate **or** VSI | 31 | 3 | `[30:33]` | 000–999 (see bitmask below) |
| 15 | Lateral *g* sign | 34 | 1 | `[33]` | `+`/`-` (`+` = leftward accel.) |
| 16 | Lateral *g* | 35 | 2 | `[34:36]` | 00–99, units of 1/100 *g* |
| 17 | Vertical *g* sign | 37 | 1 | `[36]` | `+`/`-` (`+` = upward accel.) |
| 18 | Vertical *g* | 38 | 2 | `[37:39]` | 00–99, units of 1/10 *g* |
| 19 | Angle of attack | 40 | 2 | `[39:41]` | 00–99, % of stall angle |
| 20 | Status bitmask | 42 | 6 | `[41:47]` | 24-bit value, ASCII-hex |
| 21 | Internal use | 48 | 2 | `[47:49]` | ignore |
| 22 | Checksum | 50 | 2 | `[49:51]` | ASCII-hex, see below |

### Time fields — the Fractions counter

The first three time fields (hour, minute, second) come from the EFIS's internal
**real-time clock** (Zulu). The fourth field, **Fractions**, does *not*. The
authoritative D10A/D100 manuals define chars 7–8 as:

> "00 to 63, counter for 1/64 second. **Data output frequency.**"

It is a **free-running 6-bit frame counter** driven by the serial output engine:
it increments by exactly `+1` on every emitted frame and wraps `63 → 0`. The
device emits frames at a *nominal* 64 Hz, so the counter completes one cycle per
second *on average* — but it is **not** a sub-second offset derived from the
real-time clock, and the two are **not phase-locked**:

- The counter is **never reset on a second boundary.** Its `63 → 0` wrap falls at
  a fixed-but-arbitrary phase inside the RTC second (wherever the counter happened
  to sit when the clock started), so the wrap and the seconds tick generally do
  not coincide.
- The instantaneous output rate **jitters.** In a 221,309-record capture the rate
  averaged *exactly* 64.0000 frames per wall-clock second, yet individual seconds
  held anywhere from **55 to 73 frames**. Because the RTC second and the frame
  counter advance on independent clocks, their phase drifts continuously.

The practical consequence: in real captures the Fractions counter rolls over
mid-second, and the seconds value advances while Fractions is non-zero. This is
**expected behavior, not corruption** — see *Converting raw values* below for how
this affects timestamps.

### Status bitmask — bit 0 (LSB)

Two fields are **multiplexed** frame-to-frame; the meaning is selected by bit 0
of the status bitmask:

| bit 0 | Altitude (field 12) | Field 14 |
|---|---|---|
| `0` | **displayed** altitude | **turn rate** — 000–999, units of 1/10 °/s (yaw rate) |
| `1` | **pressure** altitude | **VSI** — 000–999, units of 1/10 ft/s |

Because the EFIS alternates bit 0 roughly every other frame, you receive
displayed altitude + turn rate in one frame and pressure altitude + VSI in the
next.

### Status bitmask — what's known

Only **bit 0** of the 24-bit field has a documented meaning (the alt/VSI
multiplex above). Dynon deliberately leaves the other 23 bits undefined. On their
official support forum (thread *"Serial Stream Status Bits"*, staff reply, Jan
2008) Dynon stated:

> "The other bits are for internal use and we don't publish their functions.
> They were used back when the EFIS used serial to talk to the EMS."

Every known open-source Dynon decoder (e.g. flyonspeed/TronView `serial_d100.py`,
JohnMarzulli/DynonToHud `dynon_decoder.py`) does a single `status & 1` test and
ignores every other bit. There is no published bit-by-bit definition.

**Empirical structure** (from a 221,309-record EFIS-D100 capture). Treating the
value as `0xHH_LLLL` (high byte = bits 16–23, low 16 = bits 0–15):

- **Low 16 bits (bits 0–15) — the real status.** They take only **four** values:
  `0x69F6` and `0x6AD9` (the two common alt/VSI modes, ~110k frames each) plus the
  rare `0x6936` / `0x6A11`. Within them, **bits 4, 11, 13, 14 are always 1** and
  **bits 10, 12, 15 are always 0**; only bits 0–9 move, and they co-vary with the
  documented mode (bit 0). In other words the low 16 bits encode nothing beyond
  the documented multiplex plus a fixed device signature.
- **High byte (bits 16–23) — undocumented internal value.** It varies across
  ~256 values, ~99% concentrated in `0xEC–0xF6`. Testing against the full capture
  rules out the obvious explanations:

  | Hypothesis | Test | Result |
  |---|---|---|
  | Free-running frame counter | consecutive deltas | ❌ unchanged in 99.1% of frames; no `+1` stepping |
  | Checksum/parity of the payload | sum/xor of data chars | ❌ ~1% match (chance level) |
  | Analog flight value (g, speed, altitude, …) | Pearson correlation | ❌ \|r\| < 0.06 on every channel |
  | Status flags | bit-cluster behavior | ❌ flags would cluster into a few combos, not spread over ~256 values |

  The byte holds steady for long stretches and jumps by arbitrary amounts when it
  changes — consistent with Dynon's "internal use / legacy EFIS↔EMS" statement
  (e.g. a sampled-and-held internal diagnostic register), but it carries no
  recoverable flight information.

**Practical guidance:** use `status & 1` for the alt/VSI multiplex and treat bits
1–23 as opaque internal data — safely ignorable, exactly as every other
implementation does.

> A related claim that surfaced during research did **not** hold up: the separate
> "Internal use" slot at chars 48–49 is sometimes called a "Product ID", but the
> authoritative D10A/D100 PDFs say "internal use".
>
> Conversely, the Fractions field (chars 7–8) **is** a free-running 1/64 s frame
> counter, exactly as the manual states ("counter for 1/64 second. Data output
> frequency."). It is *not* a sub-second offset of the real-time clock — see the
> *Time fields* note above.

### Checksum

The checksum (field 22) is the **low byte of the sum of the ASCII values of the
49 preceding characters**, formatted as two uppercase hex digits:

```
checksum == (sum(ord(c) for c in record[:49])) & 0xFF
```

### Example record

```
00082119+058-00541301200+9141+011-01+15003EA0C701A4
```

Decodes to: 00:08:21 (frame counter 19/64), pitch +5.8°, roll −5.4°, heading 130°,
airspeed 120.0 (×0.1 m/s), altitude +9141 m, field-14 +011, lateral *g* −0.01,
vertical *g* +1.5, AoA 00%, status `3EA0C7` (bit 0 = 1 → altitude is pressure
altitude, field 14 is VSI = 1.1 ft/s), checksum `A4`.

> **Note:** a capture may begin mid-record, so the very first line can be short
> (e.g. 46 chars). Discard any line whose length is not 51.

---

## 2. How to parse the wire format

1. **Read a line** and strip the trailing CR/LF.
2. **Validate length** — skip the line unless it is exactly 51 characters.
3. **Slice fields** by the fixed offsets in the table above.
4. **Apply signs**: read the sign char, parse the digits as an integer, negate if
   the sign is `-`. This yields the *raw encoded integer* (still in the device's
   1/10, 1/100, etc. units — no scaling yet).
5. **Decode the bitmask**: `bit0 = int(status_hex, 16) & 1`. Route field 12 to
   *displayed* vs *pressure* altitude and field 14 to *turn rate* vs *VSI*
   accordingly. Leave the unused alternate blank for that frame.
6. **Verify the checksum** (optional but recommended): compare the computed low
   byte against the parsed hex value; flag or drop mismatches.

Minimal reference implementation:

```python
def signed(sign, digits):
    v = int(digits)
    return -v if sign == "-" else v

def decode(line):
    if len(line) != 51:
        return None                      # malformed / partial frame
    f = {
        "hour":   int(line[0:2]),
        "minute": int(line[2:4]),
        "second": int(line[4:6]),
        "frame":  int(line[6:8]),        # free-running 1/64 s frame counter, 0–63
        "pitch":  signed(line[8],  line[9:12]),    # 1/10 deg
        "roll":   signed(line[12], line[13:17]),   # 1/10 deg
        "yaw":    int(line[17:20]),                # deg
        "airspeed": int(line[20:24]),              # 1/10 m/s
        "lateral_g":  signed(line[33], line[34:36]),  # 1/100 g
        "vertical_g": signed(line[36], line[37:39]),  # 1/10 g
        "aoa":    int(line[39:41]),                # percent
        "status_hex": line[41:47],
    }
    alt = signed(line[24], line[25:29])  # metres
    tv  = signed(line[29], line[30:33])  # 1/10 deg/s OR 1/10 ft/s
    bit0 = int(f["status_hex"], 16) & 1
    if bit0 == 0:
        f["alt_displayed"], f["alt_pressure"] = alt, None
        f["turn_rate"],     f["vsi"]          = tv,  None
    else:
        f["alt_displayed"], f["alt_pressure"] = None, alt
        f["turn_rate"],     f["vsi"]          = None, tv
    f["checksum_ok"] = (sum(ord(c) for c in line[:49]) & 0xFF) == int(line[49:51], 16)
    return f
```

The raw integer fields produced here are the device's encoded values, before any
unit scaling. Sections 3 and 4 below show how to convert them.

---

## 3. Converting raw values to metric units

The raw integers are in the device's fixed-point units. To obtain SI / metric
engineering units:

| Quantity | From raw | Formula | Metric unit |
|---|---|---|---|
| Time (clock) | hour, minute, second | `HH:MM:SS` | s (1 s resolution) |
| Frame index | frame | `frame` (a counter, not an offset) | frames (0–63) |
| Pitch | `pitch` | `pitch / 10` | degrees (°) |
| Roll | `roll` | `roll / 10` | degrees (°) |
| Heading | `yaw` | `yaw` | degrees (°) |
| Airspeed | `airspeed` | `airspeed / 10` | m/s |
| Airspeed (alt.) | `airspeed` | `airspeed / 10 * 3.6` | km/h |
| Altitude | `alt_displayed` / `alt_pressure` | value (no change) | metres (m) |
| Turn rate | `turn_rate` | `turn_rate / 10` | °/s |
| Vertical speed | `vsi` | `vsi / 10 * 0.3048` | m/s |
| Vertical speed (alt.) | `vsi` | `vsi / 10 * 0.3048 * 60` | m/min |
| Lateral *g* | `lateral_g` | `lateral_g / 100` | *g* |
| Vertical *g* | `vertical_g` | `vertical_g / 10` | *g* |
| Angle of attack | `aoa` | `aoa` | % of stall |

Notes:
- **Time:** the wall-clock timestamp has **1-second resolution** (HH:MM:SS from the
  internal RTC). Do **not** compute a sub-second time as `second + frame/64`:
  Fractions is a free-running frame counter, not a phase-locked offset, so it is
  not reset on the second boundary and adding it produces a timestamp that can run
  ahead of or behind the true sub-second position (see the *Time fields* note).
  Use `frame` only as a 0–63 frame index *within* the nominal 64 Hz stream — e.g.
  to order frames or detect dropped frames (a gap in the otherwise `+1` sequence).
- Altitude is **already metric** (metres) on the wire — no scaling is needed.
- VSI is encoded in **feet/second** even though everything else is SI, so a metric
  VSI requires the foot→metre factor (0.3048).
- `*g*` values are dimensionless multiples of standard gravity and are unit-agnostic.

---

## 4. Converting raw values to imperial / aeronautical units

These are the conventional units shown on an EFIS (knots, feet, feet-per-minute).

| Quantity | From raw | Formula | Imperial unit |
|---|---|---|---|
| Time (clock) | hour, minute, second | `HH:MM:SS` | s (1 s resolution) |
| Frame index | frame | `frame` (a counter, not an offset) | frames (0–63) |
| Pitch | `pitch` | `pitch / 10` | degrees (°) |
| Roll | `roll` | `roll / 10` | degrees (°) |
| Heading | `yaw` | `yaw` | degrees (°) |
| Airspeed | `airspeed` | `airspeed / 10 * 1.943844` | knots (kt) |
| Altitude | `alt_displayed` / `alt_pressure` | `value * 3.280840` | feet (ft) |
| Turn rate | `turn_rate` | `turn_rate / 10` | °/s |
| Vertical speed | `vsi` | `vsi / 10 * 60` | feet/min (ft/min) |
| Lateral *g* | `lateral_g` | `lateral_g / 100` | *g* |
| Vertical *g* | `vertical_g` | `vertical_g / 10` | *g* |
| Angle of attack | `aoa` | `aoa` | % of stall |

Conversion factors used:
- **m/s → knots:** `× 1.943844`
- **metres → feet:** `× 3.280840`
- **VSI:** raw is 1/10 ft/s, so feet/minute = `raw / 10 × 60` = `raw × 6`.
