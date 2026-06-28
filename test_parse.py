"""
Unit tests for parse.py — the Dynon EFIS-D100 serial-dump CSV parser.

The record layout and the unit-conversion math under test are specified in
docs/FORMAT.md (Appendix A of the EFIS-D100 Pilot's User Guide). The golden
vectors here come from that document's worked example and from real lines in
sample.bin.

On the error taxonomy (see TestParseLineErrors and TestFieldValueErrors):
parse_line() raises InvalidRecordException for a wrong-length frame and
CorruptedRecordException for a structurally valid frame whose checksum does not
match. Any other malformed field — a non-hex checksum or status, a non-digit
numeric field, or a bad sign character — surfaces as a plain ValueError. main()
catches all three and counts the line as skipped (invalid length) or corrupted
(everything else), so no malformed line ever crashes the run.
"""

import csv

import pytest

import parse
from parse import (
    Conversion,
    CONVERTERS,
    CorruptedRecordException,
    InvalidRecordException,
    Record,
    System,
    get_headers,
    get_row,
    parse_line,
    signed,
)

# --------------------------------------------------------------------------- #
# Golden vectors
# --------------------------------------------------------------------------- #

# Worked example from docs/FORMAT.md §1. bit0 of status 3EA0C7 is 1, so altitude
# is *pressure* altitude and field 14 is VSI.
EXAMPLE_LINE = "00082119+058-00541301200+9141+011-01+15003EA0C701A4"

EXAMPLE_RECORD = Record(
    hour=0,
    minute=8,
    second=21,
    second_fraction=19,
    pitch=58,
    roll=-54,
    yaw=130,
    airspeed=1200,
    altitude_displayed=None,
    altitude_pressure=9141,
    turn_rate=None,
    vertical_speed=11,
    lateral_g=-1,
    vertical_g=15,
    angle_of_attack=0,
    status_bitmask="3EA0C7",
    internal="01",
    checksum="A4",
)

# Verbatim lines from sample.bin (each is exactly 51 chars).
SAMPLE_LINES = [
    "09094144+018+00061030000+0182-007+01+1099F46A1101A0",
    "09094145+018+00081030000+0238-002+01+1099F46936019F",
    "09094149+018+00081030000+0238-002+02+1099F4693601A4",
    "09094150+018+00081030000+0182-006+01+1099F46A11019E",
]

# The real first line of sample.bin is a partial (mid-record) frame: 46 chars.
SAMPLE_PARTIAL_LINE = "143+018+00081030000+0182-007+01+1099F46A1101A1"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def checksum_for(body49: str) -> str:
    """Return the two uppercase hex digits parse.py expects for a 49-char body."""
    return format(sum(ord(c) for c in body49) & 0xFF, "02X")


def body_with_checksum(body49: str) -> str:
    """Append the correct checksum to a 49-character record body."""
    assert len(body49) == 49, f"body must be 49 chars, got {len(body49)}"
    return body49 + checksum_for(body49)


def _signed_field(value: int, width: int) -> str:
    """Format a signed integer as a '+'/'-' char followed by zero-padded digits."""
    sign = "+" if value >= 0 else "-"
    return sign + str(abs(value)).zfill(width)


def make_line(
    *,
    hour=9,
    minute=9,
    second=41,
    second_fraction=44,
    pitch=18,
    roll=6,
    yaw=103,
    airspeed=0,
    altitude=182,
    turn_or_vsi=-7,
    lateral_g=1,
    vertical_g=10,
    angle_of_attack=99,
    status="000000",
    internal="01",
    checksum=None,
):
    """
    Assemble a 51-char record from named fields, recomputing the checksum unless
    one is supplied explicitly (to allow injecting corruption). The default
    status '000000' has bit0 == 0 (displayed altitude / turn rate).
    """
    assert len(status) == 6
    assert len(internal) == 2
    body = (
        f"{hour:02d}"
        f"{minute:02d}"
        f"{second:02d}"
        f"{second_fraction:02d}"
        f"{_signed_field(pitch, 3)}"
        f"{_signed_field(roll, 4)}"
        f"{yaw:03d}"
        f"{airspeed:04d}"
        f"{_signed_field(altitude, 4)}"
        f"{_signed_field(turn_or_vsi, 3)}"
        f"{_signed_field(lateral_g, 2)}"
        f"{_signed_field(vertical_g, 2)}"
        f"{angle_of_attack:02d}"
        f"{status}"
        f"{internal}"
    )
    assert len(body) == 49, f"assembled body is {len(body)} chars: {body!r}"
    return body + (checksum if checksum is not None else checksum_for(body))


def test_make_line_helper_is_self_consistent():
    """The builder produces something parse_line accepts (guards the test harness)."""
    line = make_line()
    assert len(line) == parse.LINE_LENGTH
    parse_line(line)  # must not raise


# --------------------------------------------------------------------------- #
# signed()
# --------------------------------------------------------------------------- #


class TestSigned:
    @pytest.mark.parametrize(
        "sign, digits, expected",
        [
            ("+", "058", 58),
            ("-", "0054", -54),
            ("+", "000", 0),
            ("-", "000", 0),  # negative zero collapses to 0
            ("+", "0000", 0),
            ("+", "9", 9),
            ("-", "00099", -99),
        ],
    )
    def test_valid(self, sign, digits, expected):
        assert signed(sign, digits) == expected

    @pytest.mark.parametrize("sign", [" ", "x", "0", "*", ""])
    def test_invalid_sign_raises(self, sign):
        with pytest.raises(ValueError):
            signed(sign, "001")


# --------------------------------------------------------------------------- #
# parse_line() — happy path
# --------------------------------------------------------------------------- #


class TestParseLineHappyPath:
    def test_documented_example_decodes_exactly(self):
        assert parse_line(EXAMPLE_LINE) == EXAMPLE_RECORD

    @pytest.mark.parametrize("line", SAMPLE_LINES)
    def test_real_sample_lines_parse(self, line):
        record = parse_line(line)
        assert isinstance(record, Record)
        # Every sample line carries its own valid checksum at [49:51].
        assert record.checksum == line[49:51]

    @pytest.mark.parametrize("terminator", ["", "\n", "\r", "\r\n"])
    def test_line_terminators_are_stripped(self, terminator):
        assert parse_line(EXAMPLE_LINE + terminator) == EXAMPLE_RECORD

    def test_bit0_zero_routes_to_displayed_and_turn_rate(self):
        record = parse_line(make_line(altitude=1234, turn_or_vsi=56, status="000000"))
        assert record.altitude_displayed == 1234
        assert record.altitude_pressure is None
        assert record.turn_rate == 56
        assert record.vertical_speed is None

    def test_bit0_one_routes_to_pressure_and_vsi(self):
        record = parse_line(make_line(altitude=1234, turn_or_vsi=56, status="000001"))
        assert record.altitude_displayed is None
        assert record.altitude_pressure == 1234
        assert record.turn_rate is None
        assert record.vertical_speed == 56

    def test_bit0_uses_only_the_lsb(self):
        # 0x...E has bit0 == 0; 0x...F has bit0 == 1. Both are valid hex.
        even = parse_line(make_line(status="3EA0CE"))
        odd = parse_line(make_line(status="3EA0CF"))
        assert even.altitude_displayed is not None and even.altitude_pressure is None
        assert odd.altitude_pressure is not None and odd.altitude_displayed is None

    def test_negative_signed_fields(self):
        record = parse_line(
            make_line(pitch=-180, roll=-1800, altitude=-500, turn_or_vsi=-12,
                      lateral_g=-9, vertical_g=-8)
        )
        assert record.pitch == -180
        assert record.roll == -1800
        assert record.altitude_displayed == -500
        assert record.turn_rate == -12
        assert record.lateral_g == -9
        assert record.vertical_g == -8

    def test_status_and_internal_kept_as_raw_strings(self):
        record = parse_line(make_line(status="ABCDEF", internal="ZZ"))
        assert record.status_bitmask == "ABCDEF"
        assert record.internal == "ZZ"  # internal is opaque; not validated


# --------------------------------------------------------------------------- #
# parse_line() — error paths
# --------------------------------------------------------------------------- #


class TestParseLineErrors:
    @pytest.mark.parametrize(
        "line",
        [
            SAMPLE_PARTIAL_LINE,          # 46-char real partial frame
            "",                           # empty
            "   ",                        # whitespace only
            EXAMPLE_LINE[:-1],            # one short (50)
            EXAMPLE_LINE + "0",           # one long (52)
        ],
    )
    def test_wrong_length_raises_invalid_record(self, line):
        with pytest.raises(InvalidRecordException):
            parse_line(line)

    def test_length_error_message_reports_lengths(self):
        with pytest.raises(InvalidRecordException) as info:
            parse_line(SAMPLE_PARTIAL_LINE)
        message = str(info.value)
        assert str(parse.LINE_LENGTH) in message
        assert str(len(SAMPLE_PARTIAL_LINE)) in message

    def test_checksum_mismatch_raises_corrupted(self):
        # Flip the checksum to a valid-hex but wrong value.
        good = EXAMPLE_LINE
        wrong_cksum = "00" if good[49:51] != "00" else "FF"
        with pytest.raises(CorruptedRecordException) as info:
            parse_line(good[:49] + wrong_cksum)
        assert wrong_cksum in str(info.value)

    def test_non_hex_checksum_raises_value_error(self):
        # A non-hex checksum can't be parsed as int → bare ValueError, distinct
        # from the CorruptedRecordException reserved for a valid-hex mismatch.
        with pytest.raises(ValueError):
            parse_line(EXAMPLE_LINE[:49] + "ZZ")

    def test_non_hex_status_bitmask_raises_value_error(self):
        # Build a line whose checksum is valid but whose status is not hex, so we
        # reach the int(status, 16) failure rather than the checksum failure.
        line = make_line(status="GGGGGG")
        with pytest.raises(ValueError):
            parse_line(line)


class TestFieldValueErrors:
    """
    A structurally valid frame (right length, matching checksum) whose fields
    are nonetheless unparseable surfaces as a plain ValueError from parse_line();
    main() catches these and counts the line as corrupted (see TestMain).
    """

    def test_bad_sign_char_raises_value_error(self):
        # Corrupt the pitch sign (index 8) to a non-sign char, keep checksum valid.
        body = EXAMPLE_LINE[:8] + "x" + EXAMPLE_LINE[9:49]
        line = body_with_checksum(body)
        with pytest.raises(ValueError):
            parse_line(line)

    def test_non_digit_numeric_field_raises_value_error(self):
        # Corrupt the hour (index 0:2) to letters, keep checksum valid.
        body = "aa" + EXAMPLE_LINE[2:49]
        line = body_with_checksum(body)
        with pytest.raises(ValueError):
            parse_line(line)


# --------------------------------------------------------------------------- #
# CONVERTERS integrity
# --------------------------------------------------------------------------- #


class TestConverters:
    def test_every_system_has_a_converter(self):
        assert set(CONVERTERS) == set(System)

    @pytest.mark.parametrize("system", list(System))
    def test_converter_keys_are_record_fields(self, system):
        assert set(CONVERTERS[system]).issubset(set(Record._fields))

    def test_all_systems_convert_the_same_field_set(self):
        key_sets = [frozenset(CONVERTERS[s]) for s in System]
        assert len(set(key_sets)) == 1, "systems disagree on which fields convert"

    @pytest.mark.parametrize("system", list(System))
    def test_conversions_are_well_formed(self, system):
        for conversion in CONVERTERS[system].values():
            assert isinstance(conversion, Conversion)
            assert conversion.factor > 0
            assert conversion.decimals >= 0
            assert conversion.units


# --------------------------------------------------------------------------- #
# get_headers()
# --------------------------------------------------------------------------- #


class TestGetHeaders:
    def test_length_matches_record_fields(self):
        headers = get_headers(CONVERTERS[System.RAW])
        assert len(headers) == len(Record._fields)

    def test_convertible_fields_carry_units_others_are_bare(self):
        converter = CONVERTERS[System.RAW]
        headers = get_headers(converter)
        for name, header in zip(Record._fields, headers):
            if name in converter:
                assert header == f"{name} ({converter[name].units})"
            else:
                assert header == name

    def test_non_convertible_fields_stay_bare(self):
        # hour/minute/second and the trailing string fields have no converter
        # entry; yaw and angle_of_attack DO (factor 1) so they carry units.
        headers = get_headers(CONVERTERS[System.METRIC])
        for name in ("hour", "minute", "second", "status_bitmask",
                     "internal", "checksum"):
            assert name in headers

    @pytest.mark.parametrize(
        "system, expected",
        [
            (System.RAW, "airspeed (1/10 m/s)"),
            (System.METRIC, "airspeed (km/h)"),
            (System.IMPERIAL, "airspeed (kt)"),
            (System.CUSTOM, "airspeed (km/h)"),
        ],
    )
    def test_airspeed_units_per_system(self, system, expected):
        assert expected in get_headers(CONVERTERS[system])


# --------------------------------------------------------------------------- #
# get_row()
# --------------------------------------------------------------------------- #


class TestGetRow:
    def test_raw_passes_values_through_unchanged(self):
        row = get_row(EXAMPLE_RECORD, CONVERTERS[System.RAW])
        assert row == list(EXAMPLE_RECORD)

    def test_row_length_and_order_match_fields(self):
        row = get_row(EXAMPLE_RECORD, CONVERTERS[System.METRIC])
        assert len(row) == len(Record._fields)
        # Non-converted positional fields keep their original values/order.
        idx = Record._fields.index("status_bitmask")
        assert row[idx] == EXAMPLE_RECORD.status_bitmask

    def test_none_fields_stay_none(self):
        row = get_row(EXAMPLE_RECORD, CONVERTERS[System.METRIC])
        fields = Record._fields
        assert row[fields.index("altitude_displayed")] is None
        assert row[fields.index("turn_rate")] is None

    def _value(self, record, system, field):
        return get_row(record, CONVERTERS[system])[Record._fields.index(field)]

    @pytest.mark.parametrize(
        "system, field, expected",
        [
            (System.METRIC, "second_fraction", 0.297),
            (System.METRIC, "pitch", 5.8),
            (System.METRIC, "roll", -5.4),
            (System.METRIC, "airspeed", 432.0),
            (System.METRIC, "altitude_pressure", 9141),   # metres: unchanged
            (System.METRIC, "vertical_speed", 0.34),
            (System.METRIC, "lateral_g", -0.01),
            (System.METRIC, "vertical_g", 1.5),
            (System.IMPERIAL, "airspeed", 233.26),
            (System.IMPERIAL, "altitude_pressure", 29990),  # metres → feet, rounded int
            (System.IMPERIAL, "vertical_speed", 66),         # 1/10 ft/s → ft/min
            (System.CUSTOM, "airspeed", 432.0),              # km/h like metric
            (System.CUSTOM, "altitude_pressure", 29990),     # feet like imperial
        ],
    )
    def test_converted_values(self, system, field, expected):
        assert self._value(EXAMPLE_RECORD, system, field) == expected

    def test_decimals_zero_branch_returns_int(self):
        # altitude_pressure (imperial) has decimals == 0 → result is a plain int.
        value = self._value(EXAMPLE_RECORD, System.IMPERIAL, "altitude_pressure")
        assert isinstance(value, int)

    def test_decimals_nonzero_branch_returns_float(self):
        value = self._value(EXAMPLE_RECORD, System.METRIC, "pitch")
        assert isinstance(value, float)

    def test_yaw_factor_one_decimals_zero_is_unchanged(self):
        # yaw has a converter entry (factor 1, decimals 0) → round() to int.
        value = self._value(EXAMPLE_RECORD, System.METRIC, "yaw")
        assert value == 130
        assert isinstance(value, int)


# --------------------------------------------------------------------------- #
# main() — end to end
# --------------------------------------------------------------------------- #


def _write_input(tmp_path, lines):
    path = tmp_path / "input.txt"
    path.write_text("".join(line + "\n" for line in lines))
    return path


class TestMain:
    def test_end_to_end_writes_only_valid_rows(self, tmp_path, caplog):
        lines = [
            EXAMPLE_LINE,                       # valid
            SAMPLE_PARTIAL_LINE,                # invalid length → skipped
            EXAMPLE_LINE[:49] + "00",           # bad checksum → corrupted
            SAMPLE_LINES[0],                    # valid
        ]
        in_path = _write_input(tmp_path, lines)
        out_path = tmp_path / "out.csv"

        with caplog.at_level("INFO", logger="parse"):
            parse.main([str(in_path), "-o", str(out_path)])

        with out_path.open(newline="") as handle:
            rows = list(csv.reader(handle))

        # header + 2 valid data rows
        assert len(rows) == 3
        assert rows[0] == get_headers(CONVERTERS[System.RAW])
        # First data row corresponds to EXAMPLE_LINE; raw row stringified by csv.
        expected_first = [str(v) if v is not None else ""
                          for v in get_row(EXAMPLE_RECORD, CONVERTERS[System.RAW])]
        assert rows[1] == expected_first

        log_text = caplog.text
        assert "skipped lines: 1" in log_text
        assert "corrupted lines: 1" in log_text
        assert "valid lines: 2" in log_text

    def test_field_value_error_line_is_counted_corrupted(self, tmp_path, caplog):
        # A frame with a valid checksum but a non-digit field raises ValueError in
        # parse_line; main() must count it as corrupted rather than crash.
        bad_field_line = body_with_checksum("aa" + EXAMPLE_LINE[2:49])
        in_path = _write_input(tmp_path, [EXAMPLE_LINE, bad_field_line])
        out_path = tmp_path / "out.csv"

        with caplog.at_level("INFO", logger="parse"):
            parse.main([str(in_path), "-o", str(out_path)])

        with out_path.open(newline="") as handle:
            rows = list(csv.reader(handle))

        assert len(rows) == 2  # header + 1 valid row
        log_text = caplog.text
        assert "corrupted lines: 1" in log_text
        assert "valid lines: 1" in log_text

    @pytest.mark.parametrize(
        "flag, system",
        [
            (None, System.RAW),
            ("-m", System.METRIC),
            ("--metric", System.METRIC),
            ("-i", System.IMPERIAL),
            ("--imperial", System.IMPERIAL),
            ("-c", System.CUSTOM),
            ("--custom", System.CUSTOM),
        ],
    )
    def test_system_flag_selects_headers(self, tmp_path, flag, system):
        in_path = _write_input(tmp_path, [EXAMPLE_LINE])
        out_path = tmp_path / "out.csv"
        argv = [str(in_path), "-o", str(out_path)]
        if flag is not None:
            argv.append(flag)

        parse.main(argv)

        with out_path.open(newline="") as handle:
            header = next(csv.reader(handle))
        assert header == get_headers(CONVERTERS[system])

    def test_mutually_exclusive_systems_exit(self, tmp_path):
        in_path = _write_input(tmp_path, [EXAMPLE_LINE])
        with pytest.raises(SystemExit):
            parse.main([str(in_path), "-m", "-i"])

    def test_missing_input_file_exits_with_message(self, tmp_path, capsys):
        missing = tmp_path / "does-not-exist.bin"
        with pytest.raises(SystemExit):
            parse.main([str(missing), "-o", str(tmp_path / "out.csv")])
        stderr = capsys.readouterr().err
        assert str(missing) in stderr

    def test_default_output_path_is_output_csv(self, tmp_path, monkeypatch):
        in_path = _write_input(tmp_path, [EXAMPLE_LINE])
        monkeypatch.chdir(tmp_path)  # avoid clobbering the repo's output.csv
        parse.main([str(in_path)])
        assert (tmp_path / "output.csv").exists()

    def test_empty_input_writes_header_only(self, tmp_path, caplog):
        in_path = _write_input(tmp_path, [])
        out_path = tmp_path / "out.csv"
        with caplog.at_level("INFO", logger="parse"):
            parse.main([str(in_path), "-o", str(out_path)])
        with out_path.open(newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 1  # header only
        assert "valid lines: 0" in caplog.text
