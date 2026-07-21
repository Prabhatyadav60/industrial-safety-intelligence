# Eval Results: Compound Engine vs. Naive Single-Sensor Baseline

This is a real, reproducible measurement — not an estimate. It was produced by
running `engine/eval.py`, which:

1. Plays the scripted Vizag-pattern incident (`simulator/scenario.py`) into a
   dedicated `<db>_eval` MongoDB database (never touches live demo data).
2. Replays the **actual production code** — `engine/risk_rules.py`'s
   `compute_compound_score()` and `engine/baseline.py`'s `naive_baseline_flag()`
   — point-in-time over every generated sensor reading, using the permits,
   maintenance jobs, and shift events that were genuinely active at each
   historical timestamp.
3. Records the first timestamp each detector fires.

Reproduce it yourself:

```bash
.venv/bin/python engine/eval.py
```

## Scenario setup

In the Gas Cleaning Plant zone (Z1 — modeled on the RINL Visakhapatnam Steel
Plant GCP, Jan 2025):

- A **confined-space entry permit** and a **hot-work permit** are both issued
  at t=0 (repair work inside the gas-cleaning duct).
- An **extraction-fan maintenance job** starts at t=0 — this is *why*
  ventilation degrades over the incident.
- Gas concentration ramps linearly from 5% LEL to 65% LEL across the scenario.
- Duct pressure drifts increasingly abnormal (extraction fault) alongside the
  gas rise.
- A **shift changeover** is logged partway through the incident.
- 90 ticks, simulated at 20x real time (~60 simulated minutes total).

Every one of these signals is individually unremarkable for most of the
window — that's the point. A permit being active isn't news. Gas at 30% LEL
isn't an alarm. A shift changeover happens every shift. It's the
**combination**, in the **same zone**, at the **same time**, that matters.

## Results (from the actual run, `eval_output/latest_run.json`)

| Metric | Value |
|---|---|
| **(a) Compound engine first flags RED** | simulated t ≈ 25.3 min into the incident (score 75/100) |
| Triggered rules at that moment | `hot_work_permit_active + gas 30.9% LEL > 15.0% LEL trigger`; `confined_space_permit_active + pressure deviation 3.1 kPa > 3.0 kPa` |
| **(b) Naive single-sensor baseline fires** | simulated t ≈ 44.7 min into the incident, when gas alone crosses 50% LEL (measured: 50.56%) |
| **(c) Lead time gained by the compound engine** | **19.3 minutes** |

At the moment the compound engine already flags RED, the single gas sensor
alone is reading 30.9% LEL — nowhere near its 50% LEL high-alarm threshold. A
conventional single-sensor gas panel would show nothing out of the ordinary
for another ~19 minutes.

## Why this matters

19 minutes is not a rounding error in an industrial incident timeline — it's
the difference between a scheduled permit review and an evacuation. The
compound engine catches this specific pattern not because it has a better gas
sensor, but because it's the only thing in the plant's safety stack actually
looking at permits, maintenance state, and shift changes *together* with the
sensor feed, the way the Vizag GCP incident (and near-misses like it) actually
unfold.

## Caveats

- These are simulated numbers from a scripted, controlled incident timeline,
  not a retrospective replay of real plant telemetry — the goal is to
  demonstrate the *mechanism* (compound correlation catches what single-sensor
  thresholds can't) with a realistic, OISD-convention-based threshold model,
  not to claim a validated real-world lead time for any specific plant.
- The rule weights and thresholds (`thresholds.json`) are transparent and
  tunable; the qualitative result (compound detection precedes single-sensor
  detection whenever multiple sub-threshold risk factors compound) holds
  across a wide range of reasonable weight choices for this scenario shape.
- Re-running `eval.py` reproduces the same structure but with fresh random
  noise on top of the deterministic ramp, so the exact minute figure can vary
  by a small amount run-to-run; see `eval_output/latest_run.json` for the
  numbers from the run this document reports.
