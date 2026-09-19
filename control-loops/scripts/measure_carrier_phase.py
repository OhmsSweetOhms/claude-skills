"""BPSK carrier phase from continuous, already selected prompt epochs.

NumPy-only extraction of the validated NCO-frame estimator. No receiver,
packet, SciPy, plotting, or test-helper imports. See ../references/phase-measurement.md.
"""
import argparse
import json
from pathlib import Path

import numpy as np


def measure_phase(time_s, prompt, nco_phase_rad, block_epochs, *, max_gap_s=None):
    """Return block phase, cubic residual and diagnostics; final axis is time.

    Prompt = amplitude * exp(1j * (signal_phase - applied_nco_phase)).
    NCO phase must be continuous and sampled at each actual dump midpoint.
    Leading prompt axes are independent channels/trials; NCO broadcasts to them.
    Caller must split at resets/state changes; cadence checks cannot find all gaps.
    """
    t = np.asarray(time_s, dtype=float)
    z = np.asarray(prompt, dtype=complex)
    nco = np.asarray(nco_phase_rad, dtype=float)
    if isinstance(block_epochs, (bool, np.bool_)) or not isinstance(block_epochs, (int, np.integer)) or block_epochs < 1:
        raise ValueError('block_epochs must be a positive integer')
    if t.ndim != 1 or z.ndim < 1 or z.shape[-1] != t.size or t.size < 4 * block_epochs:
        raise ValueError('time must be 1-D and match prompt final axis, with at least four complete blocks')
    if nco.ndim < 1 or nco.shape[-1] != t.size:
        raise ValueError('NCO must have the same final time axis')
    try:
        nco = np.broadcast_to(nco, z.shape)
    except ValueError as exc:
        raise ValueError('NCO must broadcast to prompt shape') from exc
    if not all(np.all(np.isfinite(a)) for a in (t, z, nco)):
        raise ValueError('inputs must be finite')
    dt = np.diff(t)
    limit = 1.5 * np.median(dt) if max_gap_s is None else float(max_gap_s)
    if not np.isfinite(limit) or limit <= 0 or np.any(dt <= 0) or np.any(dt > limit):
        raise ValueError('non-increasing time or cadence gap: select continuous segments separately')
    used = t.size // block_epochs * block_epochs
    # A constant scale per trace prevents overflow without changing power weights.
    scale = np.max(np.abs(z), axis=-1, keepdims=True)
    if np.any(scale == 0) or not np.all(np.isfinite(scale)):
        raise ValueError('zero or unrepresentable prompt amplitude')
    squared = ((z[..., :used] / scale) ** 2).reshape(*z.shape[:-1], -1, block_epochs)
    average = squared.mean(axis=-1)
    power = np.abs(squared).mean(axis=-1)
    if np.any(np.abs(average) <= 1e-12 * power) or np.any(power == 0):
        raise ValueError('undefined circular phase in a zero-power or cancelling block')
    block_t = t[:used].reshape(-1, block_epochs).mean(axis=-1)
    principal2 = np.angle(average)
    relative = 0.5 * np.unwrap(principal2, axis=-1)
    nco_block = nco[..., :used].reshape(*z.shape[:-1], -1, block_epochs).mean(axis=-1)
    phase = nco_block + relative  # Never unwrap this restored phase.
    x = (block_t - block_t.mean()) / np.ptp(block_t)
    q, _ = np.linalg.qr(np.column_stack([x ** k for k in range(4)]))
    residual = phase - phase @ q @ q.T
    return dict(time_s=block_t, phase_rad=phase, residual_rad=residual,
                relative_phase_rad=relative,
                unwrap_branch=np.rint((2 * relative - principal2) / (2 * np.pi)).astype(np.int64),
                relative_step_rad=np.diff(relative, axis=-1),
                coherence=np.abs(average) / power,
                discarded_tail_epochs=np.array(t.size - used),
                block_epochs=np.array(block_epochs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='NPZ: time_s, prompt (complex), nco_phase_rad')
    parser.add_argument('output', type=Path, help='new NPZ, never overwrite evidence')
    parser.add_argument('--block-epochs', type=int, required=True)
    parser.add_argument('--max-gap-s', type=float, help='maximum midpoint spacing; default 1.5 times median')
    args = parser.parse_args()
    with np.load(args.input, allow_pickle=False) as data:
        result = measure_phase(data['time_s'], data['prompt'], data['nco_phase_rad'],
                               args.block_epochs, max_gap_s=args.max_gap_s)
    metadata = dict(estimator='nco-frame-bpsk-circular', detrend_degree=3,
                    phase_units='radians', ambiguity='unknown integer multiple of pi',
                    validity='requires matched null/injection controls; diagnostics are not a cycle-slip proof')
    with args.output.open('xb') as stream:
        np.savez_compressed(stream, **result, metadata_json=json.dumps(metadata))
    print(json.dumps(dict(blocks=int(result['time_s'].size),
                          discarded_tail_epochs=int(result['discarded_tail_epochs']),
                          coherence_min=float(np.min(result['coherence'])))))


if __name__ == '__main__':
    main()
