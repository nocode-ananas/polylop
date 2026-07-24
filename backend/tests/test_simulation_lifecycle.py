"""
Fault-injection tests for the simulation lifecycle patch (PATCH:sim-lifecycle-v1).

Deliberately drives the ERROR paths, not the happy path:
  A  stop on a reconnected simulation (no Popen handle) must kill the process
  B  double start must be refused while the real process is alive; concurrent
     starts must produce exactly one process
  C  a stopped run must end as 'stopped', not 'failed'/'completed'
  D  state.json must follow run_state.json into the terminal status
  E  a recycled PID must not be adopted, and must never be signalled

Run it against a copy of the app package, never against the live tree — it starts and
kills real processes and rewrites state files:

    docker exec mirofish-offline sh -c "rm -rf /tmp/testenv && mkdir -p /tmp/testenv \\
        && cp -a /app/backend/app /tmp/testenv/app"
    docker exec -w /tmp/testenv mirofish-offline \\
        /app/backend/.venv/bin/python3 backend/tests/test_simulation_lifecycle.py

Baseline check: run the same file against the unpatched simulation_runner.py — it must
fail the A/B/C/D/E checks. If it passes there too, the test proves nothing.
"""
import os
import sys
import json
import time
import shutil
import threading
import subprocess

# import the app package from the working directory (a copy of backend/), not the repo
sys.path.insert(0, os.environ.get("LIFECYCLE_TEST_PKG", os.getcwd()))

from app.services.simulation_runner import SimulationRunner, RunnerStatus  # noqa: E402

TEST_ROOT = "/tmp/lifecycle_test"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def setup():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    os.makedirs(f"{TEST_ROOT}/sims", exist_ok=True)
    os.makedirs(f"{TEST_ROOT}/scripts", exist_ok=True)

    # A stand-in for run_parallel_simulation.py that just stays alive. The name must
    # end in _simulation.py because that is one of the identity markers.
    with open(f"{TEST_ROOT}/scripts/run_parallel_simulation.py", "w") as f:
        f.write("import time, sys\nwhile True:\n    time.sleep(1)\n")

    SimulationRunner.RUN_STATE_DIR = f"{TEST_ROOT}/sims"
    SimulationRunner.SCRIPTS_DIR = f"{TEST_ROOT}/scripts"
    SimulationRunner._run_states.clear()
    SimulationRunner._processes.clear()
    SimulationRunner._monitor_threads.clear()
    # absent when running this suite against the unpatched baseline
    if hasattr(SimulationRunner, "_stop_requested"):
        SimulationRunner._stop_requested.clear()


def make_sim(sim_id, with_state_json=True):
    sim_dir = os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id)
    os.makedirs(sim_dir, exist_ok=True)
    with open(os.path.join(sim_dir, "simulation_config.json"), "w") as f:
        json.dump({"time_config": {"total_simulation_hours": 2, "minutes_per_round": 30}}, f)
    if with_state_json:
        with open(os.path.join(sim_dir, "state.json"), "w") as f:
            json.dump({"simulation_id": sim_id, "status": "ready",
                       "updated_at": "2026-01-01T00:00:00"}, f)
    return sim_dir


def read_run_state(sim_id):
    with open(os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id, "run_state.json")) as f:
        return json.load(f)


def read_state_json(sim_id):
    with open(os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id, "state.json")) as f:
        return json.load(f)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def simulate_backend_restart(sim_id):
    """Drop every bit of in-memory state, exactly like a fresh backend process."""
    SimulationRunner._processes.pop(sim_id, None)
    SimulationRunner._monitor_threads.pop(sim_id, None)
    SimulationRunner._run_states.pop(sim_id, None)
    if hasattr(SimulationRunner, "_stop_requested"):
        SimulationRunner._stop_requested.discard(sim_id)


# ---------------------------------------------------------------- A + D + C(orphan)
def test_stop_after_restart():
    print("\nA) stop after a backend restart (reconnected simulation)")
    sim_id = "sim_test_orphan"
    make_sim(sim_id)

    state = SimulationRunner.start_simulation(sim_id)
    pid = state.process_pid
    check("process started", pid_alive(pid), f"pid={pid}")

    # The monitor thread of the *previous* backend is gone with the process; emulate
    # a restart, then let the new backend reconnect from run_state.json alone.
    time.sleep(0.5)
    monitor = SimulationRunner._monitor_threads.get(sim_id)
    simulate_backend_restart(sim_id)
    if monitor:
        # the old monitor still holds a Popen; stop it observing to keep the test clean
        pass

    SimulationRunner.reconnect_orphaned_simulations()
    reconnected = SimulationRunner.get_run_state(sim_id)
    check("reconnect adopted the live process",
          reconnected.runner_status == RunnerStatus.RUNNING and sim_id in SimulationRunner._monitor_threads,
          f"status={reconnected.runner_status.value}")
    check("no Popen handle exists (this is what broke stop)",
          sim_id not in SimulationRunner._processes)

    SimulationRunner.stop_simulation(sim_id)
    time.sleep(1.0)

    check("A: process is actually dead after stop", not pid_alive(pid), f"pid={pid}")

    rs = read_run_state(sim_id)
    check("C: run_state.json says stopped (not failed/completed)",
          rs["runner_status"] == "stopped", f"got '{rs['runner_status']}'")
    check("D: state.json followed to stopped",
          read_state_json(sim_id)["status"] == "stopped",
          f"got '{read_state_json(sim_id)['status']}'")
    check("stop error field is empty (termination succeeded)",
          not rs.get("error"), f"error={rs.get('error')}")


# ---------------------------------------------------------------------------- C
def test_stop_not_relabelled_as_failed():
    print("\nC) stop with a live monitor thread must not end as 'failed'")
    sim_id = "sim_test_stopstatus"
    make_sim(sim_id)

    state = SimulationRunner.start_simulation(sim_id)
    pid = state.process_pid
    time.sleep(0.5)
    check("monitor thread is running", sim_id in SimulationRunner._monitor_threads)

    SimulationRunner.stop_simulation(sim_id)
    check("process dead", not pid_alive(pid))

    # The monitor wakes up to a SIGTERM exit code (-15) — the old code turned that
    # into 'failed'. Give it more than one poll interval to prove it stays 'stopped'.
    time.sleep(4)
    rs = read_run_state(sim_id)
    check("C: still 'stopped' after the monitor observed the exit",
          rs["runner_status"] == "stopped", f"got '{rs['runner_status']}'")
    check("D: state.json is stopped", read_state_json(sim_id)["status"] == "stopped")


# ---------------------------------------------------------------------------- B
def test_double_start():
    print("\nB) double start")
    sim_id = "sim_test_double"
    make_sim(sim_id)

    state = SimulationRunner.start_simulation(sim_id)
    first_pid = state.process_pid

    # b1: status file lies (a stop that failed to kill leaves 'stopped' behind)
    st = SimulationRunner.get_run_state(sim_id)
    st.runner_status = RunnerStatus.STOPPED
    SimulationRunner._save_run_state(st)
    SimulationRunner._processes.pop(sim_id, None)

    refused = False
    try:
        SimulationRunner.start_simulation(sim_id)
    except ValueError as e:
        refused = "still alive" in str(e)
    check("B1: start refused while the real process is alive", refused)
    check("B1: first process untouched", pid_alive(first_pid))

    SimulationRunner._processes[sim_id] = None  # restore nothing; clean up by pid
    SimulationRunner._processes.pop(sim_id, None)
    os.kill(first_pid, 15)
    time.sleep(0.5)

    # b2: concurrent starts (axios retries POSTs up to 3x)
    sim_id2 = "sim_test_race"
    make_sim(sim_id2)
    results = []

    def worker():
        try:
            s = SimulationRunner.start_simulation(sim_id2)
            results.append(("ok", s.process_pid))
        except Exception as e:
            results.append(("err", str(e)))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    started = [r for r in results if r[0] == "ok"]
    check("B2: exactly one of 3 concurrent starts succeeded",
          len(started) == 1, f"results={results}")

    # Count real simulation processes. Read /proc directly rather than shelling out:
    # a shell pipeline carrying the simulation id would match its own command line.
    live = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if sim_id2 in cmd and "run_parallel_simulation.py" in cmd:
            live.append((entry, cmd.strip()))
    check("B2: exactly one simulation process exists", len(live) == 1,
          f"pids={[p for p, _ in live]}")
    for pid_str, cmd in live:
        print(f"        cmdline: {cmd[:120]}")
        try:
            os.kill(int(pid_str), 9)
        except OSError:
            pass


# ---------------------------------------------------------------------------- E
def test_pid_reuse():
    print("\nE) PID reuse must not be adopted")
    sim_id = "sim_test_pidreuse"
    sim_dir = make_sim(sim_id)

    # An unrelated long-lived process stands in for whatever inherited the PID
    stranger = subprocess.Popen(["sleep", "300"])
    time.sleep(0.3)

    with open(os.path.join(sim_dir, "run_state.json"), "w") as f:
        json.dump({
            "simulation_id": sim_id,
            "runner_status": "running",
            "process_pid": stranger.pid,
            "process_cmdline": f"/app/backend/.venv/bin/python3 "
                               f"{TEST_ROOT}/scripts/run_parallel_simulation.py "
                               f"--config {sim_dir}/simulation_config.json",
            "twitter_running": True,
            "reddit_running": True,
        }, f)
    SimulationRunner._run_states.pop(sim_id, None)

    if not hasattr(SimulationRunner, "_verify_simulation_process"):
        check("E: foreign process classified as mismatch", False, "helper not implemented")
        verdict = None
    else:
        expected = getattr(SimulationRunner._load_run_state(sim_id), "process_cmdline", None)
        verdict = SimulationRunner._verify_simulation_process(stranger.pid, sim_id, expected)
        check("E: foreign process classified as mismatch", verdict == "mismatch", f"verdict={verdict}")

    SimulationRunner.reconnect_orphaned_simulations()
    check("E: not adopted (no monitor thread)", sim_id not in SimulationRunner._monitor_threads)
    check("E: foreign process still alive (never signalled)", stranger.poll() is None)

    rs = read_run_state(sim_id)
    check("E: run marked stopped instead of hanging on 'running'",
          rs["runner_status"] == "stopped", f"got '{rs['runner_status']}'")
    check("D: state.json followed", read_state_json(sim_id)["status"] == "stopped")

    # stop must refuse to signal it, too
    st = SimulationRunner.get_run_state(sim_id)
    st.runner_status = RunnerStatus.RUNNING
    SimulationRunner._save_run_state(st)
    SimulationRunner.stop_simulation(sim_id)
    check("E: stop did not kill the foreign process", stranger.poll() is None)
    rs = read_run_state(sim_id)
    check("E: stop reports it could not terminate", bool(rs.get("error")), f"error={rs.get('error')}")

    stranger.kill()

    # legacy run_state.json without process_cmdline must still be judged correctly
    if hasattr(SimulationRunner, "_verify_simulation_process"):
        check("E: legacy state (no cmdline) — foreign process is mismatch",
              SimulationRunner._verify_simulation_process(os.getpid(), sim_id, None) == "mismatch")
        check("E: dead pid is 'gone'",
              SimulationRunner._verify_simulation_process(999997, sim_id, None) == "gone")
    else:
        check("E: legacy state (no cmdline) — foreign process is mismatch", False, "helper not implemented")
        check("E: dead pid is 'gone'", False, "helper not implemented")


# ---------------------------------------------------------------------------- D
def test_natural_completion_syncs_state_json():
    print("\nD) a simulation ending on its own must clear 'running' in state.json")
    sim_id = "sim_test_complete"
    sim_dir = make_sim(sim_id)

    # exits by itself with code 0, like a finished simulation
    with open(f"{TEST_ROOT}/scripts/run_parallel_simulation.py", "w") as f:
        f.write("import time\ntime.sleep(1)\n")

    SimulationRunner.start_simulation(sim_id)
    check("state.json set to running on start", read_state_json(sim_id)["status"] == "running")

    deadline = time.time() + 20
    while time.time() < deadline:
        if read_run_state(sim_id)["runner_status"] in ("completed", "failed", "stopped"):
            break
        time.sleep(0.5)

    rs = read_run_state(sim_id)
    check("D: run_state.json reached completed", rs["runner_status"] == "completed",
          f"got '{rs['runner_status']}'")

    # run_state.json and state.json are two separate writes; allow the second one a
    # moment rather than racing it (the gap is milliseconds and self-healing)
    deadline = time.time() + 5
    while time.time() < deadline and read_state_json(sim_id)["status"] != "completed":
        time.sleep(0.2)
    check("D: state.json no longer says running",
          read_state_json(sim_id)["status"] == "completed",
          f"got '{read_state_json(sim_id)['status']}'")

    # restore the long-running stand-in
    with open(f"{TEST_ROOT}/scripts/run_parallel_simulation.py", "w") as f:
        f.write("import time, sys\nwhile True:\n    time.sleep(1)\n")


# ---------------------------------------------------------------------------- extra
def test_reloader_guard():
    print("\nF) reloader guard (debug mode starts create_app twice)")
    from app.config import Config
    original = Config.DEBUG
    try:
        Config.DEBUG = True
        os.environ.pop("WERKZEUG_RUN_MAIN", None)
        if not hasattr(SimulationRunner, "_is_reloader_parent"):
            check("F: parent process would skip reconnect", False, "guard not implemented")
            check("F: reloader child reconnects", False, "guard not implemented")
            check("F: without debug, reconnect always runs", False, "guard not implemented")
            return
        check("F: parent process would skip reconnect", SimulationRunner._is_reloader_parent())
        os.environ["WERKZEUG_RUN_MAIN"] = "true"
        check("F: reloader child reconnects", not SimulationRunner._is_reloader_parent())
        Config.DEBUG = False
        os.environ.pop("WERKZEUG_RUN_MAIN", None)
        check("F: without debug, reconnect always runs", not SimulationRunner._is_reloader_parent())
    finally:
        Config.DEBUG = original
        os.environ["WERKZEUG_RUN_MAIN"] = "true"


def test_atomic_write():
    print("\nG) run_state.json write is atomic")
    sim_id = "sim_test_atomic"
    make_sim(sim_id)
    state = SimulationRunner.get_run_state(sim_id)
    from app.services.simulation_runner import SimulationRunState
    state = SimulationRunState(simulation_id=sim_id, runner_status=RunnerStatus.RUNNING)
    SimulationRunner._save_run_state(state)

    path = os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id, "run_state.json")
    corrupted = []

    def reader():
        end = time.time() + 2
        while time.time() < end:
            try:
                with open(path) as f:
                    json.load(f)
            except json.JSONDecodeError:
                corrupted.append(1)
            except FileNotFoundError:
                corrupted.append(1)

    t = threading.Thread(target=reader)
    t.start()
    end = time.time() + 2
    while time.time() < end:
        SimulationRunner._save_run_state(state)
    t.join()
    check("G: no partial reads during continuous rewriting", not corrupted,
          f"{len(corrupted)} corrupted reads")


if __name__ == "__main__":
    os.environ["WERKZEUG_RUN_MAIN"] = "true"  # behave like the request-serving process
    setup()
    for fn in (test_stop_after_restart, test_stop_not_relabelled_as_failed,
               test_double_start, test_pid_reuse,
               test_natural_completion_syncs_state_json,
               test_reloader_guard, test_atomic_write):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} raised", False, str(e))

    print("\n" + "=" * 60)
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for name, _, detail in failed:
        print(f"  FAILED: {name} — {detail}")
    sys.exit(1 if failed else 0)
