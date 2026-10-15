"""Public discovery and explicit employer support evidence for the medical search."""
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote, urlparse


# Each pair requires an affirmative employer statement, not an applicant question.
_PROMISES = {
    "en": (r"\b(?:offer|provide|support|sponsor|sponsors|sponsoring)\b[^.\n]{0,70}\b(?:visas?|visa sponsorship|work permits?)\b|\b(?:visa sponsorship|visa support|work permit assistance)\b[^.\n]{0,60}\b(?:available|provided|offered|included)\b",
           r"\brelocation (?:package|support|assistance|bonus|allowance|budget|benefits)\b"),
    "de": (r"\b(?:wir unterstützen|wir bieten|wir übernehmen|unterstützung bei)\b[^.\n]{0,70}\b(?:visum|visa|visumsbeantragung|arbeitserlaubnis)\b|\b(?:visum|visa)[- ](?:unterstützung|sponsoring)\b",
           r"\b(?:umzugskosten|umzugspaket|umzugsunterstützung|relocation[- ]paket|unterstützung beim umzug)\b"),
    "fr": (r"\b(?:nous (?:offrons|proposons|prenons en charge|accompagnons)|aide à l.obtention|prise en charge)\b[^.\n]{0,70}\b(?:visa|permis de travail)\b|\b(?:parrainage|sponsoring|accompagnement) (?:de |du |des )?visas?\b",
           r"\b(?:aide (?:au déménagement|à la relocation)|prise en charge (?:du déménagement|des frais de déménagement)|package de relocation|accompagnement à la mobilité internationale)\b"),
    "es": (r"\b(?:ofrecemos|proporcionamos|patrocinamos|apoyamos|ayuda con|apoyo para|patrocinio de)\b[^.\n]{0,70}\b(?:visas?|visados?|permiso de trabajo)\b",
           r"\b(?:paquete de (?:reubicación|traslado)|ayuda (?:para|con|a) (?:la )?(?:reubicación|mudanza)|apoyo (?:a|para|de) (?:la )?reubicación|asistencia (?:de|para la) reubicación)\b"),
    "pt": (r"\b(?:oferecemos|fornecemos|patrocinamos|apoio (?:ao|para)|patrocínio de|suporte (?:de|para))\b[^.\n]{0,70}\b(?:vistos?|visto de trabalho|autorização de trabalho)\b",
           r"\b(?:pacote de (?:relocação|realocação|mudança)|apoio (?:à|para a) (?:relocação|mudança)|ajuda de custo para mudança|suporte (?:à|para) relocação)\b"),
    "nl": (r"\b(?:wij (?:bieden|ondersteunen|regelen)|ondersteuning bij)\b[^.\n]{0,70}\b(?:visa|visum|werkvergunning)\b|\b(?:visumsponsoring|visumondersteuning)\b",
           r"\b(?:verhuisvergoeding|verhuiskostenvergoeding|relocatiepakket|ondersteuning bij verhuizing|relocatieondersteuning)\b"),
    "it": (r"\b(?:offriamo|forniamo|sponsorizziamo|supporto per|sponsorizzazione del)\b[^.\n]{0,70}\b(?:visto|visti|permesso di lavoro)\b",
           r"\b(?:pacchetto di trasferimento|supporto (?:per|al) trasferimento|assistenza (?:al|per il) trasferimento|rimborso (?:delle )?spese di trasferimento)\b"),
    "pl": (r"\b(?:oferujemy|zapewniamy|pomoc w uzyskaniu|wsparcie w uzyskaniu|sponsorowanie)\b[^.\n]{0,70}\b(?:wizy|wizę|wiz|zezwolenia na pracę)\b",
           r"\b(?:pakiet relokacyjny|wsparcie relokacyjne|pomoc w relokacji|pokrycie kosztów przeprowadzki)\b"),
    "ja": (r"(?:ビザ|就労許可)(?:の)?(?:サポート|支援|スポンサー|取得支援)",
           r"(?:引越し|引っ越し|転居|移住|赴任)(?:費用|手当|支援|サポート)|リロケーション(?:パッケージ|支援)"),
    "zh": (r"(?:提供|支持|协助|办理)[^。\n]{0,30}(?:工作签证|签证担保|签证支持|工作许可)",
           r"(?:搬迁补助|搬家补贴|搬迁支持|搬迁援助|安家费| relocation package)"),
}
_SUPPORT_TERMS = r"visa|visum|visados?|visas?|visto|wizy|wizę|immigration|sponsorship|relocation|umzug\w*|déménagement|reubicación|mudanza|relocação|realocação|verhuis\w*|relocatie\w*|trasferimento|relokac\w*|przeprowadzki|ビザ|転居|引越し|签证|搬迁|搬家"
_NEGATIVE = r"\b(?:no|not|cannot|unable|without|kein\w*|nicht|ohne|keine|pas|aucun\w*|sans|não|sem|geen|niet|zonder|non|nessun\w*|senza|nie|bez|brak)\b|不提供|不支持|なし|対象外"
_CONDITIONAL = re.compile(r"\b(?:may|might|case.by.case|not guaranteed|transfer.only|possibly|depending on|subject to|at (?:our|company) discretion|gegebenenfalls|eventuell|nach einzelfall|peut|éventuellement|selon le cas|podemos|podría|caso a caso|poderemos|eventualmente|mogelijk|eventueel|potremmo|możliwe|indywidualnie)\b|場合によ|可能提供|视情况", re.I)


def support_reason(text):
    text = html.unescape(html.unescape(text or ""))
    text = re.sub(r"(?i)</(?:p|li)>|<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    clauses = [re.sub(r"\s+", " ", s).strip() for s in re.split(r"(?<=[.!?;。！？])\s*|\n+", text)]
    for s in clauses:
        if "?" in s or "？" in s:
            continue
        if re.search(_SUPPORT_TERMS, s, re.I) and re.search(_NEGATIVE, s, re.I):
            # Negation must qualify support, not an unrelated "no previous experience".
            if re.search(r"(?:" + _NEGATIVE + r")[^,;。.!?]{0,55}(?:" + _SUPPORT_TERMS + r")|(?:" + _SUPPORT_TERMS + r")[^,;。.!?]{0,55}(?:" + _NEGATIVE + r")", s, re.I):
                return "support_excluded"
    usable = [s for s in clauses if "?" not in s and "？" not in s and not _CONDITIONAL.search(s)]
    visa = any(re.search(v, s, re.I) for v, _ in _PROMISES.values() for s in usable)
    relocation = any(re.search(r, s, re.I) for _, r in _PROMISES.values() for s in usable)
    return "visa_support_not_proven" if not visa else "relocation_not_proven" if not relocation else None


def in_russia(job):
    # Check posting geography only; the applicant's Russian citizenship is not a job location.
    return bool(re.search(r"\b(?:russia|russian federation|россия|российская федерация|russland|russie|rusia|rússia|rusland|rosja|ru|moscow|москва|saint petersburg|санкт-петербург|krasnoyarsk|novosibirsk)\b|俄罗斯|ロシア", str(job.get("location") or ""), re.I))


def primary_posting(job, fetch_text):
    """Replace aggregator claims with the current public ATS description and location."""
    from ats_schema import find_on_company_board
    url = job.get("url") or ""
    p = urlparse(url)
    allowed = {"job-boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.greenhouse.io", "boards.eu.greenhouse.io", "jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com", "apply.workable.com", "jobs.smartrecruiters.com", "jobs.gem.com"}
    def supported(host):
        return host in allowed or bool(re.fullmatch(r"[\w-]+\.wd\d+\.myworkdayjobs\.com", host or ""))
    if p.scheme != "https" or not supported(p.hostname):
        url = find_on_company_board(job.get("company"), job.get("title")) or ""
        p = urlparse(url)
    if p.scheme != "https" or not supported(p.hostname):
        return None
    parts = [unquote(x) for x in p.path.split("/") if x]
    if not all(re.fullmatch(r"[\w-]+", x) for x in parts):
        return None
    data, title, description, location = {}, "", "", ""
    if p.hostname.endswith(".myworkdayjobs.com") and "job" in parts and parts.index("job") >= 1:
        index = parts.index("job")
        tenant, site = p.hostname.split(".")[0], parts[index - 1]
        data = json.loads(fetch_text(f"https://{p.hostname}/wday/cxs/{tenant}/{site}/{'/'.join(parts[index:])}", timeout=20)).get("jobPostingInfo") or {}
        title, description = data.get("title"), data.get("jobDescription")
        location = data.get("location") or ""
    elif p.hostname == "jobs.gem.com" and len(parts) >= 2:
        from medical_boards import gem_board
        jobs = gem_board(f"https://jobs.gem.com/{parts[0]}", lambda j: urlparse(j["url"]).path.endswith("/" + parts[1]))
        data = next((j for j in jobs if urlparse(j["url"]).path.endswith("/" + parts[1])), {})
        title, description, location = data.get("title"), data.get("description"), data.get("location")
        job = {**job, "company": data.get("company") or job.get("company")}
    elif p.hostname.endswith("greenhouse.io") and len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
        data = json.loads(fetch_text(f"https://boards-api.greenhouse.io/v1/boards/{quote(parts[0])}/jobs/{parts[2]}", timeout=20))
        title, description = data.get("title"), data.get("content")
        location = (data.get("location") or {}).get("name") or ""
    elif p.hostname in {"jobs.lever.co", "jobs.eu.lever.co"} and len(parts) == 2:
        api = "api.eu.lever.co" if p.hostname == "jobs.eu.lever.co" else "api.lever.co"
        data = json.loads(fetch_text(f"https://{api}/v0/postings/{parts[0]}/{parts[1]}?mode=json", timeout=20))
        title = data.get("text")
        description = "\n".join([data.get("descriptionPlain") or data.get("description") or "", *(x.get("content") or "" for x in data.get("lists") or []), data.get("additionalPlain") or data.get("additional") or ""])
        location = (data.get("categories") or {}).get("location") or ""
    elif p.hostname == "jobs.ashbyhq.com" and len(parts) >= 2:
        data = json.loads(fetch_text(f"https://api.ashbyhq.com/posting-api/job-board/{parts[0]}", timeout=20))
        data = next((x for x in data.get("jobs") or [] if x.get("id") == parts[1] or urlparse(x.get("jobUrl") or "").path.rstrip("/").endswith("/" + parts[1])), {})
        title = data.get("title")
        description = data.get("descriptionHtml") or data.get("descriptionPlain")
        location = data.get("location") or ""
    elif p.hostname == "apply.workable.com" and "j" in parts and len(parts) > parts.index("j") + 1:
        code = parts[parts.index("j") + 1]
        data = json.loads(fetch_text(f"https://apply.workable.com/api/v2/jobs/{code}", timeout=20))
        title = data.get("title")
        description = "\n".join(str(data.get(x) or "") for x in ("description", "requirements", "benefits"))
        loc = data.get("location") or {}
        location = ", ".join(str(loc.get(x) or "") for x in ("city", "country"))
    elif p.hostname == "jobs.smartrecruiters.com" and len(parts) >= 2:
        posting_id = parts[1].split("-")[0]
        if not posting_id.isdigit():
            return None
        data = json.loads(fetch_text(f"https://api.smartrecruiters.com/v1/companies/{parts[0]}/postings/{posting_id}?destination=PUBLIC", timeout=20))
        title = data.get("name")
        description = "\n".join(x.get("text") or "" for x in ((data.get("jobAd") or {}).get("sections") or {}).values() if isinstance(x, dict))
        loc = data.get("location") or {}
        location = ", ".join(str(loc.get(x) or "") for x in ("city", "country"))
    if not title or not description:
        return None
    # Retain discovery context for reporting; none of its support claims survive hydration.
    employer = data.get("company") or {}
    employer = employer.get("name") if isinstance(employer, dict) else employer
    fallback = p.hostname.split(".")[0] if p.hostname.endswith(".myworkdayjobs.com") else parts[0] if parts else ""
    company = data.get("company_name") or employer or job.get("company") or fallback
    return {**job, "company": company, "source": "vendor", "discovered_via": job.get("source"), "url": url,
            "title": title, "description": description, "location": location}


def startups(fetch_text, role_fit):
    """Medical YC companies in every region, including companies outside Europe."""
    from ats_schema import company_board
    companies = json.loads(fetch_text("https://yc-oss.github.io/api/companies/hiring.json", timeout=60))
    medical = re.compile(r"\b(?:healthcare|healthtech|medtech|medical|digital health|biotech|biotechnology|therapeutics|diagnostics|genomics|clinical|patient|hospital|pharmaceutical)\b", re.I)
    companies = [c for c in companies if c.get("status") == "Active" and medical.search(" ".join(str(c.get(k) or "") for k in ("name", "one_liner", "industry", "industries", "tags")))]
    def read(c):
        jobs = []
        for title, url, location in company_board(c.get("name") or "", timeout=5, slugs=(c.get("slug") or "",)):
            j = {"company": c.get("name"), "title": title, "url": url, "location": location,
                 "description": c.get("one_liner") or "", "source": "medical_startups"}
            if role_fit(j):
                primary = primary_posting(j, fetch_text)
                if primary:
                    primary["sector_context"] = c.get("one_liner") or ""
                    primary["description"] += "\nEmployer industry: HealthTech/MedTech (public YC company directory)."
                    jobs.append(primary)
        return jobs
    jobs, errors = [], []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [(c, pool.submit(read, c)) for c in companies]
        for c, future in futures:
            try:
                jobs.extend(future.result())
            except Exception as exc:
                errors.append({"company": c.get("name"), "error": str(exc)[:200]})
    return jobs, {"medical_companies": len(companies), "security_postings": len(jobs), "errors": errors}
