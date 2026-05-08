# Instrument Reference

Use the EMI repo for exact driver behavior and command implementations:

- `emi/instruments/rsa5000.py`
- `emi/instruments/fph.py`
- `emi/instruments/sdg2000x.py`
- `emi/instruments/sma100a.py`
- `tools/emi_control.py`
- `README.md`

## Analyzer Selection

The repo supports RSA and FPH analyzer backends for swept trace workflows.
Analyzer substitution changes safe transfer paths, point counts, detector
choices, and verification checks.

### Rigol RSA5000/RSA5065

- Observed bench host: `192.168.0.101`.
- Working transfer path: store trace CSV with SCPI, then fetch
  `gpsa/measdata/<name>.csv` over FTP.
- Do not use direct `:TRACe:DATA? TRACE1` on the RSA bench path. The project
  driver intentionally fails closed for unsafe direct trace readback.
- Use passive checks before scans:

```bash
.venv/bin/python tools/emi_control.py rsa ports --host <rsa_ip>
.venv/bin/python tools/emi_control.py rsa idn --host <rsa_ip>
```

### R&S Spectrum Rider FPH

- Remote control uses VXI-11/VISA, not the RSA FTP path.
- FPH trace export uses `TRACe:DATA?` and writes a local CSV through the repo
  driver.
- Trace readout is fixed at `711` points. Use `--points 711` on workflows that
  expose point count, and treat any other trace shape as a stop condition.
- `:INPut:GAIN:STATe OFF` is useful for setting preamp off, but preamp-state
  query behavior can be brittle. If the query times out, preserve the commanded
  state in metadata and verify from command output/front panel before trusting
  the result.
- For FPH GNSS scans, use sample detector when average detector conflicts with
  the analyzer settings:

```bash
.venv/bin/python tools/emi_control.py gnss scan gnss_fph_term_YYYYMMDD \
  --analyzer fph \
  --host <fph_ip> \
  --profile l2_passband \
  --profile l1_passband \
  --points 711 \
  --detector SAMP \
  --condition rsa_terminated \
  --external-attenuation-db 20 \
  --rsa-attenuation-db 20 \
  --no-preamp
```

The `--analyzer fph` switch is available on `re102 scan`,
`re102 system-check-scan`, `ce102 scan`, `ce102 system-check-scan`, and
`gnss scan`.

## Signal Generators

### Siglent SDG2000/SDG2042X

Use the SDG for CE102 tone injection and direct smoke checks.

- Observed host: `192.168.0.2`.
- SCPI port: `5025`.
- Use channel `C1` unless the user specifies otherwise.
- Set output load to `50 ohm`; high-Z scope checks without a 50 ohm
  terminator read roughly twice the programmed Vpp.
- Turn output off before moving cables or ending a bench session.

Use repo commands:

```bash
.venv/bin/python tools/emi_control.py ce102 system-check-plan --limit basic_28v
.venv/bin/python tools/emi_control.py ce102 system-check-tone 100000 --siggen-host <sdg_ip> --apply
```

### Rohde & Schwarz SMA100A

Use the SMA100A for RE102 CW-tone system checks and direct analyzer smoke
checks.

- Raw socket control uses TCP `5025`.
- CW frequency uses `SOURce:FREQuency:CW`.
- RF level uses `SOURce:POWer:LEVel:IMMediate:AMPLitude`.
- RF output state uses `OUTPut1:STATe`.
- Start with `*IDN?` and keep output off until the RF path and attenuation are
  confirmed.

Use repo commands:

```bash
.venv/bin/python tools/emi_control.py re102 system-check-plan --frequency-hz 100000000
.venv/bin/python tools/emi_control.py re102 system-check-tone 100000000 --siggen-host <sma100a_ip> --no-output --apply
```
