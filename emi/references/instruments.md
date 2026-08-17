# Instrument Reference

> Verb-by-verb flags for everything below: repo `docs/cli-cookbook.md`.

Use the EMI repo for exact driver behavior and command implementations:

- `emi/instruments/rsa5000.py`
- `emi/instruments/fph.py`
- `emi/instruments/sdg2000x.py`
- `emi/instruments/sma100a.py`
- `emi/instruments/ngp800.py`
- `emi/instruments/owon_odp.py`
- `tools/emi_control.py`
- `README.md` (current bench hosts, per-instrument CLI usage)

## Analyzer Selection

The repo supports RSA and FPH analyzer backends for swept trace workflows.
Analyzer substitution changes safe transfer paths, point counts, detector
choices, and verification checks.

### Rigol RSA5000/RSA5065

- Bench host, serial, and firmware: see repo `README.md` "Current Bench Unit".
- Working transfer path: store trace CSV with SCPI, then fetch
  `gpsa/measdata/<name>.csv` over FTP.
- Do not use direct `:TRACe:DATA? TRACE1` on the RSA bench path. The project
  driver intentionally fails closed for unsafe direct trace readback.
- Use passive checks before scans:

```bash
.venv/bin/python tools/emi_control.py rsa ports --host <rsa_ip>
.venv/bin/python tools/emi_control.py rsa idn --host <rsa_ip>
```

Bench-proven quirks on firmware `00.01.03` (2026-05 sessions; do not
re-derive on the bench):

- Do not gate bring-up on `sa.errors()`: `:SYSTem:ERRor?` and its variants
  (`:NEXT?`, `:ALL?`, `:COUNt?`) time out on this unit. Use `*STB?` or the
  front panel.
- `:INITiate:IMMediate` is invalid as a swept-SA single trigger (per the
  programming guide); the driver uses documented sweep/`*OPC?` handling.
- Do not switch into RTSA during RE102/CE102 work. `:INSTrument:NSELect 1`
  restored SA, but the mode change exposed fragile remote-I/O behaviour.
- The LAN service group is fragile: a light HTTP/FTP probe or the unsafe
  trace query can leave HTTP (80), VXI-11 (111), and raw SCPI (5555)
  refusing connections while ICMP, FTP (21), and SSH (22) stay up. Recovery
  without a power cycle: SSH in and run
  `sh /mnt/app/app-config >/tmp/app-config.restart.log 2>&1 &`; the
  services reopen after ~30–60 s (relay clicks are normal). Use
  `rsa ports` (or `tools/rsa_export_probe.py --ports-only`) to check service
  health without entering VISA remote mode.
- Any pyvisa/VXI-11 access puts the front panel in remote mode; physical
  **Esc** returns local control (see the tracker for the unverified VXI-11
  `device_local` alternative).
- FTP login lands in `/mnt/user`; use relative `gpsa/measdata/<name>.csv`
  (absolute `/gpsa/...` fails). SSH offers only legacy `ssh-rsa`/`ssh-dss`
  host keys (`-o HostKeyAlgorithms=+ssh-rsa,ssh-dss`). The service
  credential default lives in the repo driver; MSO5000 field credentials do
  not carry over. Do not copy credentials into this skill.
- After an app restart the analyzer may come back with 50 dB attenuation —
  re-check attenuation/preamp before comparing levels.
- GPSA stitched-band scanning is the chosen method (works without the
  RSA5000-EMI license); EMI-mode FSCan, analyzer limit lines, quasi-peak,
  binary trace transfer, external 10 MHz reference, FMT trigger, and RTSA IQ
  keyword verification are deferred backlog in the repo
  `docs/emi_tracker.json` (items 26–32).

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

- Bench host: see repo `README.md`.
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

## Power Supplies

### R&S NGP800 and OWON ODP

Both are driven over raw TCP SCPI (`ngp800` on 5025, `odp` on 3000; hosts in
the repo `README.md`) through deliberately narrow drivers: identity/snapshot
queries plus one per-channel output on/off operation. No resets, no
voltage/current setpoint writes, no remote/local mode changes, no arbitrary
or compound SCPI. Widening that boundary is an operator decision recorded in
the repo `ORCHESTRATOR-CACHE.md`, not something to do mid-session.

```bash
.venv/bin/python tools/emi_control.py ngp800 ports|idn|snapshot|output ...
.venv/bin/python tools/emi_control.py odp ports|idn|snapshot|output ...
```
