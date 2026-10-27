import json
import os
from pathlib import Path

import pytest
import autopilot as A
import db


def synthetic_worker(wid, in_q, out_q, cfg, dry, submit, show, review, stop, started, limit, skip_gen):
    """Real process/queue/SQLite crash; no browser, provider or live database."""
    db.DB_PATH = Path(cfg['_test_db'])
    con = db.connect()
    while not stop.is_set():
        with started.get_lock():
            if started.value >= limit:
                break
            started.value += 1
        job = in_q.get(timeout=2)
        assert db.claim_job(con, job['id'], f'{os.getpid()}-test')
        result = {**job, 'applied_at': A.now(), 'needs_manual': 1, 'manual_reason': 'isolated watchdog'}
        if job['id'] == 'j0' and cfg.get('_crash_before_record'):
            os._exit(3)
        db.insert_application(con, result)
        if job['id'] == 'j0' or cfg.get('_crash_all'):
            if cfg.get('_quit'):
                Path(cfg['_control']).write_text('{"action":"quit"}')
            os._exit(3)
        db.release_job(con, job['id'], f'{os.getpid()}-test')
        out_q.put({'wid': wid, 'result': result})
    con.close()


def setup_run(tmp_path, monkeypatch, count, **extra):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'jobs.db')
    monkeypatch.setattr(A, 'ROOT', tmp_path)
    monkeypatch.setattr(A, 'CONTROL', tmp_path / 'control.json')
    monkeypatch.setattr(A, 'PROGRESS', tmp_path / 'progress.json')
    monkeypatch.setattr(A, '_QUIT_RUN', {'v': False})
    monkeypatch.setattr(A, '_STOP_EVENT', None)
    monkeypatch.setattr(A._TL, 'worker_id', 0, raising=False)
    monkeypatch.setattr(A, '_start_stall_watchdog', lambda *a: None)
    monkeypatch.setattr(A, 'prune_screenshots', lambda *a: None)
    monkeypatch.setattr(A, '_cv_names_candidate', lambda *a: True)
    monkeypatch.setattr(A, 'title_fit', lambda *a: True)
    monkeypatch.setattr(A, 'blocked_company', lambda *a: False)
    monkeypatch.setattr(A, '_worker_process_loop', synthetic_worker)
    cfg = {'profile': {}, 'cv_path': 'appsec.pdf', 'search': {}, 'llm': {'enabled': False},
           'apply': {'max_per_run': 74, 'max_per_company': 100, 'max_per_company_window': 100,
                     'reject_cooldown_count': 0, 'min_cv_match': 0}, '_test_db': str(db.DB_PATH), '_control': str(A.CONTROL), **extra}
    (tmp_path / 'appsec.pdf').write_bytes(b'fixture')
    monkeypatch.setattr(A, 'read_json', lambda *a: cfg)
    monkeypatch.setattr(A, 'pick_cv', lambda *a: 'appsec.pdf')
    with db.connect() as con:
        for i in range(count):
            con.execute("INSERT INTO jobs (id,company,title,url,score,created_at,persona) VALUES (?,?,?,?,100,?,'appsec')",
                        (f'j{i}', f'Company {i}', 'AppSec Engineer', f'https://jobs.lever.co/company/{i:08x}-0000-0000-0000-000000000000', A.now()))
        con.commit()
    return A.build_parser().parse_args(['apply', '--submit', '--ats-only', '--workers', '1', '--max', '74'])


@pytest.mark.parametrize('before_record', [False, True])
def test_single_dead_worker_recovers_and_budget_is_74(tmp_path, monkeypatch, before_record):
    args = setup_run(tmp_path, monkeypatch, 75, _crash_before_record=before_record)
    A.apply(args)
    with db.connect() as con:
        assert con.execute('SELECT count(*),count(DISTINCT job_id) FROM applications').fetchone()[:] == (74, 74)
        assert con.execute("SELECT count(*) FROM applications WHERE job_id='j0'").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM jobs WHERE claim_token IS NOT NULL").fetchone()[0] == 0


def test_quit_after_crash_never_restarts(tmp_path, monkeypatch):
    args = setup_run(tmp_path, monkeypatch, 4, _quit=True)
    with pytest.raises(SystemExit) as error:
        A.apply(args)
    assert error.value.code == 2
    with db.connect() as con:
        assert con.execute('SELECT count(*) FROM applications').fetchone()[0] == 1


def test_restart_limit_reports_incomplete(tmp_path, monkeypatch):
    args = setup_run(tmp_path, monkeypatch, 8, _crash_all=True)
    with pytest.raises(SystemExit) as error:
        A.apply(args)
    assert error.value.code == 3
    with db.connect() as con:
        assert con.execute('SELECT count(*),count(DISTINCT job_id) FROM applications').fetchone()[:] == (4, 4)


def test_exhausted_short_queue_finishes_without_extra_restart(tmp_path, monkeypatch):
    args = setup_run(tmp_path, monkeypatch, 1)
    A.apply(args)
    with db.connect() as con:
        assert con.execute('SELECT count(*) FROM applications').fetchone()[0] == 1


def test_manual_pending_unconfirmed_and_url_variants_are_excluded(tmp_path, monkeypatch):
    args = setup_run(tmp_path, monkeypatch, 0)
    with db.connect() as con:
        for i, kind in enumerate(('manual', 'pending', 'unconfirmed', 'submitted')):
            old_url = f'https://job-boards.greenhouse.io/old/jobs/{100+i}'
            con.execute("INSERT INTO jobs (id,company,title,url,score,created_at,status) VALUES (?,?,?,?,100,?,?)",
                        (kind, kind, 'Old role', old_url, A.now(), 'pending_confirmation' if kind == 'pending' else None))
            if kind != 'pending':
                db.insert_application(con, {'id': kind, 'applied_at': '2020-01-01', 'submitted': kind == 'submitted',
                                           'needs_manual': kind == 'manual', 'manual_reason': 'submit did not confirm' if kind == 'unconfirmed' else ''})
            con.execute("INSERT INTO jobs (id,company,title,url,score,created_at) VALUES (?,?,?,?,100,?)",
                        (kind+'-variant', 'Other '+kind, 'Different title', f'https://careers.example.test/role?gh_jid={100+i}', A.now()))
        con.commit()
        original = [tuple(r) for r in con.execute('SELECT * FROM applications')]
        for kind in ('manual', 'pending', 'unconfirmed', 'submitted'):
            assert kind in db.skip_ids(con)
            assert not db.claim_job(con, kind, f'{os.getpid()}-test')
    A.apply(args)
    with db.connect() as con:
        assert [tuple(r) for r in con.execute('SELECT * FROM applications')] == original


@pytest.mark.parametrize('already_recorded', [False, True])
def test_worker_exception_is_durable_without_a_duplicate(tmp_path, monkeypatch, already_recorded):
    import multiprocessing as mp
    import queue
    worker = A._worker_process_loop
    setup_run(tmp_path, monkeypatch, 1)
    def fail(job, *a):
        if already_recorded:
            with db.connect() as con:
                db.insert_application(con, {'id': job['id'], 'applied_at': A.now(), 'needs_manual': 1})
        raise RuntimeError('isolated transport failure')
    monkeypatch.setattr(A, 'apply_one', fail)
    with db.connect() as con:
        job = dict(con.execute("SELECT * FROM jobs WHERE id='j0'").fetchone())
    input_q, output_q = queue.Queue(), queue.Queue()
    input_q.put(job)
    cfg = {'apply': {}, 'profile': {}, 'cv_path': 'appsec.pdf'}
    worker(1, input_q, output_q, cfg, False, True, False, False, mp.Event(), mp.Value('i', 0), 1)
    assert output_q.get()['result']['needs_manual'] == 1
    with db.connect() as con:
        assert con.execute('SELECT count(*) FROM applications').fetchone()[0] == 1
        assert 'j0' in db.skip_ids(con)
