# Time Formats

## Purpose

This specification defines the shared, second-accurate time value formats used
by Chunky runtime interfaces. It defines representation only; each owning
operation still defines the meaning and validation of its time fields.

## Formats

### `duration`

A `duration` is a non-negative elapsed time expressed as an unsigned whole
number of seconds. Zero is representable. The operation using a duration
decides whether zero is valid for that field.

### `positive-duration`

A `positive-duration` is an elapsed time expressed as an unsigned whole number
of seconds. Its `seconds` value MUST be greater than zero.

### `absolute-time`

An `absolute-time` is an unsigned whole number of seconds since the Unix epoch,
`1970-01-01T00:00:00Z`. It cannot represent instants before the Unix epoch.

## Precision

These formats do not represent fractional seconds. A caller or binding that
starts with a higher-precision value MUST select the whole-second value before
crossing the WIT boundary. The policy for that conversion belongs to the
binding or operation that performs it.
