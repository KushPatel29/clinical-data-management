"""
Run Synthea and record exactly what was run.

Synthea is a Java program distributed as a jar from a rolling `master-branch-
latest` release, which means "I generated this with Synthea" is not by itself a
reproducible statement — the jar behind that tag changes. So every run writes a
provenance file next to the output holding the jar's SHA-256, the full argument
vector, the Java version, and the resulting resource counts. `metrics.json`
carries the same digest, so any number in the README can be traced to the exact
binary that produced the data behind it.

Neither Java nor the jar is vendored. The jar is 197 MB; committing it would be
committing a binary dependency to dodge a documentation problem.

    python fhir/synthea.py --population 100 --output data/synthea
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "synthea" / "synthea.properties"
DEFAULT_OUTPUT = ROOT / "data" / "synthea"

# Pinned so that population size is the only thing that varies between runs.
# `-r` fixes Synthea's reference date; without it the simulation ends at "now"
# and the same seed produces different data tomorrow.
SEED = 20260806
CLINICIAN_SEED = 20260806
REFERENCE_DATE = "20260801"
STATE = "Massachusetts"

# The eight resource types this warehouse models. Synthea emits two dozen;
# Claim and ExplanationOfBenefit alone are over half the bytes and neither is
# clinical data.
RESOURCE_TYPES = (
    "Patient",
    "Encounter",
    "Condition",
    "Observation",
    "Procedure",
    "MedicationRequest",
    "Practitioner",
    "Organization",
)


class SyntheaUnavailable(RuntimeError):
    pass


def _java() -> str:
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    found = shutil.which("java")
    if found:
        return found
    raise SyntheaUnavailable(
        "java not found. Synthea needs a JDK 11+ on PATH or JAVA_HOME set. "
        "Temurin: https://adoptium.net"
    )


def _jar() -> Path:
    configured = os.environ.get("SYNTHEA_JAR")
    if configured and Path(configured).exists():
        return Path(configured)
    for candidate in (
        ROOT / "synthea" / "synthea-with-dependencies.jar",
        Path.cwd() / "synthea-with-dependencies.jar",
    ):
        if candidate.exists():
            return candidate
    raise SyntheaUnavailable(
        "synthea-with-dependencies.jar not found. Set SYNTHEA_JAR, or download it:\n"
        "  https://github.com/synthetichealth/synthea/releases/download/"
        "master-branch-latest/synthea-with-dependencies.jar"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_command(population: int, output: Path, jar: Path, java: str) -> list[str]:
    return [
        java,
        # Synthea stamps local-time offsets into every effectiveDateTime. Pinning
        # the JVM to UTC is what stops the same seed producing different
        # timestamps on a machine in a different timezone.
        "-Duser.timezone=UTC",
        "-jar",
        str(jar),
        "-p",
        str(population),
        "-s",
        str(SEED),
        "-cs",
        str(CLINICIAN_SEED),
        "-r",
        REFERENCE_DATE,
        "-c",
        str(CONFIG),
        "--exporter.baseDirectory",
        str(output),
        STATE,
    ]


def resource_counts(fhir_dir: Path) -> dict[str, int]:
    """Line counts per modelled resource type.

    Synthea timestamps the filenames of the three provider-level resources
    (`Practitioner.1785955552260.ndjson`), so matching has to be on the prefix
    before the first dot rather than on the whole stem.
    """
    counts: dict[str, int] = {}
    for path in sorted(fhir_dir.glob("*.ndjson")):
        resource_type = path.name.split(".", 1)[0]
        if resource_type not in RESOURCE_TYPES:
            continue
        with open(path, encoding="utf-8") as handle:
            counts[resource_type] = counts.get(resource_type, 0) + sum(1 for _ in handle)
    return counts


def generate(population: int, output: Path) -> dict:
    java, jar = _java(), _jar()
    fhir_dir = output / "fhir"
    # `bulk_data.append = false` stops Synthea appending within a run, but a
    # previous run's files are still on disk and are not always overwritten —
    # a smaller population leaves the larger run's rows behind.
    if fhir_dir.exists():
        shutil.rmtree(fhir_dir)
    output.mkdir(parents=True, exist_ok=True)

    command = build_command(population, output, jar, java)
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        raise SyntheaUnavailable(
            f"synthea exited {result.returncode}\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )

    java_version = subprocess.run(
        [java, "-version"], capture_output=True, text=True
    ).stderr.splitlines()[0]

    provenance = {
        "generator": "synthea",
        "jar_path": str(jar),
        "jar_sha256": _sha256(jar),
        "jar_bytes": jar.stat().st_size,
        "java": java_version,
        "command": [c if c != java else "java" for c in command],
        "population_requested": population,
        "seed": SEED,
        "clinician_seed": CLINICIAN_SEED,
        "reference_date": REFERENCE_DATE,
        "state": STATE,
        "elapsed_seconds": round(elapsed, 1),
        "resource_counts": resource_counts(fhir_dir),
    }
    provenance["resources_total"] = sum(provenance["resource_counts"].values())
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic FHIR R4 extract.")
    parser.add_argument("--population", type=int, default=100)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        provenance = generate(args.population, args.output)
    except SyntheaUnavailable as exc:
        print(f"synthea unavailable: {exc}", file=sys.stderr)
        return 2

    print(f"synthea: {provenance['population_requested']} patients "
          f"in {provenance['elapsed_seconds']}s -> {args.output}")
    for resource_type, count in sorted(provenance["resource_counts"].items()):
        print(f"  {resource_type:<20} {count:>9,}")
    print(f"  {'TOTAL':<20} {provenance['resources_total']:>9,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
