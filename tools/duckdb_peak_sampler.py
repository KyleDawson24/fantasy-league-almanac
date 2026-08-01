"""Whole-run peak for DuckDB's own memory counters.

WHY THIS EXISTS. `duckdb_profiled` writes DuckDB's JSON profile, whose root
carries `system_peak_buffer_memory` and `system_peak_temp_dir_size`. The
profile is REWRITTEN per query, so the file a finished run leaves behind
holds one query's numbers, not the run's -- the audit records a trailing
`not_null` test reporting 0.01 GiB for a model that had just been OOMing.

`profiles.yml` claimed the opposite: "CUMULATIVE for the connection ... so
the last profile a run writes holds the whole-run peak". Both halves were
measured -- a heavy query then a trivial one on the SAME cursor, then the
trivial one on a SECOND cursor of the same database instance -- and the
truth is in between:

  * cumulative WITHIN a cursor -- a trailing `SELECT 1` does NOT erase it;
  * but scoped PER CURSOR, not per database instance, despite the
    `SYSTEM_` prefix. A second cursor on the same instance reads 0.000 GiB
    while the first still reads its peak.

dbt-duckdb hands each dbt thread its own cursor, so every node restarts the
counter from zero and the last profile is the last NODE's peak. That is the
whole reason a max-over-time sampler is REQUIRED here rather than merely
convenient: there is no single moment at which the file holds the answer.

WHAT IT DOES. Runs the command as a child, polls the profile file while it
lives, keeps the running max of both counters, and exits with the child's
own exit code. A wrapper rather than a stop-file daemon on purpose -- the
MLB-172 journal records `Start-Process -PassThru` without `-Wait` returning
an empty `.ExitCode`, and an exit code is a primary datum.

TWO THINGS IT IS HONEST ABOUT.

1. It reads only the HEAD of the profile. Both counters are the first two
   keys the writer emits, so a 200-byte read gets them; parsing the full
   operator tree per sample would cost far more than the thing being
   measured. `--verify-head` proves head-parse == full-parse on a real
   file before trusting it.
2. It UNDERCOUNTS by construction. A node whose profile is overwritten
   between two ticks is never seen, so the reported figure is a measured
   lower bound on the run's true peak. That is the right direction for a
   headroom claim -- it can only under-promise -- but it is not a ceiling,
   and `distinct_profiles` vs the node count says how much was seen.

USAGE (from the repo root):

  python tools/duckdb_peak_sampler.py --label my-run -- \\
    <venv>/Scripts/dbt.exe build \\
    --project-dir dbt_league --profiles-dir dbt_league/profiles \\
    --target duckdb_profiled --target-path target/duckdb --threads 1

The target MUST be `duckdb_profiled` -- the ordinary `duckdb` target writes
no profile, and this reports "no samples" rather than a peak of zero when
that is what happened.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

GIB = 1024 ** 3

# The two root keys, as the profile writer emits them. Anchored to the very
# start of the document so a half-written tail can never satisfy the match.
HEAD_RE = re.compile(
    r'^\s*\{\s*"system_peak_buffer_memory"\s*:\s*(\d+)\s*,'
    r'\s*"system_peak_temp_dir_size"\s*:\s*(\d+)\s*,'
)
HEAD_BYTES = 256


def gib(n):
    return n / GIB


def read_head(path):
    """Both counters from the head of the profile, or None.

    None covers every not-yet-readable state the same way -- absent file,
    a rewrite caught mid-flight, a truncated head -- because none of them
    is a sample and all of them are expected during a run.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES)
    except (FileNotFoundError, PermissionError, OSError):
        return None
    m = HEAD_RE.match(head.decode("utf-8", "replace"))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def read_full(path):
    """Same two counters via a real JSON parse -- the head-read's referee."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return None
    return (
        int(doc.get("system_peak_buffer_memory", 0)),
        int(doc.get("system_peak_temp_dir_size", 0)),
    )


def verify_head(path):
    """Refuse to trust the fast path until it agrees with the slow one."""
    full, head = read_full(path), read_head(path)
    if full is None:
        print(f"  verify-head: no readable profile at {path}")
        return False
    if head is None:
        print("  verify-head: FAILED -- full parse read it, head parse did not")
        return False
    ok = head == full
    print(f"  verify-head: head={head} full={full} -> {'MATCH' if ok else 'MISMATCH'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile-out", default="duckdb_profile.json",
                    help="the file duckdb_profiled writes (profiling_output)")
    ap.add_argument("--interval", type=float, default=0.25,
                    help="seconds between polls (default 0.25)")
    ap.add_argument("--label", default="run", help="names the output files")
    # Under scratchpad/, not next to this file: measurement output is session
    # debris and tools/ is tracked.
    ap.add_argument("--out-dir",
                    default=str(Path(__file__).resolve().parents[1]
                                / "scratchpad" / "duckdb_peaks"))
    ap.add_argument("--verify-head", action="store_true",
                    help="check head-parse == full-parse against the existing "
                         "profile and exit, running no command")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="-- then the command to run and sample")
    args = ap.parse_args()

    prof = Path(args.profile_out).resolve()

    if args.verify_head:
        return 0 if verify_head(prof) else 1

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("no command given (put it after `--`)")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{args.label}.samples.csv"
    sum_path = out_dir / f"{args.label}.summary.json"

    # A profile left by an EARLIER run would otherwise be sampled as if it
    # belonged to this one -- the counters carry no run identity, so a stale
    # file is indistinguishable from a live one and would silently inflate
    # (or invent) the peak.
    stale = prof.exists()
    if stale:
        prof.unlink()

    # The path is pushed into the CHILD'S ENVIRONMENT, not just polled.
    # `duckdb_profiled` writes wherever DBT_DUCKDB_PROFILE_OUT points and
    # defaults to ./duckdb_profile.json, so passing --profile-out alone told
    # the sampler where to LOOK without telling dbt where to WRITE. The
    # first real run cost 45s of build before the empty samples file gave it
    # away -- and an empty sampler looks exactly like a run that never
    # spilled. One flag now sets both ends.
    env = dict(os.environ)
    env["DBT_DUCKDB_PROFILE_OUT"] = str(prof)

    print(f"  sampling {prof}")
    print(f"  every {args.interval}s   stale profile removed: {stale}")
    print(f"  DBT_DUCKDB_PROFILE_OUT set for the child")
    print(f"  $ {' '.join(cmd)}\n", flush=True)

    t0 = time.time()
    child = subprocess.Popen(cmd, env=env)

    max_buf = max_tmp = 0
    at_buf = at_tmp = 0.0
    samples = ticks = misses = 0
    seen = set()
    poll_cost = 0.0

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["elapsed_s", "buffer_bytes", "temp_bytes",
                    "buffer_gib", "temp_gib"])
        while True:
            tick = time.time()
            got = read_head(str(prof))
            poll_cost += time.time() - tick
            ticks += 1

            if got is None:
                misses += 1
            else:
                buf, tmp = got
                samples += 1
                seen.add(got)
                el = tick - t0
                w.writerow([f"{el:.3f}", buf, tmp,
                            f"{gib(buf):.4f}", f"{gib(tmp):.4f}"])
                if buf > max_buf:
                    max_buf, at_buf = buf, el
                if tmp > max_tmp:
                    max_tmp, at_tmp = tmp, el

            if child.poll() is not None:
                break
            time.sleep(args.interval)

    rc = child.wait()
    wall = time.time() - t0

    summary = {
        "label": args.label,
        "command": cmd,
        "exit_code": rc,
        "wall_s": round(wall, 1),
        "interval_s": args.interval,
        "ticks": ticks,
        "samples": samples,
        "unreadable_ticks": misses,
        "distinct_profiles": len(seen),
        "duty_cycle_pct": round(100 * poll_cost / wall, 4) if wall else None,
        "peak_buffer_bytes": max_buf,
        "peak_buffer_gib": round(gib(max_buf), 3),
        "peak_buffer_at_s": round(at_buf, 1),
        "peak_temp_bytes": max_tmp,
        "peak_temp_gib": round(gib(max_tmp), 3),
        "peak_temp_at_s": round(at_tmp, 1),
        "memory_limit": os.environ.get("DBT_DUCKDB_MEMORY_LIMIT", "6GB (default)"),
        "temp_limit": os.environ.get("DBT_DUCKDB_TEMP_LIMIT", "6GB (default)"),
        "engine_threads": os.environ.get("DBT_DUCKDB_THREADS", "1 (default)"),
    }
    sum_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n  exit {rc} after {wall:.1f}s")
    print(f"  ticks {ticks}  samples {samples}  unreadable {misses}  "
          f"distinct {len(seen)}  duty {summary['duty_cycle_pct']}%")

    # A sampler that saw nothing reports 0.000 GiB, which is indistinguishable
    # from a run that genuinely never spilled. Say so instead of printing a
    # number that would be quoted as a measurement.
    if samples == 0:
        summary["peak_buffer_gib"] = summary["peak_temp_gib"] = None
        sum_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print("  !! NO SAMPLES -- the profile was never readable at that path.")
        print("     This is NOT a peak of zero; it is no measurement at all.")
        print(f"     Check that {prof} is where the target actually writes.")
        return rc if rc else 2

    print(f"  PEAK buffer  {gib(max_buf):7.3f} GiB  (at {at_buf:6.1f}s)")
    print(f"  PEAK temp    {gib(max_tmp):7.3f} GiB  (at {at_tmp:6.1f}s)")
    print(f"  -> {sum_path}")
    print("  NOTE: a lower bound. Nodes whose profile is overwritten between "
          "ticks are never sampled.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
