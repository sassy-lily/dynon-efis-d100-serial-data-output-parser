#!/usr/bin/env python3

"""
Parse a Dynon EFIS D100 RS-232 serial dump into CSV.
The record layout is defined in the EFIS D100 Pilot's User Guide appendix A, pages 85-87.
Each record is a fixed width 51 ASCII characters line.
"""

import argparse
import collections.abc
import csv
import enum
import logging
import sys
import typing

LINE_LENGTH = 51


class InvalidRecordException(Exception):
    pass


class CorruptedRecordException(Exception):
    pass


class InvalidSignException(Exception):
    pass


class Record(typing.NamedTuple):
    hour: int
    minute: int
    second: int
    second_fraction: int
    pitch: int
    roll: int
    yaw: int
    airspeed: int
    altitude_displayed: int | None
    altitude_pressure: int | None
    turn_rate: int | None
    vertical_speed: int | None
    lateral_g: int
    vertical_g: int
    angle_of_attack: int
    status_bitmask: str
    internal: str
    checksum: str


class System(enum.Enum):
    RAW = 'raw'
    METRIC = 'metric'
    IMPERIAL = 'imperial'
    CUSTOM = 'custom'


class Conversion(typing.NamedTuple):
    factor: float
    decimals: int
    units: str


def signed(sign: str, digits: str) -> int:
    value = int(digits)
    match sign:
        case '+':
            return value
        case '-':
            return -value
        case _:
            raise InvalidSignException()


def parse_line(line: str) -> Record:
    line = line.rstrip('\n').rstrip('\r')
    line_length = len(line)
    if line_length != LINE_LENGTH:
        raise InvalidRecordException(f'expected length {LINE_LENGTH}, found {line_length}')
    checksum = line[49:51]
    checksum_computed = sum(ord(c) for c in line[:49]) & 0xFF
    try:
        if checksum_computed != int(checksum, 16):
            raise CorruptedRecordException(f'expected checksum {checksum}, found {checksum_computed:02X}')
    except ValueError as exception:
        raise CorruptedRecordException(f'expected hex checksum, found {checksum}') from exception
    hour = int(line[0:2])
    minute = int(line[2:4])
    second = int(line[4:6])
    second_fraction = int(line[6:8])
    pitch = signed(line[8], line[9:12])
    roll = signed(line[12], line[13:17])
    yaw = int(line[17:20])
    airspeed = int(line[20:24])
    altitude_displayed_or_pressure = signed(line[24], line[25:29])
    turn_rate_or_vertical_speed = signed(line[29], line[30:33])
    lateral_g = signed(line[33], line[34:36])
    vertical_g = signed(line[36], line[37:39])
    angle_of_attack = int(line[39:41])
    status_bitmask = line[41:47]
    internal = line[47:49]
    try:
        fields_discriminator = int(status_bitmask, 16) & 1
    except ValueError as exception:
        raise CorruptedRecordException(f'expected hex status bitmask, found {status_bitmask}') from exception
    if fields_discriminator == 0:
        altitude_displayed = altitude_displayed_or_pressure
        altitude_pressure = None
        turn_rate = turn_rate_or_vertical_speed
        vertical_speed = None
    else:
        altitude_displayed = None
        altitude_pressure = altitude_displayed_or_pressure
        turn_rate = None
        vertical_speed = turn_rate_or_vertical_speed
    return Record(hour, minute, second, second_fraction, pitch, roll, yaw, airspeed, altitude_displayed,
                  altitude_pressure, turn_rate, vertical_speed, lateral_g, vertical_g, angle_of_attack,
                  status_bitmask, internal, checksum)


CONVERTERS = {
    System.RAW: {
        'second_fraction': Conversion(1, 0, '1/64 s'),
        'pitch': Conversion(1, 0, '1/10 deg'),
        'roll': Conversion(1, 0, '1/10 deg'),
        'yaw': Conversion(1, 0, 'deg'),
        'airspeed': Conversion(1, 0, '1/10 m/s'),
        'altitude_displayed': Conversion(1, 0, 'm'),
        'altitude_pressure': Conversion(1, 0, 'm'),
        'turn_rate': Conversion(1, 0, '1/10 deg/s'),
        'vertical_speed': Conversion(1, 0, '1/10 ft/s'),
        'lateral_g': Conversion(1, 0, '1/100 g'),
        'vertical_g': Conversion(1, 0, '1/10 g'),
        'angle_of_attack': Conversion(1, 0, '% of stall')
    },
    System.METRIC: {
        'second_fraction': Conversion(1/64, 3, 's'),
        'pitch': Conversion(1/10, 1, 'deg'),
        'roll': Conversion(1/10, 1, 'deg'),
        'yaw': Conversion(1, 0, 'deg'),
        'airspeed': Conversion(1/10*3.6, 2, 'km/h'),
        'altitude_displayed': Conversion(1, 0, 'm'),
        'altitude_pressure': Conversion(1, 0, 'm'),
        'turn_rate': Conversion(1/10, 1, 'deg/s'),
        'vertical_speed': Conversion(1/10*0.3048, 2, 'm/s'),
        'lateral_g': Conversion(1/100, 2, 'g'),
        'vertical_g': Conversion(1/10, 1, 'g'),
        'angle_of_attack': Conversion(1, 0, '% of stall')
    },
    System.IMPERIAL: {
        'second_fraction': Conversion(1/64, 3, 's'),
        'pitch': Conversion(1/10, 1, 'deg'),
        'roll': Conversion(1/10, 1, 'deg'),
        'yaw': Conversion(1, 0, 'deg'),
        'airspeed': Conversion(1/10*1.943844, 2, 'kt'),
        'altitude_displayed': Conversion(1*3.280840, 0, 'ft'),
        'altitude_pressure': Conversion(1*3.280840, 0, 'ft'),
        'turn_rate': Conversion(1/10, 1, 'deg/s'),
        'vertical_speed': Conversion(1/10*60, 0, 'ft/min'),
        'lateral_g': Conversion(1/100, 2, 'g'),
        'vertical_g': Conversion(1/10, 1, 'g'),
        'angle_of_attack': Conversion(1, 0, '% of stall')
    },
    System.CUSTOM: {
        'second_fraction': Conversion(1/64, 3, 's'),
        'pitch': Conversion(1/10, 1, 'deg'),
        'roll': Conversion(1/10, 1, 'deg'),
        'yaw': Conversion(1, 0, 'deg'),
        'airspeed': Conversion(1/10*3.6, 2, 'km/h'),
        'altitude_displayed': Conversion(1*3.280840, 0, 'ft'),
        'altitude_pressure': Conversion(1*3.280840, 0, 'ft'),
        'turn_rate': Conversion(1/10, 1, 'deg/s'),
        'vertical_speed': Conversion(1/10*60, 0, 'ft/min'),
        'lateral_g': Conversion(1/100, 2, 'g'),
        'vertical_g': Conversion(1/10, 1, 'g'),
        'angle_of_attack': Conversion(1, 0, '% of stall')
    }
}


def get_headers(converter: dict[str, Conversion]) -> list[str]:
    return [f'{name} ({converter[name].units})' if name in converter else name for name in Record._fields]


def get_row(record: Record, converter: dict[str, Conversion]) -> list[float | int | str | None]:
    values = []
    for name, value in zip(Record._fields, record):
        conversion = converter.get(name)
        if conversion is None or value is None:
            values.append(value)
        else:
            scaled = value * conversion.factor
            rounded = round(scaled, conversion.decimals) if conversion.decimals else round(scaled)
            values.append(rounded)
    return values


def main(argv: collections.abc.Iterable[str] | None = None) -> None:
    logger = logging.getLogger(__name__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='the input file')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-m', '--metric', dest='system', action='store_const', const=System.METRIC,
                       help='convert the values to metrics')
    group.add_argument('-i', '--imperial', dest='system', action='store_const', const=System.IMPERIAL,
                       help='convert the values to imperial')
    group.add_argument('-c', '--custom', dest='system', action='store_const', const=System.CUSTOM,
                       help='convert the values to the Italian mixed system')
    parser.add_argument('-o', '--output', dest='output', default='output.csv', help='the output file')
    parser.set_defaults(system=System.RAW)
    arguments = parser.parse_args(argv)
    converter = CONVERTERS[arguments.system]
    headers = get_headers(converter)
    skipped_lines = 0
    corrupted_lines = 0
    valid_lines = 0
    current_line = 0
    with open(arguments.input, 'rt') as input_file, open(arguments.output, 'wt', newline='') as output_file:
        writer = csv.writer(output_file)
        writer.writerow(headers)
        for line in input_file:
            current_line += 1
            try:
                record = parse_line(line)
            except InvalidRecordException as exception:
                logger.warning('line %d has been skipped: invalid record (%s)', current_line, exception)
                skipped_lines += 1
                continue
            except CorruptedRecordException as exception:
                logger.warning('line %d has been skipped: corrupted record (%s)', current_line, exception)
                corrupted_lines += 1
                continue
            row = get_row(record, converter)
            writer.writerow(row)
            valid_lines += 1
    logger.info('skipped lines: %d', skipped_lines)
    logger.info('corrupted lines: %d', corrupted_lines)
    logger.info('valid lines: %d', valid_lines)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main(sys.argv[1:])
