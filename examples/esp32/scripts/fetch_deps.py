#!/usr/bin/env python3
"""Fetch exact, hash-verified QUIC/TLS sources; no global installation."""
import hashlib
import io
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / "third_party"
DEPS = {
    "ngtcp2": ("ngtcp2/ngtcp2", "7dd482f949f145632c482d01af2184954b471795",
               "bfff0764112f4d7a22b71b20bbb0b3dc6d2d421f467567fc1a1aa07953d6249c"),
    "wolfssl": ("wolfSSL/wolfssl", "decea12e223869c8f8f3ab5a53dc90b69f436eb2",
                "7a5ca6f13a58a93fb2eb361ff3f556867ee9da1c0f395098c4b7b0af292366d4"),
}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, (repo, commit, sha256) in DEPS.items():
        destination = ROOT / name
        marker = destination / ".cgp-revision"
        if marker.exists() and marker.read_text().strip() == commit:
            print(f"{name}: {commit} already fetched")
            continue
        if destination.exists():
            raise SystemExit(f"Refusing to replace {destination}; move it aside first")
        url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
        print(f"Fetching {name}: {commit}", flush=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise SystemExit(f"Archive checksum mismatch for {name}")
        with tempfile.TemporaryDirectory(prefix=".fetch-", dir=ROOT) as temp:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                # Check every path and reject links/devices before extraction.
                for member in archive.getmembers():
                    path = Path(member.name)
                    if path.is_absolute() or ".." in path.parts or not (
                        member.isfile() or member.isdir()
                    ):
                        raise SystemExit(f"Unsafe archive member: {member.name}")
                archive.extractall(temp)
            extracted = list(Path(temp).iterdir())
            if len(extracted) != 1 or not extracted[0].is_dir():
                raise SystemExit(f"Unexpected archive layout for {name}")
            shutil.move(str(extracted[0]), destination)
        marker.write_text(commit + "\n")
    print("Dependencies ready.")


if __name__ == "__main__":
    main()
