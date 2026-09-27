# AGENTS.md

Guidance for anyone (human or agent) working in this repository.

## What this is

A live simulation of a fly's brain (full MaleCNS v1.0 connectome) picking
cashback categories, rendered as a bank-app mockup. See `README.md` for the
product framing and the decision/visualization rationale. This file is about
the code, not the product.

## Architecture

```
neural/    connectome loading, odor encoding, native spiking kernel (Python + C++)
worker/    the always-running simulation process; writes JSON snapshots to disk
config/    rooms, categories, interests, simulation timing — data, not code
web/       TypeScript + Three.js frontend, polls the worker's snapshots over HTTP
tools/     benchmarking/calibration scripts (not part of the runtime path)
tests/     pytest suite for neural/ and worker/
docs/      per-stage measurement reports
reports/   raw JSON backing those reports
```

There is no application server. `worker/` writes files; nginx serves
`web/dist/` as the site root and the worker's `snapshots/` directory as
`/api/`. The browser only ever does `GET` polling — no endpoint accepts
writes from a client.

## Data flow

1. `config/rooms.json` lists rooms with `enabled: true/false`. The worker
   starts one thread per enabled room; the room count is never hardcoded.
2. Each room runs `neural.flybrain.FlyBrain`: a category id is hashed into
   an odor pattern, boosted if it matches one of the room's declared
   interests, then read out from the lateral horn over a fixed window.
   Two sweeps through the month's offers fill five slots by rank.
3. `worker/snapshot.py` writes `snapshots/rooms/{id}/snapshot.json` once a
   second, atomically (temp file, `fsync`, `os.replace` — a reader must
   never see a partial file).
4. `web/src/api.ts` polls that same file once a second and renders
   whatever it contains. The client never computes or guesses a selection
   ahead of `selection.selected` in the snapshot.

## Running it

Requires Python 3.11 and a C++17 compiler (`g++`/`clang++`) on `PATH` —
`neural/kernel.cpp` is compiled on first run and loaded via `ctypes`.
Dependencies are pinned in `pyproject.toml` / `uv.lock`; do not add a
dependency without pinning it there.

```sh
uv sync --dev                             # creates .venv, installs pinned deps
.venv/bin/python -m neural.fetch prepare   # downloads + verifies the connectome
export OPENBLAS_NUM_THREADS=1             # required, or BLAS steals sim cores
.venv/bin/python -m worker.main --once    # one cycle, then exit
.venv/bin/python -m worker.main           # keep broadcasting until Ctrl-C

.venv/bin/python -m pytest tests/ -q                  # fast subset
FLY_FULL_TEST=1 .venv/bin/python -m pytest tests/ -q  # + full-connectome tests
```

```sh
cd web
npm install
npm run dev          # vite dev server, expects the worker's snapshots/ to exist
npm run build         # tsc --noEmit + vite build -> web/dist/
```

## Invariants — do not break these

1. **No hardcoded outcome mapping.** There must be no code path of the
   shape `if interest == X: pick(Y)`. Interests only scale odor amplitude
   via `interest_gain`; `test_unit_gain_makes_interests_irrelevant` fails
   the build if `interest_gain = 1.0` ever produces interest-dependent
   results.
2. **No pre-recorded or synthetic results.** Every number in a snapshot
   comes from stepping the live simulation for that room. Nothing in
   `snapshots/` or `checkpoints/` is committed to git (see `.gitignore`) —
   it is runtime state, regenerated on every run.
3. **Full connectome, not a subset.** `connectome_data/` (~1.6GB, also
   gitignored) is used whole; there is no pruning for performance.
4. **The client never writes.** Room switching only changes which
   snapshot URL the browser polls. There is no endpoint that lets a
   viewer influence the computation.
5. **Room/category config is data, not code.** Adding a room means adding
   an entry to `config/rooms.json` (and, if new categories/interests are
   needed, to `config/categories.json` / `config/interests.json`) — never
   a code change in `neural/` or `worker/`.
6. **Snapshot writes are atomic.** Any change to `worker/snapshot.py` must
   preserve the temp-file + `fsync` + `os.replace` pattern; a reader must
   never observe a half-written file.

## Frontend conventions

- Panels only render what a snapshot already contains — no client-side
  guessing of the outcome, no decorative numbers.
- CSS breakpoints: 1120px and 780px (see `web/src/styles/base.css`).
  Mobile-specific rules live inside the 780px media query, not as
  separate stylesheets.
- Comments explain *why* (a non-obvious constraint, a measured tradeoff,
  a licensing limitation), not *what* — the code itself should make the
  "what" obvious. Do not restore comments describing development history
  or requests; if a decision needs a paper trail, it belongs in `docs/`,
  not in a code comment.

## Licensing notes relevant to the code

- `neural/` is taken from a stonkfly clone, MIT-licensed; license and
  attribution are kept in `neural/LICENSE.stonkfly-MIT` and
  `neural/THIRD_PARTY.stonkfly.md`. Do not strip that attribution when
  touching `neural/`.
- The connectome (MaleCNS v1.0) is CC BY 4.0 — cite the dataset and its
  paper if publishing results derived from it (see `neural/datasets.json`).
- `web/public/fonts/` is gitignored: any proprietary font binaries are
  placed on the server directly, not committed.
