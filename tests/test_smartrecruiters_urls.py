import io
import json
from unittest.mock import patch

import autopilot as A
import harvest_ats_boards as H


BOARD = "https://api.smartrecruiters.com/v1/companies/ADGAGroupConsultantsInc1/postings"
PUBLIC = "https://jobs.smartrecruiters.com/ADGAGroupConsultantsInc1/744000134530759"


def test_api_board_url_produces_public_job_link():
    payload = {"content": [{"id": "744000134530759", "name": "Cybersecurity Inspector"}]}
    with patch.object(H.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
        result = H.fetch_smartrecruiters(BOARD)
    assert result[0]["url"] == PUBLIC


def test_legacy_links_and_title_variants_share_posting_identity():
    legacy = f"https://jobs.smartrecruiters.com/{BOARD}/744000134530759"
    assert A.canonical_url(legacy) == PUBLIC
    assert A._ats_job_key(legacy) == A._ats_job_key(PUBLIC + "-cybersecurity-inspector")
    assert A._ats_job_key(PUBLIC) != A._ats_job_key(PUBLIC.replace("759", "760"))
    foreign = legacy.replace("api.smartrecruiters.com", "example.com")
    assert A.canonical_url(foreign) == foreign
    assert A._ats_job_key(foreign) is None


def test_restricted_application_page_is_reported_as_access_failure(monkeypatch):
    monkeypatch.setattr(A, "page_body_all", lambda _: "Access is temporarily restricted")
    assert A.is_bot_wall(object())
    monkeypatch.setattr(A, "page_body_all", lambda _: "Submit your application")
    assert not A.is_bot_wall(object())
