"""Read-only Nikon HE stream framing diagnostics; this does not decode pixels.

Private paths and framing reports must stay in the ignored output directory.
Format research: https://github.com/zidage/LibRaw/blob/main_alcedo/doc/nikon_he_public_algorithm.md
Only framing facts are used here, not the external decoder implementation.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import mmap
from pathlib import Path

from openraw_studio.core.files import sha256_file
from openraw_studio.raw.native.dng import (
    DngMetadataReader, _select_pixel_ifd, _tag_scalar_int,
)


def inspect_framing(data, *, offset, length, height):
    if offset < 0 or length < 6 or offset + length > len(data):
        raise ValueError("HE strip is outside the file bounds")
    if height <= 0 or height % 4:
        raise ValueError("This diagnostic requires a positive height divisible by four")
    end = offset + length

    def read(position, count):
        if position < offset or count < 0 or position + count > end:
            raise ValueError("Truncated HE framing")
        return bytes(data[position:position + count])

    if read(offset, 4) != b"\xff\x10\xff\x50":
        raise ValueError("Missing Nikon HE stream signature")
    position = offset + 2
    markers = []
    # The diagnostic supports the observed header, not every JPEG XS profile.
    for expected in (0xFF50, 0xFF12, 0xFF13, 0xFF14, 0xFF20):
        header = read(position, 4)
        marker = int.from_bytes(header[:2], "big")
        size = int.from_bytes(header[2:], "big")
        if marker != expected or size < 2:
            raise ValueError("Unrecognized HE marker sequence or invalid marker length")
        payload = read(position + 4, size - 2)
        if marker == 0xFF20 and payload != b"\x00\x00":
            raise ValueError("Unrecognized initial HE slice header")
        markers.append({"marker": f"{marker:04x}", "offset": position - offset, "length": size})
        position += 2 + size

    precinct_offset = position - offset
    expected_precincts = height // 4
    if expected_precincts > (end - position) // 12:
        raise ValueError("HE strip is too short for its declared height")
    regimes, depths = Counter(), Counter()
    smallest, largest = length, 0
    slices = 1
    for index in range(expected_precincts):
        prefix = read(position, 12)
        size = int.from_bytes(prefix[:3], "big")
        if size < 8 * 7 or position + 12 + size > end:
            raise ValueError("Invalid or truncated HE precinct payload")
        regimes[(prefix[3], prefix[4])] += 1
        for byte in prefix[5:]:
            for shift in (6, 4, 2, 0):
                depths[(byte >> shift) & 3] += 1
        smallest, largest = min(smallest, size), max(largest, size)
        position += 12 + size
        if (index + 1) % 16 == 0 and index + 1 < expected_precincts:
            boundary = read(position, 6)
            if boundary[:4] != b"\xff\x20\x00\x04" or int.from_bytes(boundary[4:], "big") != slices:
                raise ValueError("Missing or out-of-order HE slice boundary")
            slices += 1
            position += 6
    if position + 2 != end or read(position, 2) != b"\xff\x11":
        raise ValueError("HE precinct walk does not end at the strip end marker")
    return {
        "framing_valid": True, "pixels_decoded": False,
        "header_markers": markers, "precinct_offset": precinct_offset,
        "precincts": expected_precincts, "slices": slices,
        "payload_size_range": [smallest, largest],
        "bp_br_counts": [{"bp": bp, "br": br, "count": count} for (bp, br), count in sorted(regimes.items())],
        "depth_hint_counts": dict(sorted(depths.items())),
    }


def inspect_source(source):
    with source.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        _order, _endian, ifds = DngMetadataReader()._read_structure(data)
        raw = _select_pixel_ifd(ifds)
        if _tag_scalar_int(raw, 259, "Compression") != 34713:
            raise ValueError("Not a Nikon compressed RAW strip")
        offset = _tag_scalar_int(raw, 273, "StripOffsets")
        length = _tag_scalar_int(raw, 279, "StripByteCounts")
        height = _tag_scalar_int(raw, 257, "ImageLength")
        width = _tag_scalar_int(raw, 256, "ImageWidth")
        return {"width": width, "height": height, "strip_bytes": length,
                **inspect_framing(data, offset=offset, length=length, height=height)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    ignored = Path(__file__).resolve().parents[1] / "output"
    if not args.output.resolve().is_relative_to(ignored):
        parser.error("Private diagnostics must stay inside the project's ignored output folder")
    records = []
    for source in args.sources:
        record = {"source": str(source), "pixels_decoded": False, "source_unchanged": None}
        before = None
        try:
            before = sha256_file(source)
            record.update(inspect_source(source), ok=True)
        except (ValueError, OSError) as error:
            record.update(ok=False, error=str(error))
        if before is not None:
            try:
                record["source_unchanged"] = before == sha256_file(source)
            except OSError as error:
                record.update(ok=False, error=f"Source verification failed: {error}")
        records.append(record)
        print(json.dumps(record), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return int(any(not r["ok"] or r["source_unchanged"] is not True for r in records))


if __name__ == "__main__":
    raise SystemExit(main())
