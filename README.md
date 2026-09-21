# CASHBACKFLY

A single simulated fly picks five cashback categories a month, live, by
running its actual brain — the full **MaleCNS v1.0** connectome
(166,700 neurons, 25.6M directed connections) — instead of any recommendation
algorithm. The frontend renders the fly, the connectome activity, and the
category list as a bank-app mockup.

There is no scripted or pre-recorded outcome anywhere in this repository.
The worker computes the simulation continuously and writes snapshots to
disk; the browser polls a snapshot once a second. Nothing the viewer does
feeds back into the computation — switching rooms only changes which
snapshot file the client reads.

## How the selection works

Each cashback category has a fixed id (e.g. `supermarkety`, `kinoteatry`).
The id is hashed deterministically (`blake2b`) into a stimulation pattern
over a small set of olfactory glomeruli — the same category id always
produces the same odor pattern, run after run. If a category's tags match
one of the fly's declared interests, the odor's amplitude is boosted by a
fixed `interest_gain`; that is the *only* mechanism by which interests
influence the outcome. There is no `if interest == X: pick(Y)` anywhere in
the codebase — a dedicated test (`test_unit_gain_makes_interests_irrelevant`)
asserts that with `interest_gain = 1.0` two rooms with different interests
must select identically.

The decision itself is read from the **lateral horn** — the innate,
non-learned odor-valence area of the fly brain — rather than from the
mushroom body (the learning/memory area) or from descending neurons.
This was not an aesthetic choice: it came out of measurement. An early
attempt read descending neurons and produced near-pure noise
(reproducibility ≈ 0.01) because a single stimulus pushed the network into
a self-sustaining state that didn't decay even after 1.6s of silence. A
sweep across candidate populations and window lengths found the lateral
horn on a 200ms window gives F ≈ 68.5 with reproducibility 0.944 — by far
the most stable, discriminative signal available, and the biologically
sensible one, since synaptic plasticity is disabled (the fly does not
learn) and the lateral horn is exactly the circuit that scores odor
valence innately. Categories are ranked by mean lateral-horn firing rate
over that window; after two sweeps through the month's offers, the five
highest-ranked categories fill the slots in order.

## Why the neural-activity map is normalized per neuron

The right-hand panel renders a heat map of ~7,000 sampled neurons colored
by activity. The first version normalized every frame against that frame's
own peak activity. It looked alive, but the same bright cluster held still
across categories that had nothing in common. Checking the top-40 most
active neurons between frames from different categories showed 91%
overlap — and cross-referencing the connectome graph directly confirmed
why: those neurons are structural hubs (median in-degree 561 vs. 133 for
the full CNS sample, 94th–100th percentile of connectivity). A hub lights
up for almost any stimulus, so normalizing against the frame's peak was
mostly showing connectivity, not a response to the specific category.

The fix keeps every number real — no value is invented or drawn over the
data — but changes what each neuron is normalized against: each neuron
tracks its own rolling 95th percentile of activity and is colored relative
to *that*, not to whatever the busiest neuron in the frame happens to be.
Top-40 overlap between unrelated categories dropped from 91% to 24%. The
technique (per-signal rolling-percentile normalization instead of a
frame-global peak) is adapted from `flyhard`'s `cns_view.py` (MIT,
attribution kept in `web/src/panels.ts`).

## Stack

- **`neural/`** — Python 3.11, connectome loading, odor encoding, and a
  native spiking kernel: `neural/kernel.cpp` is compiled on first run
  (`-O3 -std=c++17 -shared -fPIC`, cached by source hash) and loaded via
  `ctypes` — a C++17 compiler must be present on the machine. No web
  framework, no database.
- **`worker/`** — Python, one process, one thread per enabled room. Steps
  the simulation continuously and writes a JSON snapshot per room to disk
  once a second, atomically (temp file + `fsync` + `os.replace`).
- **`web/`** — TypeScript + Three.js, built by Vite into static assets.
  No server-side rendering; the browser polls the worker's snapshot files
  over plain HTTP once a second.
- **Serving** — nginx serves `web/dist/` as the site root and the
  worker's `snapshots/` directory as `/api/`. There is no application
  server in front of either.

## Running it locally

Requires Python 3.11 and a C++17 compiler (`g++`/`clang++`) on `PATH`.
Dependencies are pinned in `pyproject.toml` / `uv.lock`.

```sh
uv sync --dev                         # creates .venv, installs pinned deps
python -m neural.fetch prepare        # downloads the connectome, verifies checksums
export OPENBLAS_NUM_THREADS=1         # required — otherwise BLAS steals cores from the sim
.venv/bin/python -m worker.main --once  # one full cycle and exit
.venv/bin/python -m worker.main         # keep broadcasting snapshots until Ctrl-C
```

Tests:

```sh
.venv/bin/python -m pytest tests/ -q                  # fast subset
FLY_FULL_TEST=1 .venv/bin/python -m pytest tests/ -q   # + full-graph tests (~5 min)
```

Frontend (from `web/`), pointed at the snapshots the worker is writing:

```sh
npm install
npm run dev
```

## Repo structure

```
neural/      connectome loading, odor encoding, spiking kernel (adapted from
             stonkfly, MIT — see neural/LICENSE.stonkfly-MIT)
worker/      the always-running simulation process + atomic snapshot writer
config/      room list, categories, interests, simulation timing — all data,
             no code changes needed to add rooms or change timing
tools/       one-off benchmarking and calibration scripts used to derive the
             constants in config/ (decoder choice, interest_gain, cycle length)
tests/       pytest suite; FLY_FULL_TEST=1 gates the slow full-connectome tests
web/         TypeScript + Three.js frontend, built by Vite
docs/        per-stage measurement reports referenced above
reports/     raw JSON output backing those reports
```

Room count and each room's declared interests are config (`config/rooms.json`),
not code — enabling a second or third room is a config change, not a
refactor. Only one room is enabled in production today.

## Hard invariants

- No hardcoded outcome mapping. Interests only scale stimulus amplitude;
  they never select a category directly.
- No pre-recorded results. Every number in a snapshot comes from stepping
  the live simulation.
- The full connectome graph is used — no pruning for performance.
- The viewer cannot influence the computation. Switching rooms only
  changes which snapshot URL the client polls.

## Disclaimers

The wiring is the real MaleCNS v1.0 connectome. The dynamics are an
approximation (a spiking model calibrated against measured behavior, not
a wet-lab recording of this exact task). No living fly is involved. There
is no cryptocurrency token of any kind associated with this project.

## References

Code taken with attribution:

- **[stonkfly](https://github.com/nftechie/stonkfly)** (MIT) — `neural/`
  is taken from stonkfly's connectome/odor/spiking module as-is, with only
  the data path adapted. License preserved in `neural/LICENSE.stonkfly-MIT`,
  full attribution in `neural/THIRD_PARTY.stonkfly.md`.
- **[doomfly](https://github.com/nftechie/doomfly)** (MIT) — stonkfly's own
  connectome importer, visual-projection, and spiking-kernel code is in turn
  adapted from doomfly; this project inherits that lineage through `neural/`.
- **[flyhard](https://github.com/MarkUnthank/flyhard)** (MIT) — the
  per-neuron rolling-percentile normalization used in the activity heat map
  (`web/src/panels.ts`) is adapted from `flyhard/src/flyhard/cns_view.py`,
  attributed inline in the code.
- **[MaleCNS v1.0](https://male-cns.janelia.org/)** — the connectome dataset
  itself (Creative Commons Attribution 4.0). See `neural/datasets.json` for
  release URLs and checksums.

Visual/genre inspiration only, no code taken:

- **[FlyBrain](https://github.com/nftechie/FlyBrain)** — a connectome-driven
  video game boss; informed the "a real connectome, watched live" framing
  of this project, no code shared.
- **stonkfly** and **FlyTok**'s public frontends were also used as visual
  reference for the fly's 3D model and the dark, panel-based dashboard
  layout — the geometry-building approach and motion envelope (fast attack,
  slow decay) were observed and re-implemented independently, not copied.
