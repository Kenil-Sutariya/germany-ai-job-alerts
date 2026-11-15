#!/usr/bin/env python3
"""Free, rule-based GitHub dataset change monitor and Telegram CV alerts."""
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

REPO = os.getenv('SOURCE_REPO', 'ashishtiwari03/work-in-germany')
PATH = 'data/jobs.json'
STATE = Path('state/seen.json')
MAX_ALERTS = int(os.getenv('MAX_ALERTS', '12'))
AGENT = 'GermanyAIJobAlerts/2.0'

SKILLS = {
    'Python': r'\bpython\b', 'SQL': r'\bsql\b', 'PyTorch': r'\bpytorch\b',
    'scikit-learn': r'\bscikit[- ]learn\b', 'LangGraph': r'\blanggraph\b',
    'RAG': r'\brag\b|retrieval.augmented', 'LLM': r'\bllms?\b|large language models?',
    'AI agents': r'\b(ai|llm) agents?\b|agentic',
    'Vector databases': r'vector (database|db|store)|\b(pinecone|weaviate|milvus|qdrant|faiss)\b',
    'FastAPI': r'\bfastapi\b', 'Docker': r'\bdocker\b',
    'AWS': r'\baws\b|amazon web services', 'Azure': r'\bazure\b',
    'MLflow': r'\bmlflow\b', 'n8n': r'\bn8n\b',
    'Time series': r'time.series|forecasting|zeitreihen',
    'Machine learning': r'machine learning|maschinelles lernen',
}
ROLE = re.compile(r'\b(machine learning|ml engineer|ai engineer|ai developer|applied ai|generative ai|genai|llm|data scientist|data science|mlops|agentic ai|ai automation|ki[- ]?(entwickler|ingenieur)|künstliche intelligenz|research engineer|nlp engineer|computer vision|deep learning)\b', re.I)
SECONDARY = re.compile(r'\b(data engineer|dateningenieur|research scientist)\b', re.I)
ML = re.compile(r'\b(machine learning|\bml\b|llm|rag|pytorch|tensorflow|langgraph|model training|ai agents|vector database)\b', re.I)
SENIOR = re.compile(r'\b(senior|sr\.?|staff|principal|lead|head of|director|vp|chief|manager|architect)\b', re.I)
ENTRY = re.compile(r'\b(junior|graduate|entry.level|associate|trainee|berufseinsteiger)\b', re.I)
COUNTRIES = re.compile(r'\b(switzerland|schweiz|austria|österreich|usa|united states|united kingdom|uk|india)\b', re.I)
GERMANY = re.compile(r'\b(germany|deutschland|remote.eu|remote europe|european union)\b', re.I)
BOARDS = ('linkedin.com','indeed.com','stepstone.de','glassdoor.com','jobrapido.com','adzuna.com','jooble.org','monster.com','xing.com','reddit.com','github.com')


def request(url, headers=None, timeout=45):
    h = {'User-Agent': AGENT, 'Accept': 'application/vnd.github+json'}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def source():
    token = os.getenv('GITHUB_SOURCE_TOKEN', '').strip()
    h = {'Authorization': 'Bearer ' + token} if token else {}
    raw = request(f'https://api.github.com/repos/{REPO}/contents/{PATH}', h)
    item = json.loads(raw)
    if not item.get('sha'):
        raise ValueError('Source jobs.json SHA missing')
    return item['sha'], item.get('download_url') or f'https://raw.githubusercontent.com/{REPO}/main/{PATH}'


def pick(job, *keys):
    for key in keys:
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for sub in ('name','title','value','url','city'):
                if isinstance(value.get(sub), str) and value[sub].strip():
                    return value[sub].strip()
        if isinstance(value, list):
            parts = [v for v in value if isinstance(v, str)]
            if parts:
                return ', '.join(parts)
    return ''


def jobs_in(node):
    found = []
    def walk(value, depth=0):
        if depth > 7:
            return
        if isinstance(value, list):
            for item in value:
                walk(item, depth+1)
        elif isinstance(value, dict):
            if set(value) & {'title','job_title','position','jobTitle'} and set(value) & {'url','link','apply_url','job_url','application_url','applyUrl'}:
                found.append(value)
            else:
                for item in value.values():
                    if isinstance(item, (dict,list)):
                        walk(item, depth+1)
    walk(node)
    return found


def normalize(job):
    return {
        'title': pick(job,'title','job_title','position','jobTitle'),
        'url': pick(job,'url','apply_url','application_url','job_url','link','applyUrl'),
        'company': pick(job,'company','company_name','employer','organization') or 'Company not stated',
        'location': pick(job,'location','city','job_location','jobLocation','locations'),
        'description': pick(job,'description','job_description','summary','snippet','requirements','tags'),
    }


def clean_url(url):
    p = urllib.parse.urlsplit(url.strip())
    qs = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    qs = [(k,v) for k,v in qs if not (k.lower().startswith('utm_') or k.lower() in {'ref','source','trk','trackingid'})]
    return urllib.parse.urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/'),urllib.parse.urlencode(qs),''))


def identity(job):
    url = clean_url(job['url'])
    key = url or '|'.join((job['company'],job['title'],job['location']))
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def acceptable_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or '').lower()
        return bool(p.scheme == 'https' and host and not p.username and not p.password and not any(host == x or host.endswith('.'+x) for x in BOARDS))
    except ValueError:
        return False


def language(text):
    if re.search(r'\b(german|deutsch(?:kenntnisse)?|deutschsprachig)\b[^.\n]{0,75}\b(c1|c2)\b|\b(c1|c2)\b[^.\n]{0,45}\b(german|deutsch)\b',text,re.I):
        return 'German C1/C2 mentioned (check requirement)'
    if re.search(r'\b(german|deutsch(?:kenntnisse)?|deutschsprachig)\b[^.\n]{0,75}\bb2\b|\bb2\b[^.\n]{0,45}\b(german|deutsch)\b',text,re.I):
        return 'German B2 mentioned (check requirement)'
    if re.search(r'\b(german|deutsch(?:kenntnisse)?|deutschsprachig)\b[^.\n]{0,75}\bb1\b|\bb1\b[^.\n]{0,45}\b(german|deutsch)\b',text,re.I):
        return 'German B1 mentioned (check requirement)'
    if re.search(r'\b(german|deutsch(?:kenntnisse)?|deutschsprachig)\b',text,re.I):
        return 'German mentioned; level unclear'
    if re.search(r'\benglish\b|englisch',text,re.I):
        return 'English mentioned; German requirement unknown'
    return 'Language requirement not stated in dataset'


def evaluate(job):
    title = job['title']
    text = title + '\n' + job['description']
    if SENIOR.search(title):
        return None
    primary = bool(ROLE.search(title))
    secondary = bool(SECONDARY.search(title) and ML.search(text))
    if not (primary or secondary):
        return None
    loc = job['location']
    if COUNTRIES.search(loc) and not GERMANY.search(loc):
        return None
    matched = [name for name,pattern in SKILLS.items() if re.search(pattern,text,re.I)]
    # Missing from the available text is not proof the employer requires or excludes a skill.
    absent = [name for name in ('Python','PyTorch','LangGraph','RAG','FastAPI','Docker','AWS','Azure') if name not in matched]
    score = (4 if primary else 1) + (3 if ENTRY.search(title) else 0) + min(5,len(matched))
    if not job['description']:
        score = min(score, 6)
    return {'score':score,'matched':matched,'not_mentioned':absent,'language':language(job['description']), 'description_available':bool(job['description'])}


def send(text):
    token = os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    chat = os.getenv('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat:
        raise RuntimeError('Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID')
    body = urllib.parse.urlencode({'chat_id':chat,'text':text[:3900],'disable_web_page_preview':'true'}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=body,method='POST')
            with urllib.request.urlopen(req,timeout=25) as resp:
                if not json.loads(resp.read()).get('ok'):
                    raise RuntimeError('Telegram rejected message')
            return
        except (urllib.error.URLError,RuntimeError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)


def save(sha,ids):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    value = {'sha':sha,'seen_ids':sorted(ids),'checked_at':datetime.now(timezone.utc).isoformat()}
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    tmp.replace(STATE)


def main():
    sha,url = source()
    old = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else None
    if old and old.get('sha') == sha:
        print('No new jobs.json revision')
        return
    dataset = json.loads(request(url,{'Accept':'application/json'},timeout=100))
    jobs = jobs_in(dataset)
    if not jobs:
        raise ValueError('No jobs recognized: source format may have changed; state untouched')
    current = {identity(n):n for n in (normalize(j) for j in jobs)}
    if old is None:
        save(sha,current.keys())
        send(f'✅ Germany AI Job Alerts baseline initialized: {len(current)} listings. Future dataset additions will be screened.')
        return
    new = [j for key,j in current.items() if key not in set(old.get('seen_ids',[]))]
    candidates = []
    for j in new:
        if not acceptable_url(j['url']):
            continue
        info = evaluate(j)
        if info is not None:
            candidates.append((info['score'],j,info))
    candidates.sort(key=lambda x:x[0],reverse=True)
    print(f'Blob SHA: {sha}; dataset: {len(current)}; new: {len(new)}; rule matches: {len(candidates)}')
    # State only advances after all alerts succeed; failure can retry without losing alerts.
    if candidates:
        send(f'🇩🇪 Germany AI Job Alerts\nGitHub dataset newly observed: {len(new)}\nRule-based candidates: {len(candidates)}\nBlob SHA: {sha[:12]}\nNot proof of newly posted or still-open vacancies.')
        for index,(score,j,info) in enumerate(candidates[:MAX_ALERTS],1):
            matches = ', '.join(info['matched'][:10]) or 'None explicitly stated'
            missing = ', '.join(info['not_mentioned'][:8]) or 'None from tracked list'
            msg = (f'{index}. {j["title"]}\n🏢 {j["company"]}\n📍 {j["location"] or "Not stated"}\n'
                   f'📊 Rule-based relevance: {score}/12 (not hiring probability)\n'
                   f'✅ Skills found in available text: {matches}\n'
                   f'❔ Skills not mentioned: {missing}\n'
                   f'🗣 {info["language"]}\n'
                   f'📄 Description: {"available" if info["description_available"] else "missing/incomplete"}\n'
                   f'🔗 {j["url"]}\n'
                   f'⚠️ Link is not independently verified as an open employer vacancy. Check official employer careers page before applying.')
            send(msg)
    save(sha,current.keys())

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}',file=sys.stderr)
        sys.exit(1)
