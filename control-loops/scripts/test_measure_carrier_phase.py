"""Known-truth controls for the standalone NCO-frame measurement utility."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from measure_carrier_phase import measure_phase


class PhaseControls(unittest.TestCase):
    def setUp(self):
        duration = np.resize(np.array([4095, 4096, 4097]) / 4096000, 10003)
        self.t = np.r_[0., np.cumsum(duration[:-1])] + duration / 2
        self.count = 20

    def test_large_nco_turns_and_bpsk_signs(self):
        t = self.t
        nco = 1000 * t + 100 * np.sin(2 * np.pi * .3 * t)
        error = .15 * np.sin(2 * np.pi * .7 * t)
        signs = np.where(np.arange(len(t)) // 20 % 2, -1., 1.)
        result = measure_phase(t, signs * np.exp(1j * error), nco, self.count)
        used = len(t) // self.count * self.count
        truth = (nco + error)[:used].reshape(-1, self.count).mean(axis=-1)
        np.testing.assert_allclose(result['phase_rad'], truth, atol=1e-6, rtol=0)
        self.assertGreater(np.max(np.abs(np.diff(result['phase_rad']))), np.pi)
        self.assertEqual(int(result['discarded_tail_epochs']), 3)

    def test_seeded_noisy_null_and_injection(self):
        t = self.t
        truth = .35 * np.sin(2 * np.pi * 3 * t)
        rng = np.random.default_rng(42)
        noise = .1 * (rng.standard_normal((20, len(t))) + 1j * rng.standard_normal((20, len(t))))
        for phase in (np.zeros_like(t), truth):
            result = measure_phase(t, np.exp(1j * phase) + noise, np.zeros_like(t), 20)
            expected = phase[:10000].reshape(-1, 20).mean(axis=-1)
            self.assertLess(float(np.sqrt(np.mean((result['phase_rad'] - expected) ** 2))), .03)
            self.assertFalse(np.any(result['unwrap_branch']))

    def test_cubic_detrending_and_broadcast(self):
        t = self.t
        nco = 2 + t + .2 * t**2 + .01 * t**3
        result = measure_phase(t, np.ones((2, len(t))), nco, 1)
        self.assertEqual(result['phase_rad'].shape, (2, len(t)))
        np.testing.assert_allclose(result['residual_rad'], 0, atol=1e-12)

    def test_alias_can_look_quiet(self):
        t = np.arange(100) * .001
        # Exactly pi per epoch is invisible after BPSK squaring.
        prompt = np.exp(1j * np.pi * np.arange(len(t)))
        result = measure_phase(t, prompt, np.zeros_like(t), 1)
        self.assertLess(np.max(np.abs(result['relative_step_rad'])), 1e-12)
        self.assertLess(np.max(np.abs(result['phase_rad'])), 1e-12)
        # The utility reports an ambiguous phase, not a validity/slip certificate.

    def test_rejects_invalid_inputs_and_gaps(self):
        t = self.t
        for count in (0, -1, 1.5, True, len(t)):
            with self.subTest(count=count), self.assertRaises(ValueError):
                measure_phase(t, np.ones(len(t)), np.zeros_like(t), count)
        bad = t.copy(); bad[500:] += .05
        with self.assertRaisesRegex(ValueError, 'cadence gap'):
            measure_phase(bad, np.ones(len(t)), np.zeros_like(t), 20)
        for z, nco in ((np.ones(len(t)-1), np.zeros_like(t)),
                       (np.ones(len(t)), np.array(0.)),
                       (np.full(len(t), np.nan), np.zeros_like(t)),
                       (np.zeros(len(t)), np.zeros_like(t))):
            with self.assertRaises(ValueError):
                measure_phase(t, z, nco, 20)

    def test_cancellation_is_failure_not_a_mask(self):
        t = np.arange(20) * .001
        z = np.resize(np.array([1, 1j]), len(t))
        with self.assertRaisesRegex(ValueError, 'cancelling block'):
            measure_phase(t, z, np.zeros_like(t), 2)

    def test_cli_round_trip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / 'epochs.npz'
            output = Path(scratch) / 'phase.npz'
            np.savez(source, time_s=self.t, prompt=np.ones(len(self.t)), nco_phase_rad=np.zeros_like(self.t))
            command = [sys.executable, str(Path(__file__).with_name('measure_carrier_phase.py')),
                       str(source), str(output), '--block-epochs', '20']
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            with np.load(output, allow_pickle=False) as data:
                self.assertEqual(data['time_s'].size, 500)
                self.assertEqual(json.loads(str(data['metadata_json']))['phase_units'], 'radians')
            original = output.read_bytes()
            rerun = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(rerun.returncode, 0)
            self.assertEqual(original, output.read_bytes())


if __name__ == '__main__':
    unittest.main()
