#!/usr/bin/env python3
"""
mxfe_rate_math.py -- canonical AD9986/AD9081 (MXFE) NCO/FTW rate math + verifier.

Promoted from the build-recipe unification migration (gps_design thread
cross-cutting/20260703-socks-canonical-build-driver, 2026-07-25): until
this module existed, no executable FTW/NCO derivation lived anywhere in
the workspace -- pyadi-jif is cited as evidence in references but never
imported, and band tables were hand-carried between profiles. This is
now the single implementation; the frequency-planning reference
(gps-design skill, ad9986-gps-nco-frequency-planning.md) documents the
same formulas in prose.

Formulas (48-bit NCOs, all divisions must be EXACT for error_hz == 0):
  CDDC (coarse) FTW : center_hz / adc_frequency_hz * 2^48
  FDDC (fine)  FTW  : offset_hz / fddc_input_hz    * 2^48
  fddc_input_hz     = adc_frequency_hz / main_decimation

Verifier contract: `verify_operating_point(op)` takes a build-recipe
operating_point dict (schema_version-3 shape) and returns a list of
finding strings -- empty list means every recorded FTW, trailing-zero
count, error_hz, and center/offset relation is reproduced bit-exact.
CLI: `python3 mxfe_rate_math.py <build-recipe.json>` exits 1 on findings.
"""

import json
import sys

FTW_BITS = 48


def exact_ftw(numerator_hz, clock_hz):
    """Exact 48-bit FTW, or None if the ratio is not exactly representable."""
    num = numerator_hz * (1 << FTW_BITS)
    if num % clock_hz:
        return None
    return num // clock_hz


def trailing_binary_zeros(n):
    n = abs(n)
    if n == 0:
        return 0
    return (n & -n).bit_length() - 1


def band_ftw48(offset_hz, fddc_input_hz):
    ftw = exact_ftw(offset_hz, fddc_input_hz)
    if ftw is None:
        raise ValueError(f"offset {offset_hz} Hz not exactly representable at "
                         f"fddc_input {fddc_input_hz} Hz")
    return ftw


def cddc_ftw48(center_hz, adc_frequency_hz):
    ftw = exact_ftw(center_hz, adc_frequency_hz)
    if ftw is None:
        raise ValueError(f"CDDC center {center_hz} Hz not exactly representable at "
                         f"ADC {adc_frequency_hz} Hz")
    return ftw


def verify_operating_point(op):
    """Re-derive every recorded NCO word in an operating_point; return findings."""
    findings = []
    rx = op.get("rx", {})
    adc_hz = rx.get("adc_frequency_hz")
    main_dec = rx.get("main_decimation")
    if not adc_hz or not main_dec:
        return ["rx.adc_frequency_hz / rx.main_decimation missing -- cannot verify"]
    if adc_hz % main_dec:
        findings.append(f"adc_frequency_hz {adc_hz} not divisible by main_decimation {main_dec}")
        return findings
    fddc_in = adc_hz // main_dec

    cddc_center = rx.get("cddc_center_hz")
    if cddc_center is not None and "cddc_ftw_48" in rx:
        want = exact_ftw(cddc_center, adc_hz)
        if want != rx["cddc_ftw_48"]:
            findings.append(f"cddc_ftw_48 mismatch: recorded {rx['cddc_ftw_48']}, derived {want}")
        tz = trailing_binary_zeros(rx["cddc_ftw_48"])
        if "cddc_ftw_trailing_binary_zeros" in rx and tz != rx["cddc_ftw_trailing_binary_zeros"]:
            findings.append(f"cddc trailing-zeros mismatch: recorded "
                            f"{rx['cddc_ftw_trailing_binary_zeros']}, derived {tz}")

    for band in rx.get("bands", []):
        name = band.get("name", "?")
        if cddc_center is not None and "center_hz" in band and "offset_hz" in band:
            if band["center_hz"] - cddc_center != band["offset_hz"]:
                findings.append(f"band {name}: center-cddc != offset "
                                f"({band['center_hz']} - {cddc_center} != {band['offset_hz']})")
        if "offset_hz" in band and "ftw_48" in band:
            want = exact_ftw(band["offset_hz"], fddc_in)
            if want is None:
                findings.append(f"band {name}: offset {band['offset_hz']} not exact at {fddc_in}")
            elif want != band["ftw_48"]:
                findings.append(f"band {name}: ftw_48 mismatch recorded {band['ftw_48']}, derived {want}")
            if "ftw_trailing_binary_zeros" in band:
                tz = trailing_binary_zeros(band["ftw_48"])
                if tz != band["ftw_trailing_binary_zeros"]:
                    findings.append(f"band {name}: trailing-zeros mismatch recorded "
                                    f"{band['ftw_trailing_binary_zeros']}, derived {tz}")
            if band.get("error_hz", 0) != 0 and want == band.get("ftw_48"):
                findings.append(f"band {name}: error_hz {band['error_hz']} recorded but FTW is exact")

    tx = op.get("tx", {})
    dac_hz = tx.get("dac_frequency_hz")
    if dac_hz and "main_nco_shift_hz" in tx and "main_nco_ftw_48" in tx:
        want = exact_ftw(tx["main_nco_shift_hz"], dac_hz)
        if want is not None and want != tx["main_nco_ftw_48"]:
            findings.append(f"tx main_nco_ftw_48 mismatch: recorded {tx['main_nco_ftw_48']}, derived {want}")
        if want is None:
            findings.append(f"tx main_nco_shift_hz {tx['main_nco_shift_hz']} not exact at DAC {dac_hz}")

    return findings


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    with open(sys.argv[1]) as f:
        doc = json.load(f)
    op = doc.get("operating_point", doc)
    findings = verify_operating_point(op)
    for x in findings:
        print(f"FINDING: {x}")
    print("RATE MATH: " + ("ALL EXACT" if not findings else f"{len(findings)} findings"))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
