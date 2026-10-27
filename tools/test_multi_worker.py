import threading, time
from pathlib import Path
import autopilot as A

def test_workers_parser():
    parser = A.build_parser()
    args = parser.parse_args(["apply", "--workers", "3", "--max", "6"])
    assert args.workers == 3
    assert args.max == 6

    args2 = parser.parse_args(["apply", "--max", "2"])
    assert args2.workers is None

    args3 = parser.parse_args(["run", "--workers", "2"])
    assert args3.workers == 2

def test_profile_dir_isolation():
    cfg = A.read_json(A.CFG)
    base = A._profile_dir(cfg)
    w1 = A._profile_dir(cfg, worker_id=1)
    w2 = A._profile_dir(cfg, worker_id=2)
    assert base is not None
    assert str(w1).endswith("-w1")
    assert str(w2).endswith("-w2")
    assert w1 != w2
    assert w1 != base

def test_watch_isolation():
    errors = []
    def run_worker(wid, job_name):
        try:
            A._TL.worker_id = wid
            A._WATCH["job"] = job_name
            A._WATCH["t"] = time.monotonic()
            A._mark_step("filling")
            time.sleep(0.05)
            assert A._WATCH["job"] == job_name
            assert A._WATCH["step"] == "filling"
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=run_worker, args=(1, "Job One"))
    t2 = threading.Thread(target=run_worker, args=(2, "Job Two"))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert not errors, errors
    assert A._WORKER_WATCH[1]["job"] == "Job One"
    assert A._WORKER_WATCH[2]["job"] == "Job Two"

def test_fill_deadline_isolation():
    errors = []
    cfg = A.read_json(A.CFG)
    def w1():
        try:
            A._TL.worker_id = 1
            A._extend_fill_budget(cfg)
            assert not A._fill_expired()
        except Exception as e:
            errors.append(e)

    def w2():
        try:
            A._TL.worker_id = 2
            A._TL.fill_deadline = time.monotonic() - 10.0
            assert A._fill_expired()
        except Exception as e:
            errors.append(e)

    th1 = threading.Thread(target=w1)
    th2 = threading.Thread(target=w2)
    th1.start(); th2.start()
    th1.join(); th2.join()
    assert not errors, errors

def test_worker_quit_via_event():
    import json, multiprocessing as mp
    ev = mp.Event()
    A._STOP_EVENT, A._QUIT_RUN["v"] = ev, False
    try:
        A.CONTROL.write_text(json.dumps({"action": "quit"}), encoding="utf-8")
        assert not A._quit_requested() and A.read_control() is None
        assert json.loads(A.CONTROL.read_text(encoding="utf-8")) == {"action": "quit"}, \
            "a worker must not swallow the parent's quit"
        gen = mp.Value("i", 0)
        A._SKIP_GEN, A._SKIP_SEEN["v"] = gen, 0
        assert A.read_control() is None
        gen.value += 1
        assert A.read_control() == "skip" and A.read_control() is None, \
            "a relayed skip fires once per worker"
        ev.set()
        assert A._quit_requested() and A.read_control() == "quit"
    finally:
        A._STOP_EVENT, A._SKIP_GEN = None, None
        A.CONTROL.write_text("{}", encoding="utf-8")

def test_worker_respects_limit():
    import multiprocessing as mp, queue
    stubs = {"apply_one": lambda job, *a: {"id": job["id"]}}
    saved = {k: getattr(A, k) for k in stubs}
    saved_db = (A.db.claim_job, A.db.release_job)
    for k, v in stubs.items(): setattr(A, k, v)
    A.db.claim_job = lambda con, jid, tok, **kw: jid != "j1"   # j1 is held by another run
    A.db.release_job = lambda con, jid, tok: None
    in_q, out_q, started = mp.Queue(), mp.Queue(), mp.Value("i", 0)
    try:
        for i in range(5):
            in_q.put({"id": f"j{i}", "company": "c", "title": "t"})
        time.sleep(0.2)
        cfg = {"apply": {"vacancy_timeout_seconds": 240}}
        A._worker_process_loop(1, in_q, out_q, cfg, True, False, False, False, mp.Event(), started, 2)
        got = []
        while True:
            try: got.append(out_q.get(timeout=0.5)["result"]["id"])
            except queue.Empty: break
        assert got == ["j0", "j2"], got
        assert started.value == 2
    finally:
        for k, v in saved.items(): setattr(A, k, v)
        A.db.claim_job, A.db.release_job = saved_db
        A._STOP_EVENT = None

if __name__ == "__main__":
    test_workers_parser()
    test_profile_dir_isolation()
    test_watch_isolation()
    test_fill_deadline_isolation()
    test_worker_quit_via_event()
    test_worker_respects_limit()
    print("multi-worker self-check OK")
