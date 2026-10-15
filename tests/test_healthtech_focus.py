import copy

import autopilot as A


def config():
    return {"active_persona": "appsec", "search": {}, "personas": {
        "appsec": {"search": {
            "required_title_terms": ["security engineer"],
            "excluded_title_terms": ["physical security"],
            "sector_focus": "healthtech_medtech",
            "sector_focus_terms": ["healthtech", "medical devices", "patient data", "fhir"],
            "niche_terms": ["healthtech", "medical devices", "patient data", "fhir"],
        }},
        "creative": {"search": {"required_title_terms": ["photographer"]}},
    }}


def job(description, **kwargs):
    return {"title": "Security Engineer", "company": "Example", "location": "Berlin, Germany",
            "persona": "appsec", "description": description, **kwargs}


def test_medical_insurance_is_not_a_healthtech_product():
    cfg = config()
    general = A.score(job("Secure web applications and API endpoints."), cfg)
    insurance = A.score(job("Secure web applications and API endpoints. Benefits: medical, dental, vision and health insurance."), cfg)
    health = A.score(job("Secure web applications and API endpoints in a HealthTech patient data platform."), cfg)
    assert insurance == general
    assert health > general


def test_health_sector_does_not_make_us_only_remote_eligible():
    cfg = config()
    assert A.score(job("Security for patient data", location="Remote - US"), cfg) < 0
    assert A.score(job("Security for patient data. We offer visa sponsorship and relocation assistance.", location="Boston, United States"), cfg) > 0


def test_medical_background_does_not_remove_security_role_requirement():
    assert not A.title_fit(job("HealthTech patient data", title="Clinical Physician"), config())


def test_sector_focus_does_not_change_creative_ranking_or_legacy_config():
    cfg = config()
    creative = job("healthtech medical devices", persona="creative", title="Photographer", location="Remote - US")
    before = A.score(creative, cfg)
    cfg["personas"]["appsec"]["search"]["sector_focus_terms"].append("Example")
    assert A.score(creative, cfg) == before
    legacy = copy.deepcopy(cfg)
    legacy["personas"]["appsec"]["search"].pop("sector_focus")
    assert A._sector_focus_bonus(job("HealthTech"), legacy["personas"]["appsec"]["search"]) == 0
