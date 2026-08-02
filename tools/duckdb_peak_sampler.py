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

WHY IT ALSO SAMPLES THE PROCESS TREE. Neither DuckDB counter can answer
"how much free RAM does this need", and that is not a capture problem.
`system_peak_buffer_memory` reads 2.0x `memory_limit` at BOTH 6 GB and
4 GB -- it reports what the engine was ALLOWED, not what it NEEDED, so
trending it would track our own configuration. `system_peak_temp_dir_size`
measures spill, which is disk. The quantity the claim is about is physical
residency, and the OS is the only thing that measures it.

So each tick also walks the child's process TREE and reads two OS figures
per process. The tree matters: `dbt.exe` is a launcher shim whose real
worker is a GRANDCHILD, and sampling the shim alone measures ~10 MB and
is lying (MLB-172, session 4). Two figures are reported because they
BRACKET the answer from opposite sides:

  * `peak_rss_tree` -- the max over ticks of the SUM of every live
    process's CURRENT working set. Simultaneous by construction, so it is
    a true instant; but it misses whatever happened between two ticks, so
    it is a LOWER bound.
  * `sum_peak_rss` -- the sum of each process's own `PeakWorkingSetSize`,
    the high-water mark WINDOWS maintains. Immune to sampling gaps, but
    those peaks need not have occurred at the same moment, so it is an
    UPPER bound.

The truth is between them, and how far apart they sit is itself the
reading: close together means the run has one dominant process whose peak
the sampler caught. Per-process peaks are retained after a process exits,
so a worker that dies mid-run still counts.

This half is WINDOWS-ONLY, via ctypes -- no new dependency, because this
file is tracked and psutil is not in any venv here. On other platforms the
DuckDB counters still work and the summary says `rss_available: false`
rather than reporting a zero that would read as a measurement.

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


# --------------------------------------------------------------------------
# Process-tree residency (Windows). See the module docstring for why.
# --------------------------------------------------------------------------

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)


def total_phys_bytes():
    """Installed RAM, for context next to a residency figure."""
    if not IS_WINDOWS:
        return None
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    if not _k32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return None
    return int(st.ullTotalPhys)


def _all_processes():
    """{pid: (ppid, exe_name)} for every process, from one Toolhelp snapshot.

    One snapshot per tick rather than per process: the walk has to be
    cheap enough that measuring does not perturb what is measured, and
    `duty_cycle_pct` in the summary is what proves it did not.

    The name is carried so the peak can be ATTRIBUTED. A bare "5.3 GiB"
    is not quotable -- the whole risk of walking a process tree is
    sweeping in something that is not the build, and a figure nobody can
    break down is one nobody can check.
    """
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return {}
    try:
        ent = PROCESSENTRY32W()
        ent.dwSize = ctypes.sizeof(ent)
        if not _k32.Process32FirstW(snap, ctypes.byref(ent)):
            return {}
        out = {}
        while True:
            out[int(ent.th32ProcessID)] = (int(ent.th32ParentProcessID),
                                           str(ent.szExeFile))
            if not _k32.Process32NextW(snap, ctypes.byref(ent)):
                break
        return out
    finally:
        _k32.CloseHandle(snap)


def descendants(root_pid):
    """{pid: exe_name} for `root_pid` plus everything beneath it.

    PPIDs come from a snapshot and Windows recycles PIDs, so a long-dead
    parent's number can be reused and drag an unrelated process into the
    set. Over a run measured in minutes that is vanishingly unlikely, and
    the failure mode would be an over-count -- which is the safe direction
    for a headroom figure.
    """
    procs = _all_processes()
    if root_pid not in procs:
        return {}
    kids = {}
    for pid, (ppid, _name) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    seen, stack = {}, [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen[pid] = procs[pid][1]
        stack.extend(kids.get(pid, ()))
    return seen


def proc_memory(pid):
    """(working_set, peak_working_set, private_bytes) or None if gone.

    None covers exited and access-denied alike: neither is a sample, and
    both are ordinary during a run.
    """
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        ctrs = PROCESS_MEMORY_COUNTERS_EX()
        ctrs.cb = ctypes.sizeof(ctrs)
        if not _k32.K32GetProcessMemoryInfo(h, ctypes.byref(ctrs), ctrs.cb):
            return None
        return (int(ctrs.WorkingSetSize),
                int(ctrs.PeakWorkingSetSize),
                int(ctrs.PrivateUsage))
    finally:
        _k32.CloseHandle(h)


class TreeResidency:
    """Max-over-time working set for a process tree, both bounds at once.

    Keeps each PID's OS-maintained peak in a dict that OUTLIVES the
    process, so a worker that exits before the run ends still contributes
    to `sum_peak`. Without that, the one number immune to sampling gaps
    would itself be lost to one.
    """

    def __init__(self):
        self.peak_tree = 0          # lower bound: max of a simultaneous sum
        self.peak_tree_at = 0.0
        self.peak_private = 0
        self.per_pid_peak = {}      # upper bound source, retained after exit
        self.per_pid_name = {}
        self.peak_breakdown = []    # who held the tree peak, and how much
        self.max_procs = 0
        self.samples = 0

    def tick(self, root_pid, elapsed):
        """One reading. Returns the tree's current working set, or None."""
        pids = descendants(root_pid)
        if not pids:
            return None
        live = ws = priv = 0
        contributors = []
        for pid, name in pids.items():
            got = proc_memory(pid)
            if got is None:
                continue
            cur, peak, private = got
            live += 1
            ws += cur
            priv += private
            contributors.append((name, pid, cur))
            self.per_pid_name[pid] = name
            if peak > self.per_pid_peak.get(pid, 0):
                self.per_pid_peak[pid] = peak
        if not live:
            return None
        self.samples += 1
        self.max_procs = max(self.max_procs, live)
        if ws > self.peak_tree:
            self.peak_tree, self.peak_tree_at = ws, elapsed
            # Snapshot the split at the peak, not at the end: by the time
            # the run finishes the process that held it may be gone.
            self.peak_breakdown = sorted(contributors, key=lambda c: -c[2])
        self.peak_private = max(self.peak_private, priv)
        return ws

    @property
    def sum_peak(self):
        return sum(self.per_pid_peak.values())

    def breakdown_rows(self):
        return [{"exe": n, "pid": p, "working_set_gib": round(gib(b), 3)}
                for n, p, b in self.peak_breakdown]

    def top_peaks(self, limit=6):
        """Per-process high-water marks, biggest first -- the audit trail
        for `sum_peak_rss`."""
        rows = sorted(self.per_pid_peak.items(), key=lambda kv: -kv[1])
        return [{"exe": self.per_pid_name.get(pid, "?"), "pid": pid,
                 "peak_working_set_gib": round(gib(v), 3)}
                for pid, v in rows[:limit]]


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
    res = TreeResidency() if IS_WINDOWS else None

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["elapsed_s", "buffer_bytes", "temp_bytes",
                    "buffer_gib", "temp_gib", "tree_rss_bytes",
                    "tree_rss_gib"])
        while True:
            tick = time.time()
            got = read_head(str(prof))
            # Inside the timed region on purpose: the tree walk is part of
            # the cost of measuring, so duty_cycle_pct has to carry it.
            rss = res.tick(child.pid, tick - t0) if res else None
            poll_cost += time.time() - tick
            ticks += 1

            # The RSS half samples the OS and the counter half samples a
            # file dbt may not have written yet, so they miss independently.
            # A tick with residency but no profile is still a real reading
            # and gets a row, with the counter columns left blank rather
            # than filled with a zero that would flatten the peak.
            if got is None:
                misses += 1
                if rss is not None:
                    w.writerow([f"{tick - t0:.3f}", "", "", "", "",
                                rss, f"{gib(rss):.4f}"])
            else:
                buf, tmp = got
                samples += 1
                seen.add(got)
                el = tick - t0
                w.writerow([f"{el:.3f}", buf, tmp,
                            f"{gib(buf):.4f}", f"{gib(tmp):.4f}",
                            "" if rss is None else rss,
                            "" if rss is None else f"{gib(rss):.4f}"])
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
        "rss_available": bool(res and res.samples),
    }

    if res and res.samples:
        total = total_phys_bytes()
        summary.update({
            "rss_samples": res.samples,
            "rss_max_processes": res.max_procs,
            # Lower bound -- a simultaneous sum, but only at sampled instants.
            "peak_rss_tree_bytes": res.peak_tree,
            "peak_rss_tree_gib": round(gib(res.peak_tree), 3),
            "peak_rss_tree_at_s": round(res.peak_tree_at, 1),
            # Upper bound -- gap-immune, but the per-process peaks it adds
            # need not have been simultaneous.
            "sum_peak_rss_bytes": res.sum_peak,
            "sum_peak_rss_gib": round(gib(res.sum_peak), 3),
            "peak_private_bytes_gib": round(gib(res.peak_private), 3),
            "machine_total_ram_gib": round(gib(total), 1) if total else None,
            # Attribution. Without these the figure is unquotable: the
            # standing risk of a tree walk is sweeping in a process that
            # is not the build, and this is what lets a reader check.
            "peak_rss_breakdown": res.breakdown_rows(),
            "per_process_peaks": res.top_peaks(),
        })
    elif res:
        summary["rss_note"] = ("tree never resolved -- the child exited "
                               "before the first tick, or PID reuse broke "
                               "the walk")
    else:
        summary["rss_note"] = f"process-tree residency is Windows-only (on {sys.platform})"

    sum_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n  exit {rc} after {wall:.1f}s")
    print(f"  ticks {ticks}  samples {samples}  unreadable {misses}  "
          f"distinct {len(seen)}  duty {summary['duty_cycle_pct']}%")

    # Printed before the no-profile bail-out: residency is sampled from the
    # OS, so it survives a run that wrote no profile at all -- and that is
    # exactly the run where it is the only thing you have.
    if res and res.samples:
        print(f"  PEAK rss     {gib(res.peak_tree):7.3f} GiB  "
              f"(at {res.peak_tree_at:6.1f}s, {res.max_procs} procs)  "
              f"<= true peak <= {gib(res.sum_peak):.3f} GiB")
        for row in res.breakdown_rows()[:4]:
            print(f"       at peak: {row['working_set_gib']:7.3f} GiB  "
                  f"{row['exe']} ({row['pid']})")

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
