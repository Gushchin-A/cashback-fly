"""Fetch released MaleCNS inputs and verify both sources and prepared arrays."""

import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

import numpy as np

from .common import DATA, GRAPH, digest

PACKAGE = Path(__file__).parent


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify():
    lock = json.loads((PACKAGE / "sources.lock.json").read_text())
    if sha(DATA / "annotations.feather") != lock["annotations.feather"]["sha256"]:
        raise RuntimeError("Annotation checksum mismatch")
    expected = json.loads((PACKAGE / "arrays.lock.json").read_text())
    with np.load(GRAPH, allow_pickle=False) as a:
        if set(a.files) != set(expected):
            raise RuntimeError("Graph fields mismatch")
        for k, h in expected.items():
            if digest(a[k]) != h:
                raise RuntimeError("Graph array checksum mismatch: " + k)
        if len(a["ids"]) != 166700 or len(a["post"]) != 25582938:
            raise RuntimeError("Wrong retained graph")
    import pyarrow.feather as f

    # Match normalized transmitter identities to the checksum-locked released file.
    if not (DATA / "normalized/neurons.feather").exists():
        raise RuntimeError("Normalized neuron metadata missing")
    n = f.read_table(DATA / "normalized/neurons.feather").to_pandas()
    transmitter_values = json.dumps(
        n.neurotransmitter.fillna("").astype(str).tolist(), separators=(",", ":")
    ).encode()
    nt_expected = json.loads((PACKAGE / "neurons.lock.json").read_text())[
        "neurotransmitter_values_sha256"
    ]
    if hashlib.sha256(transmitter_values).hexdigest() != nt_expected:
        raise RuntimeError("Normalized transmitter values mismatch")
    with np.load(GRAPH, allow_pickle=False) as a:
        if not np.array_equal(n.source_id.to_numpy(), a["ids"]):
            raise RuntimeError("Normalized neuron order mismatch")
    return {
        "release": "MaleCNS v1.0",
        "neurons": 166700,
        "directed_edges": 25582938,
        "arrays_verified": True,
    }


def _progress(name, size):
    state = {"last": -1}

    def hook(block, block_size, total):
        if total <= 0:
            return
        done = min(block * block_size, total)
        percent = int(100 * done / total)
        if percent != state["last"] and percent % 5 == 0:
            state["last"] = percent
            print(
                f"  {name}: {percent:3d}%  {done / 1e6:.0f}/{total / 1e6:.0f} MB",
                flush=True,
            )

    del size
    return hook


def prepare(reuse=None, offline=False):
    """Fetch, verify and compile the graph.

    Files already present on disk are never re-downloaded: only their SHA-256 is
    checked against the lock. That is what lets a VPS receive the dataset out of
    band (uploaded directly) and still pass the same integrity gate. With
    ``offline`` a missing file is an error instead of a fetch.
    """
    DATA.mkdir(parents=True, exist_ok=True)
    if reuse:
        root = Path(reuse)
        mapping = {
            root / "outputs/doom/malecns_v1/graph.npz": GRAPH,
            root / "connectome_data/malecns_v1/annotations.feather": DATA
            / "annotations.feather",
            root / "connectome_data/malecns_v1/normalized/neurons.feather": DATA
            / "normalized/neurons.feather",
        }
        for source, target in mapping.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    else:
        lock = json.loads((PACKAGE / "sources.lock.json").read_text())
        for name, info in lock.items():
            path = DATA / name
            if not path.exists():
                if offline:
                    raise RuntimeError(
                        f"Offline mode: place {name} in {DATA} before preparing"
                    )
                print(f"Downloading {name} ({info['bytes'] / 1e6:.0f} MB)", flush=True)
                tmp = path.with_suffix(".partial")
                urllib.request.urlretrieve(
                    info["url"], tmp, reporthook=_progress(name, info["bytes"])
                )
                if sha(tmp) != info["sha256"]:
                    raise RuntimeError("Downloaded checksum mismatch: " + name)
                tmp.replace(path)
            print(f"Verifying {name}", flush=True)
            if sha(path) != info["sha256"]:
                raise RuntimeError("Source checksum mismatch: " + name)
        shutil.copyfile(PACKAGE / "sources.lock.json", DATA / "source.lock.json")
        from .connectome import import_graph
        from .prepare import prepare as compile_graph

        print("Importing retained graph", flush=True)
        import_graph()
        print("Compiling CSR arrays and retinal projection", flush=True)
        compile_graph()
    print(json.dumps(verify()), flush=True)


def main():
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "verify"])
    p.add_argument("--reuse", type=Path, help="Reuse a verified DOOMFLY working copy")
    p.add_argument(
        "--offline",
        action="store_true",
        help="Require the released files to be on disk already; never fetch",
    )
    a = p.parse_args()
    if a.command == "verify":
        print(json.dumps(verify(), indent=2))
    else:
        prepare(a.reuse, a.offline)


if __name__ == "__main__":
    main()
