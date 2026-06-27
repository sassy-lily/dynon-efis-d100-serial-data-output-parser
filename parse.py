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

logger = logging.getLogger(__name__)


class InvalidRecordException(Exception):
    pass


class CorruptedRecordException(Exception):
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


def signed(sign: str, digits: str) -> int:
    value = int(digits)
    return -value if sign == "-" else value


def parse(line: str) -> Record:
    line = line.rstrip('\n').rstrip('\r')
    line_length = len(line)
    if line_length != LINE_LENGTH:
        logger.warning('invalid record length: expected %d, found %d', LINE_LENGTH, line_length)
        raise InvalidRecordException()
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
    checksum = line[49:51]
    checksum_computed = '%0.2X' % (sum(ord(c) for c in line[:49]) & 0xFF)
    if checksum_computed != checksum:
        logger.warning('invalid record checksum: expected %s, found %s', checksum, checksum_computed)
        raise CorruptedRecordException()
    fields_discriminator = int(status_bitmask, 16) & 1
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


def main(argv: collections.abc.Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', help='the file to be parsed')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-m', '--metric', dest='system', action='store_const', const=System.METRIC,
                       help='convert the values to metrics')
    group.add_argument('-i', '--imperial', dest='system', action='store_const', const=System.IMPERIAL,
                       help='convert the values to imperial')
    parser.set_defaults(system=System.RAW)
    arguments = parser.parse_args(argv)
    skipped_lines = 0
    corrupted_lines = 0
    valid_line = 0
    current_line = 0
    records = []
    with open(arguments.file, 'rt') as file:
        for line in file:
            current_line += 1
            try:
                record = parse(line)
            except InvalidRecordException:
                logger.warning('line %d has been skipped: invalid record', current_line)
                skipped_lines += 1
                continue
            except CorruptedRecordException:
                logger.warning('line %d has been skipped: corrupted record', current_line)
                corrupted_lines += 1
                continue
            records.append(record)
            valid_line += 1
    # TODO Convert the values to the chosen measurements system, if requested.
    with open('output.csv', 'wt', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(Record._fields)
        writer.writerows(records)
    logger.info('skipped lines: %d', skipped_lines)
    logger.info('corrupted lines: %d', corrupted_lines)
    logger.info('valid lines: %d', valid_line)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main(sys.argv[1:])
