"""CAPTCHA-only diagnostic. All protected form submissions are blocked."""
import argparse
import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import autopilot as A


def captcha_request(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    return parts.scheme == 'https' and (
        host == 'hcaptcha.com' or host.endswith('.hcaptcha.com') or
        host == 'challenges.cloudflare.com' or
        ((host in ('www.google.com', 'www.recaptcha.net')) and
         parts.path.startswith(('/recaptcha/api2/', '/recaptcha/enterprise/')) and
         parts.path.rsplit('/', 1)[-1] in ('reload', 'userverify', 'replaceimage', 'payload', 'bframe', 'anchor', 'clr')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--kind', required=True, choices=('hcaptcha', 'recaptcha', 'turnstile'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task', choices=('grid', 'spatial', 'drag'))
    parser.add_argument('--browser', choices=('chromium', 'camoufox'), default='chromium')
    parser.add_argument('--local-only', action='store_true', help='Use only loopback local vision providers')
    args = parser.parse_args()
    if urlsplit(args.url).scheme != 'https' or not urlsplit(args.url).hostname:
        parser.error('--url must be HTTPS')
    if args.task and args.kind != 'hcaptcha':
        parser.error('--task applies only to hCaptcha')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {'started_at': A.now(), 'url': args.url, 'kind': args.kind, 'requested_task': args.task,
              'browser': args.browser,
              'external_vision_disabled': args.local_only,
              'ats_submission': False, 'personal_fields_filled': False, 'blocked_writes': [], 'prompts': [], 'vendor_verifications': []}
    cfg = A.read_json(A.CFG)
    cfg['browser'] = {**cfg.get('browser', {}), 'cdp_url': '', 'user_data_dir': '', 'storage_state': ''}
    cfg['browser']['engine'] = args.browser
    A.CONTROL = args.output.with_suffix('.control.json')
    A._QUIT_RUN['v'] = False
    A._STOP_EVENT = threading.Event()
    A._HCAPTCHA_CACHE = args.output.parent / (args.output.stem + '-cache')
    if args.local_only:
        cfg['apply'] = {**cfg.get('apply', {}), 'captcha_local_only': True}
    deadline = threading.Timer(90, A._STOP_EVENT.set)
    deadline.daemon = True
    start = time.monotonic()
    deadline.start()
    try:
        with A.browser_playwright(cfg) as pw:
            browser = A.launch_browser(pw, cfg, headless=True)
            try:
                context = browser.new_context(**({'no_viewport': True} if args.browser == 'camoufox'
                                                else {'viewport': {'width': 1280, 'height': 960}}))
                def guard(route):
                    request = route.request
                    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not captcha_request(request.url):
                        report['blocked_writes'].append(urlsplit(request.url)._replace(query='', fragment='').geturl())
                        route.abort()
                    else:
                        route.continue_()
                context.route('**/*', guard)
                page = context.new_page()
                def verification(response):
                    parts = urlsplit(response.url)
                    if (parts.hostname or '').endswith('.hcaptcha.com') and '/checkcaptcha/' in parts.path:
                        try:
                            data = response.json()
                            report['vendor_verifications'].append({'status': response.status, 'pass': data.get('pass'),
                                'token_returned': bool(data.get('generated_pass_UUID')), 'keys': sorted(data.keys())})
                        except Exception:
                            report['vendor_verifications'].append({'status': response.status, 'json_unavailable': True})
                page.on('response', verification)
                page.goto(args.url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(5000)
                if args.kind == 'hcaptcha':
                    page.add_script_tag(content="if(window.hcaptcha) window.hcaptcha.execute().catch(()=>{});")
                    page.wait_for_timeout(4000)
                    for attempt in range(4):
                        frame = A._hcaptcha_challenge_frame(page)
                        if frame is None:
                            break
                        prompt = A._hcaptcha_read_prompt(frame)
                        task = 'drag' if A._hcaptcha_is_drag(prompt) else 'grid' if A._hcaptcha_is_regular_grid(A._hcaptcha_tile_boxes(frame)) else 'spatial'
                        report['prompts'].append({'prompt': prompt, 'task': task})
                        if not args.task or task == args.task or attempt == 3:
                            break
                        if not A._hcaptcha_click_refresh(page, frame):
                            break
                        page.wait_for_timeout(2000)
                report['challenge_open_before'] = A._captcha_challenge_open(page)
                report['visible_vendor_controls'] = []
                for frame in page.frames:
                    if not captcha_request(frame.url):
                        continue
                    try:
                        controls = frame.locator('input[type="checkbox"],label,#challenge-stage').evaluate_all("els=>els.map(e=>({tag:e.tagName,cls:e.className,box:((r)=>({x:r.x,y:r.y,width:r.width,height:r.height}))(e.getBoundingClientRect())}))")
                        report['visible_vendor_controls'].append({'url': urlsplit(frame.url)._replace(query='',fragment='').geturl(), 'controls':controls})
                    except Exception:
                        pass
                report['solver_result'] = A.try_solve_turnstile_or_captcha(page)
                name = {'hcaptcha':'h-captcha-response','recaptcha':'g-recaptcha-response','turnstile':'cf-turnstile-response'}[args.kind]
                tokens = []
                for scope in [page] + list(page.frames):
                    try:
                        tokens += scope.evaluate("name => [...document.querySelectorAll('[name=\"'+name+'\"]')].map(e=>e.value||'')", name)
                    except Exception:
                        pass
                token = max(tokens, key=len, default='')
                report['vendor_messages'] = []
                for frame in page.frames:
                    if captcha_request(frame.url):
                        try:
                            report['vendor_messages'].append(frame.locator('body').inner_text(timeout=1000)[:500])
                        except Exception:
                            pass
                report.update({'token_length': len(token), 'token_sha256': hashlib.sha256(token.encode()).hexdigest() if token else None,
                               'local_audio_model_loaded': hasattr(A._TL, 'captcha_audio_model'),
                               'challenge_closed': not A._captcha_challenge_open(page), 'verified': A._captcha_verified(page, args.kind),
                               'deadline_reached': A._STOP_EVENT.is_set(), 'page_title': page.title(),
                               'task_observed': report['prompts'][-1]['task'] if report['prompts'] else None})
                page.screenshot(path=str(args.output.with_suffix('.png')))
            finally:
                browser.close()
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {str(exc).splitlines()[0][:200]}'
        report['verified'] = False
    finally:
        deadline.cancel()
        report['finished_at'] = A.now()
        report['elapsed_seconds'] = round(time.monotonic() - start, 2)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True), flush=True)
    return 0 if report.get('verified') and not report.get('deadline_reached') else 1


if __name__ == '__main__':
    raise SystemExit(main())
