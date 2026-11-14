#!/usr/bin/env python3
"""GitHub jobs.json change monitor -> CV-filtered Telegram alerts. Stdlib only."""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OWNER_REPO = os.getenv('SOURCE_REPO', 'ashishtiwari03/work-in-germany')
SOURCE_PATH = 'data/jobs.json'
STATE = Path('state/seen.json')
MAX_ALERTS = int(os.getenv('MAX_ALERTS', '12'))
USER_AGENT = 'GermanyAIJobAlerts/1.0 (+https://github.com)'


def fetch(url, headers=None, timeout=35):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(), response.headers


def source_revision():
    url = f'https://api.github.com/repos/{OWNER_REPO}/contents/{SOURCE_PATH}'
    token = os.getenv('GITHUB_SOURCE_TOKEN', '').strip()
    h = {'Authorization': f'Bearer {token}'} if token else {}
    raw, _ = fetch(url, h)
    metadata = json.loads(raw)
    if not metadata.get('sha'):
        raise ValueError('Missing jobs.json blob SHA')
    return metadata['sha'], metadata.get('download_url') or f'https://raw.githubusercontent.com/{OWNER_REPO}/main/{SOURCE_PATH}'


def get_jobs(data):
    """Support list, {jobs: [...]}, nested grouped arrays, and API result objects."""
    found = []
    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
        elif isinstance(node, dict):
            keys = set(node)
            if keys.intersection({'title', 'job_title', 'position', 'jobTitle'}) and keys.intersection({'url','link','apply_url','job_url','application_url','applyUrl'}):
                found.append(node)
            else:
                for val in node.values():
                    if isinstance(val, (list, dict)):
                        walk(val, depth + 1)
    walk(data)
    return found


def pick(job, *names):
    for key in names:
        val = job.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for inner in ('name', 'title', 'value', 'url', 'city'):
                if isinstance(val.get(inner), str) and val[inner].strip():
                    return val[inner].strip()
    return ''


def normalized(job):
    title = pick(job, 'title','job_title','position','jobTitle')
    url = pick(job, 'url','apply_url','application_url','job_url','link','applyUrl')
    company = pick(job, 'company','company_name','employer','organization')
    location = pick(job, 'location','city','job_location','jobLocation','locations')
    desc = pick(job, 'description','job_description','summary','snippet','requirements','category','tags')
    return {'title': title, 'url': url, 'company': company or 'Company not stated', 'location': location, 'description': desc}


def clean_url(url):
    try:
        p = urllib.parse.urlsplit(url.strip())
        qs = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        qs = [(k,v) for k,v in qs if not (k.lower().startswith('utm_') or k.lower() in {'ref','source','trk','trackingid'})]
        return urllib.parse.urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/'),urllib.parse.urlencode(qs),'') )
    except Exception:
        return url


def job_id(job):
    url = clean_url(job['url'])
    identity = url if url else '|'.join((job['company'],job['title'],job['location']))
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]


ROLE_PATTERN = re.compile(r'\b(machine learning|ml engineer|ai engineer|ai developer|applied ai|generative ai|genai|llm|large language model|data scientist|data science|mlops|agentic ai|ai automation|ki.?(entwickler|ingenieur)|künstliche intelligenz|research engineer|nlp engineer|computer vision engineer|deep learning)\b', re.I)
NEGATIVE_PATTERN = re.compile(r'\b(senior|sr\.?|staff|principal|lead|head of|director|vp|vice president|chief|manager|architect)\b', re.I)
SECONDARY_PATTERN = re.compile(r'\b(data engineer|dateningenieur|research scientist)\b', re.I)
ML_EVIDENCE = re.compile(r'\b(ml|machine learning|llm|rag|vector|pytorch|tensorflow|model training|feature engineering|retrieval|ai agents|langgraph|mle)\b', re.I)
ENTRY_PATTERN = re.compile(r'\b(junior|graduate|entry.level|associate|trainee|working student|werkstudent|berufseinsteiger)\b', re.I)


def fit_score(job):
    title = job['title']
    evidence = ' '.join((job['title'], job['description']))
    if NEGATIVE_PATTERN.search(title):
        return -1
    role = bool(ROLE_PATTERN.search(title))
    secondary = bool(SECONDARY_PATTERN.search(title) and ML_EVIDENCE.search(evidence))
    if not (role or secondary):
        return -1
    # Location is a preference check, not a claim about remote eligibility.
    location = job['location'].lower()
    if re.search(r'\b(switzerland|schweiz|austria|österreich|usa|united states|uk|united kingdom|india)\b', location) and not re.search(r'\b(germany|deutschland|remote.eu|remote europe|european union)\b', location):
        return -1
    score = 2 if role else 0
    if ENTRY_PATTERN.search(title):
        score += 3
    if re.search(r'\b(ai engineer|machine learning engineer|applied ai|llm engineer|generative ai|data scientist|mlops)\b', title, re.I):
        score += 3
    if ML_EVIDENCE.search(evidence):
        score += 1
    if re.search(r'\b(python|fastapi|docker|scikit.learn|pytorch|rag|langgraph|forecasting|time.series)\b', evidence, re.I):
        score += 1
    return score


def application_url(url):
    try:
        p=urllib.parse.urlsplit(url)
        if p.scheme != 'https' or not p.netloc or p.username or p.password:
            return False
        host = (p.hostname or '').lower()
        banned = ('linkedin.com','indeed.com','stepstone.de','glassdoor.com','jobrapido.com','adzuna.','jooble.org','monster.','xing.com','reddit.com','github.com')
        if any(host == b or host.endswith('.'+b) or (b.endswith('.') and b in host) for b in banned):
            return False
        return True
    except ValueError:
        return False


def telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN','').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat_id:
        raise RuntimeError('Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as GitHub Actions secrets')
    payload = urllib.parse.urlencode({'chat_id':chat_id,'text':text,'disable_web_page_preview':'true'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',data=payload,method='POST')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=25) as response:
                result = json.loads(response.read())
                if not result.get('ok'):
                    raise RuntimeError('Telegram response was not ok')
                return
        except (urllib.error.URLError, RuntimeError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def load_state():
    if not STATE.exists():
        return None
    return json.loads(STATE.read_text(encoding='utf-8'))


def save_state(sha, ids):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    value={'sha':sha,'seen_ids':sorted(ids),'checked_at':datetime.now(timezone.utc).isoformat()}
    tmp=STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    tmp.replace(STATE)


def main():
    sha, download_url = source_revision()
    old = load_state()
    if old and sha == old.get('sha'):
        print('No change in jobs.json SHA')
        return
    print('New jobs.json revision:',sha)
    raw,_=fetch(download_url,headers={'Accept':'application/json'},timeout=100)
    jobs = get_jobs(json.loads(raw))
    if not jobs:
        raise ValueError('No recognizable jobs found: source schema may have changed. State NOT updated.')
    current = {job_id(n):n for n in (normalized(j) for j in jobs)}
    old_ids = set(old.get('seen_ids',[])) if old else set()
    if old is None:
        save_state(sha, current.keys())
        telegram(f'✅ Germany AI Job Alerts સેટઅપ સફળ!\nBaseline: {len(current)} jobs. હવે પછી ઉમેરાયેલી યોગ્ય jobs માટે જ Alert આવશે.\nSource: {OWNER_REPO}')
        print('Initialized baseline without alerts')
        return
    new = [v for k,v in current.items() if k not in old_ids]
    candidate = [j for j in new if application_url(j['url']) and fit_score(j)>=0]
    candidate.sort(key=lambda j:fit_score(j),reverse=True)
    print(f'Total: {len(current)}, newly observed: {len(new)}, matching with eligible URLs: {len(candidate)}')
    # Automated text screening cannot prove a vacancy is still open. Label links explicitly as unverified.
    if candidate:
        top=candidate[:MAX_ALERTS]
        header=f'🇩🇪 Germany AI Jobs | નવી JSON Update\nનવી ઓળખાયેલી Jobs: {len(new)} | CV title-filter matches: {len(candidate)}\nઆ ફક્ત GitHub પર નવી ઓળખાયેલી Jobs છે, નવા market postings હોવાની ખાતરી નથી.\n\n'
        telegram(header)
        for i,j in enumerate(top,1):
            message=(f'{i}. {j["title"]}\n🏢 {j["company"]}\n📍 {j["location"] or "Location unspecified"}\n'
                     f'🔗 {j["url"]}\n⚠️ Live vacancy, official employer ownership અને language/visa requirements મેન્યુઅલી ચકાસો.')
            telegram(message[:3900])
        if len(candidate)>len(top):
            telegram(f'આ Update માં હજુ {len(candidate)-len(top)} title-filter matches છે. MAX_ALERTS બદલીને વધુ મેળવી શકો છો.')
    save_state(sha, current.keys())


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
