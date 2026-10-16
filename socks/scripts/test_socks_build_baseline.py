#!/usr/bin/env python3
"""
Tests for socks_build.baseline_index: the collision rule and the recipe's
baseline pin (stages.hdl_no_os.baseline).

Run:  python3 scripts/test_socks_build_baseline.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_build import baseline_index

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


def bank(root, rel_dir, name, sha, mode=None):
    """Write one <name>.build-manifest.json under platforms/<rel_dir>/."""
    d = os.path.join(root, "platforms", rel_dir)
    os.makedirs(d, exist_ok=True)
    doc = {"schema": "build-manifest/1", "artifact": {"file": name, "sha256": sha}}
    if mode:
        doc["build"] = {"reproducibility": {"mode": mode}}
    with open(os.path.join(d, f"{name}.build-manifest.json"), "w") as fh:
        json.dump(doc, fh)


PROFILE = "platforms/profiles/p"
OTHER = "platforms/profiles/q"


def main():
    with tempfile.TemporaryDirectory() as root:
        # The building profile banks one bundle per respin; another profile
        # homes the same artifact name once, and a kernel Image once.
        bank(root, "profiles/p/hdl/respin-a", "system_top.bit", "a" * 64)
        bank(root, "profiles/p/hdl/respin-b", "system_top.bit", "b" * 64,
             mode="load-equivalent-unverified")
        bank(root, "profiles/q/hdl/only", "system_top.bit", "q" * 64)
        bank(root, "profiles/q/linux/kernel", "Image", "k" * 64)

        print("no pin: today's rule")
        idx = baseline_index(root, PROFILE)
        check("two in-profile manifests are ambiguous without a pin",
              "ambiguous" in idx["system_top.bit"]
              and len(idx["system_top.bit"]["ambiguous"]) == 3,
              str(idx["system_top.bit"]))
        check("a globally unique name is its one manifest",
              idx["Image"]["sha256"] == "k" * 64)

        print("pin names a banked run-label")
        idx = baseline_index(root, PROFILE, PROFILE + "/hdl/respin-b")
        check("the pinned manifest is the baseline",
              idx["system_top.bit"].get("sha256") == "b" * 64, str(idx["system_top.bit"]))
        check("the pinned manifest keeps its mode",
              idx["system_top.bit"].get("mode") == "load-equivalent-unverified")
        check("a name the profile does not bank follows today's rule under a pin",
              idx["Image"]["sha256"] == "k" * 64)

        print("pin names a run-label with no manifests yet")
        idx = baseline_index(root, PROFILE, PROFILE + "/hdl/image-1")
        check("the name is marked unpinned, not ambiguous",
              "unpinned" in idx["system_top.bit"], str(idx["system_top.bit"]))
        check("unpinned lists only the profile's own manifests as history",
              sorted(idx["system_top.bit"]["unpinned"]) == sorted([
                  "platforms/profiles/p/hdl/respin-a/system_top.bit.build-manifest.json",
                  "platforms/profiles/p/hdl/respin-b/system_top.bit.build-manifest.json"]),
              str(idx["system_top.bit"]["unpinned"]))

        print("pin with the other profile's claimant still present")
        # Drop respin-a so the profile banks exactly one; the pin still wins
        # over q's claimant.
        os.remove(os.path.join(root, "platforms/profiles/p/hdl/respin-a",
                               "system_top.bit.build-manifest.json"))
        idx = baseline_index(root, PROFILE, PROFILE + "/hdl/respin-b")
        check("the pinned manifest wins over another profile's",
              idx["system_top.bit"].get("sha256") == "b" * 64, str(idx["system_top.bit"]))

        print("pin names a label the profile has not banked, nothing else banked")
        os.remove(os.path.join(root, "platforms/profiles/p/hdl/respin-b",
                               "system_top.bit.build-manifest.json"))
        idx = baseline_index(root, PROFILE, PROFILE + "/hdl/image-1")
        check("only the other profile's manifest remains, so it is the one entry",
              idx["system_top.bit"].get("sha256") == "q" * 64, str(idx["system_top.bit"]))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
