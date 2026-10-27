"""Regression checks for the core-flow audit: truthfulness of cached/offline answers,
forcing only via CLI, persona answer overrides, infra errors, stall rules."""
import json
import multiprocessing as mp
import queue

import pytest
import autopilot as A
import db
from tests.test_worker_recovery import setup_run

SPONSOR_DA = {"sponsorship": "Yes, but not one of the visas listed here", "visa": "Yes, but not one of the visas listed here",
              "right to work": "No"}


@pytest.mark.parametrize("label", [
    "Are you able to work in the EU without sponsorship?",
    "Are you eligible to work in the UK without requiring visa sponsorship?",
    "Are you legally eligible to work in Germany without sponsorship?",
    "Can you start work without the need for sponsorship?",
])
def test_without_sponsorship_questions_are_answered_no(label):
    assert A._match_dropdown_answer(label, SPONSOR_DA) == "No"
    assert A.offline_answer(label, ["Yes", "No"], {"profile": {}}) is None
    assert A._best_option_index("No", [(0, "yes"), (1, "no")]) == 1


def test_need_sponsorship_question_is_unchanged():
    assert A._match_dropdown_answer("Will you require visa sponsorship?", SPONSOR_DA).startswith("Yes")


@pytest.mark.parametrize("label", [
    "I confirm that I do not reside in or perform work from any of the following locations: Russia, Crimea region",
    "Since obtaining your most recent citizenship, did you afterwards become a permanent resident in any other country?",
    "Are you local and within commuting distance to this job?",
])
def test_lies_are_rejected_and_answered_no(label):
    q = {"label": label, "options": ["Yes", "No"]}
    assert A._unsupported_claim(q, "Yes", "cv text") , "a cached/LLM Yes must be rejected"
    assert not A._unsupported_claim(q, "No", "cv text")
    assert A.offline_answer(label, ["Yes", "No"], {"profile": {}}) is None


def test_willing_to_commute_still_yes():
    assert A.offline_answer("Are you willing to commute to the office?", ["Yes", "No"], {"profile": {}, "apply": {"answers": {"open_to_onsite": "Yes"}}}) == "Yes"
    assert not A._unsupported_claim({"label": "Are you willing to commute?"}, "Yes", "cv")


def test_offline_rules_have_no_backspace_chars():
    assert not any("\x08" in v for _p, v in A._OFFLINE_RULES)
    pats = [p for p, _v in A._OFFLINE_RULES]
    assert len(pats) == len(set(pats)), "duplicate offline rule"


def test_force_only_through_cli_flag(monkeypatch):
    cfg = A.read_json(A.CFG)
    assert "force_ids" not in cfg["apply"]
    monkeypatch.delenv("AUTOPILOT_FORCE_IDS", raising=False)
    assert A._forced_ids() == set()
    monkeypatch.setenv("AUTOPILOT_FORCE_IDS", "abc")
    assert A._forced_ids() == {"abc"}
    args = A.build_parser().parse_args(["apply", "--job", "abc", "--force"])
    assert args.force and args.job == "abc"


def test_consent_group_selector_is_module_level():
    assert isinstance(A.group_sel, str) and ".application-question" in A.group_sel


def test_persona_apply_override_is_deep_merged():
    cfg = {"apply": {"answers": {"current_employer": "Acme Labs", "visa": "needs visa"}, "dropdown_answers": {"degree": "MD"}},
           "personas": {"appsec": {}, "creative": {"apply": {"answers": {"current_employer": "Studio"},
                                                             "dropdown_answers": {"degree": ""}}}}}
    c = A.get_persona_cfg(cfg, "creative")
    assert c["apply"]["answers"] == {"current_employer": "Studio", "visa": "needs visa"}
    assert c["apply"]["dropdown_answers"]["degree"] == ""
    assert A.get_persona_cfg(cfg, "appsec")["apply"]["answers"]["current_employer"] == "Acme Labs"
    assert cfg["apply"]["answers"]["current_employer"] == "Acme Labs", "shared config must not be mutated"


def test_real_config_creative_has_no_medical_leak():
    c = A.get_persona_cfg(A.read_json(A.CFG), "creative")
    assert "Web3" not in c["apply"]["answers"]["current_employer"]
    assert "medical" not in c["apply"]["answers"]["why"].lower() and "HealthTech" not in c["apply"]["answers"]["why"]
    assert not c["profile"].get("degree") and not c["profile"].get("school")


@pytest.mark.parametrize("url,name", [
    ("https://boards.greenhouse.io/acme-corp/jobs/123", "Acme Corp"),
    ("https://jobs.lever.co/widgetco/abc", "Widgetco"),
    ("https://careers.example.com/job/1", "Example"),
    ("https://acme.recruitee.com/o/x", "Acme"),
])
def test_company_from_url(url, name):
    assert A._company_from_url(url) == name


def test_read_control_keeps_unknown_actions(tmp_path, monkeypatch):
    ctl = tmp_path / "control.json"
    monkeypatch.setattr(A, "CONTROL", ctl)
    monkeypatch.setattr(A, "_STOP_EVENT", None)
    monkeypatch.setattr(A, "_QUIT_RUN", {"v": False})
    ctl.write_text('{"action":"pause"}')
    assert A.read_control(keep_unknown=True) is None
    assert json.loads(ctl.read_text())["action"] == "pause", "pool parent must not swallow pause"
    assert A.read_control() == "pause" and json.loads(ctl.read_text()) == {}
    ctl.write_text('{"action":"skip"}')
    assert A.read_control(keep_unknown=True) == "skip" and json.loads(ctl.read_text()) == {}


def test_linkedin_apply_takes_the_run_lock():
    src = open(A.__file__, encoding="utf-8").read()
    assert '"linkedin-apply", "rescore"' in src


def _insert_stall(con, job_id, step):
    con.execute("INSERT INTO applications (job_id, applied_at, needs_manual, manual_reason, submitted) VALUES (?, datetime('now'), 1, ?, 0)",
                (job_id, f"stalled the run at step '{step}' after 240s"))


def test_single_early_stall_is_retried_second_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    with db.connect() as con:
        for j in ("early", "late"):
            con.execute("INSERT INTO jobs (id, company, title, url) VALUES (?, 'Co', 'T', 'http://x')", (j,))
        _insert_stall(con, "early", "open page")
        _insert_stall(con, "late", "fill form")
        con.commit()
        skip = db.skip_ids(con)
        assert "early" not in skip, "a stall before the form was touched must stay eligible once"
        assert "late" in skip, "a stall at an unknown/late step could have sent: never repeated"
        _insert_stall(con, "early", "open page")
        con.commit()
        assert "early" in db.skip_ids(con), "two stalls must skip"


def test_unconfirmed_send_counts_against_company_window(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    with db.connect() as con:
        con.execute("INSERT INTO jobs (id, company, title, url) VALUES ('j1', 'Acme Corp', 'T', 'http://x')")
        db.insert_application(con, {"id": "j1", "applied_at": A.now(), "needs_manual": 1, "dry_run": 0,
                                    "manual_reason": "submit did not confirm; outcome unconfirmed"})
        con.commit()
        assert db.recent_company_counts(con, 30) == {"acme-corp": 1}


def test_worker_gives_back_max_slot_on_infra_error(tmp_path, monkeypatch):
    worker = A._worker_process_loop
    setup_run(tmp_path, monkeypatch, 1)

    def boom(job, *a):
        raise A.BrowserLaunchError("no browser")
    monkeypatch.setattr(A, "apply_one", boom)
    with db.connect() as con:
        job = dict(con.execute("SELECT * FROM jobs WHERE id='j0'").fetchone())
    in_q, out_q = queue.Queue(), queue.Queue()
    in_q.put(job)
    started = mp.Value("i", 0)
    worker(1, in_q, out_q, {"apply": {}, "profile": {}, "cv_path": "appsec.pdf"}, False, True, False, False,
           mp.Event(), started, 1)
    assert out_q.get()["result"]["infra_error"] is True
    assert started.value == 0, "an attempt that never reached a browser must not use up --max"


def _infra_worker(wid, in_q, out_q, cfg, dry, submit, show, review, stop, started, limit, skip_gen):
    while True:
        try:
            job = in_q.get(timeout=2)
        except Exception:
            break
        out_q.put({"wid": wid, "result": {"id": job["id"], "company": job.get("company"), "title": job.get("title"),
                                          "infra_error": True, "submitted": False}})


def test_all_infra_errors_exit_4_and_are_not_counted(tmp_path, monkeypatch, capsys):
    args = setup_run(tmp_path, monkeypatch, 2)
    monkeypatch.setattr(A, "_worker_process_loop", _infra_worker)
    with pytest.raises(SystemExit) as e:
        A.apply(args)
    assert e.value.code == 4
    out = capsys.readouterr().out
    assert '"processed": 0' in out


def test_linkedin_postings_are_not_queued_without_optin(tmp_path, monkeypatch):
    args = setup_run(tmp_path, monkeypatch, 1)
    with db.connect() as con:
        con.execute("UPDATE jobs SET url='https://www.linkedin.com/jobs/view/1234567890' WHERE id='j0'")
        con.commit()
    A.apply(args)
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM applications").fetchone()[0] == 0


def test_apply_url_skips_an_already_applied_posting(tmp_path, monkeypatch):
    setup_run(tmp_path, monkeypatch, 0)
    url = "https://boards.greenhouse.io/acme/jobs/777"
    with db.connect() as con:
        con.execute("INSERT INTO jobs (id, company, title, url, score, created_at) VALUES ('old', 'Acme', 'AppSec Engineer', ?, 100, ?)",
                    ("https://job-boards.greenhouse.io/acme/jobs/777", A.now()))
        db.insert_application(con, {"id": "old", "applied_at": A.now(), "submitted": True})
        con.commit()
    calls = []
    monkeypatch.setattr(A, "apply_one", lambda *a, **k: calls.append(a) or {"id": "x"})
    monkeypatch.setattr(A, "submit_block_reason", lambda *a, **k: None)
    args = A.build_parser().parse_args(["apply-url", url, "--submit", "--title", "AppSec Engineer"])
    A.apply_url(args)
    assert calls == [], "a posting already sent must not be re-applied through apply-url"
    args = A.build_parser().parse_args(["apply-url", "https://boards.greenhouse.io/acme/jobs/778", "--submit", "--title", "AppSec Engineer"])
    A.apply_url(args)
    assert len(calls) == 1 and calls[0][0]["company"] == "Acme"
