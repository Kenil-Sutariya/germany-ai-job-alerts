#!/usr/bin/env python3
"""Germany AI job monitor 4.2: standard-library only, no paid AI.

Rules: new Git blob only; once-per-rules-version replay for missed listings;
never treat job-board links, generic ATS landing pages, or an EU remote label
as proof of an open Germany employment vacancy or EU Blue Card eligibility.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = os.getenv('SOURCE_REPO', 'ashishtiwari03/work-in-germany')
DATA_PATH = 'data/jobs.json'
STATE = Path('state/seen.json')
REVIEW_FILE = Path('state/review_queue.json')
RULES_VERSION = '4.2'
# No notification caps: every eligible job is reported once.
# Telegram messages are batched and paced to avoid exceeding platform limits.
# For the user's missed 165 jobs. This compares the prior commit against HEAD,
# once per RULES_VERSION, without re-alerting anything already sent by v4.
REPLAY_PREVIOUS_COMMIT = os.getenv('REPLAY_PREVIOUS_COMMIT',
    '1c8a3633f12dab43329be3adadeae08560ebd930').strip()
AGENT = 'GermanyAIJobAlerts/4.2 (+https://github.com/ashishtiwari03/work-in-germany)'
TIMEOUT = 18

BOARD_DOMAINS = ('linkedin.com', 'indeed.com', 'stepstone.de', 'glassdoor.com',
                 'xing.com', 'monster.com', 'jooble.org', 'adzuna.com',
                 'jobrapido.com', 'jobbird.com', 'reddit.com', 'github.com')
ATS_DOMAINS = ('greenhouse.io', 'lever.co', 'ashbyhq.com', 'smartrecruiters.com',
               'myworkdayjobs.com', 'workdayjobs.com', 'jobs.personio.de',
               'jobs.personio.com', 'recruitee.com', 'teamtailor.com',
               'bamboohr.com', 'join.com', 'workable.com', 'softgarden.io',
               'softgarden.de', 'apply.workable.com', 'taleo.net', 'icims.com')
# Match an actual vacancy rather than a generic page that happens to say Apply.
APPLY_MARKERS = re.compile(r'apply now|apply for this job|apply for this position|'
                           r'submit application|jetzt bewerben|auf diese stelle bewerben|'
                           r'apply to this job|bewerbung absenden', re.I)
CLOSED_MARKERS = re.compile(r'no longer accepting applications|this job is (?:no longer )?available|'
                            r'position has been filled|job has expired|'
                            r'stelle (?:ist )?besetzt|stellenanzeige nicht mehr verfügbar', re.I)
# There is deliberately no exhaustive list of cities. DE address, DE country field,
# or explicit German wording is stronger evidence than a limited city whitelist.
DE_LOC = re.compile(r'\b(?:germany|deutschland|bundesweit|remote germany|remote de|'
                    r'deutschlandweit|deutschlandweit remote|deutschland remote|'
                    r'de[- ](?:berlin|hamburg|munich|münchen|frankfurt|stuttgart)|'
                    r'berlin|hamburg|münchen|munich|frankfurt|stuttgart|köln|cologne|'
                    r'düsseldorf|dortmund|leipzig|dresden|hannover|bremen|bonn|'
                    r'nürnberg|nuremberg|karlsruhe|mannheim|heidelberg|mainz|'
                    r'wiesbaden|freiburg|augsburg|essen|aachen|potsdam|ulm|'
                    r'göttingen|regensburg|braunschweig|kiel|münster|bochum)\b', re.I)
DE_CONTRACT = re.compile(r'\b(?:german employment contract|german labor contract|'
                         r'german labour contract|german payroll|german entity|'
                         r'working from germany permitted|work from germany allowed|'
                         r'deutscher arbeitsvertrag|anstellung in deutschland|'
                         r'arbeitsvertrag in deutschland)\b', re.I)
OTHER_ONLY = re.compile(r'\b(?:switzerland|schweiz|austria|österreich|'
                        r'united states|usa|united kingdom|uk only|india only)\b', re.I)
BAD_CONTRACT = re.compile(r'\b(?:freelance|freelancer|independent contractor|'
                          r'contractor only|self.employed|selbstständig|freiberuflich)\b', re.I)
STUDENT = re.compile(r'\b(?:werkstudent|working student|praktikum|internship|'
                     r'intern\b|ausbildung|apprentice|thesis student|duales studium)\b', re.I)
SENIOR = re.compile(r'\b(?:senior|sr\.?|staff|principal|lead\b|head of|director|'
                    r'vice president|vp|chief|distinguished)\b', re.I)
JUNIOR = re.compile(r'\b(?:junior|graduate|entry level|associate|trainee|'
                    r'berufseinsteiger|early career)\b', re.I)
PRIMARY = re.compile(
    r'\b(?:artificial intelligence|machine learning|maschinelles lernen|'
    r'data scientis\w*|applied ai|generative ai|genai|gen ai|llm|mlops|'
    r'ai[ /]ml|ai(?:\s+(?:solutions?|application|platform|automation|acoustic))?\s+'
    r'(?:engineer|developer|specialist|consultant|researcher|scientist)|'
    r'ki\s+(?:entwickler|ingenieur|spezialist)|ki entwickl\w*|'
    r'nlp engineer|deep learning|computer vision engineer|'
    r'ai automation|agentic ai)\b', re.I)
SECONDARY = re.compile(r'\b(?:data engineer|software engineer|software developer|'
                       r'product engineer|research engineer|research scientist|'
                       r'cloud engineer|forward deployed engineer|'
                       r'full.?stack developer|automation engineer|'
                       r'knowledge graphs?|graph data)\b', re.I)
TECH = re.compile(r'\b(?:machine learning|ml\b|llm\b|genai\b|generative ai|'
                  r'rag\b|langgraph|pytorch|tensorflow|deep learning|'
                  r'artificial intelligence|ai\b|ki\b|ai[ /]ml|agentic|ai agents?|'
                  r'knowledge graphs?|vector (?:db|database|store)|'
                  r'neural networks?|model serving|computer vision|'
                  r'mlflow|fine.tuning|nlp\b)\b', re.I)
SKILLS = {
    'Python': r'\bpython\b', 'SQL': r'\bsql\b', 'PyTorch': r'\bpytorch\b',
    'scikit-learn': r'\bscikit[- ]learn\b', 'LangGraph': r'\blanggraph\b',
    'RAG': r'\brag\b|retrieval.augmented', 'LLM': r'\bllms?\b|large language model',
    'AI Agents': r'agentic|\b(?:ai|llm) agents?\b',
    'Vector DB': r'vector (?:db|database|store)|\b(?:pinecone|qdrant|weaviate|faiss)\b',
    'FastAPI': r'\bfastapi\b', 'Docker': r'\bdocker\b',
    'AWS': r'\baws\b|amazon web services', 'Azure': r'\bazure\b',
    'MLflow': r'\bmlflow\b', 'n8n': r'\bn8n\b',
    'Forecasting': r'forecasting|time.series|zeitreihen',
    'Knowledge graphs': r'knowledge graph|wissensgraph',
}


def request(url, headers=None, timeout=TIMEOUT):
    h = {'User-Agent': AGENT, 'Accept': 'application/json'}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read(2_500_001)
        if len(data) > 2_500_000 and 'api.github.com' not in url:
            raise ValueError('Web page exceeds verification limit')
        return data, response.geturl()


def source():
    token = os.getenv('GITHUB_SOURCE_TOKEN', '').strip()
    h = {'Authorization': 'Bearer ' + token} if token else {}
    raw, _ = request(f'https://api.github.com/repos/{REPO}/contents/{DATA_PATH}', h)
    info = json.loads(raw)
    if not info.get('sha'):
        raise ValueError('GitHub did not return the jobs.json blob SHA')
    return info['sha'], info.get('download_url') or f'https://raw.githubusercontent.com/{REPO}/main/{DATA_PATH}'


def field(job, *names):
    for name in names:
        value = job.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for k in ('name', 'title', 'value', 'city', 'url'):
                if isinstance(value.get(k), str) and value[k].strip():
                    return value[k].strip()
        if isinstance(value, list):
            result = [v for v in value if isinstance(v, str)]
            if result:
                return ', '.join(result)
    return ''


def jobs_in(data):
    out = []
    def walk(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, list):
            for v in node:
                walk(v, depth+1)
        elif isinstance(node, dict):
            if set(node) & {'title', 'job_title', 'position', 'jobTitle'} and \
               set(node) & {'url', 'link', 'apply_url', 'job_url', 'application_url', 'applyUrl'}:
                out.append(node)
            else:
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        walk(v, depth+1)
    walk(data)
    return out


def normalize(job):
    return {
        'title': field(job, 'title', 'job_title', 'position', 'jobTitle'),
        'url': field(job, 'url', 'apply_url', 'application_url', 'job_url', 'link', 'applyUrl'),
        'company': field(job, 'company', 'company_name', 'employer', 'organization') or 'Unknown company',
        'location': field(job, 'location', 'city', 'job_location', 'jobLocation', 'locations'),
        'description': field(job, 'description', 'job_description', 'summary', 'snippet', 'requirements', 'tags'),
    }


def job_id(job):
    parsed = urllib.parse.urlsplit(job['url'])
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
         if not k.lower().startswith('utm_') and k.lower() not in ('ref', 'source', 'trk', 'trackingid')]
    clean = urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                                    parsed.path.rstrip('/'), urllib.parse.urlencode(q), ''))
    key = clean or '|'.join((job['company'], job['title'], job['location']))
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]


def load_dataset(raw):
    jobs = jobs_in(json.loads(raw))
    if not jobs:
        raise ValueError('No recognizable jobs in source JSON; state not advanced')
    return {job_id(j): j for j in (normalize(x) for x in jobs)}


def title_text(title):
    return re.sub(r'[-_/|]+', ' ', title).strip()


def role_decision(job):
    title = title_text(job['title'])
    description = job['description']
    if STUDENT.search(title):
        return 'skip', 'student/internship (not target)'
    if SENIOR.search(title):
        return 'skip', 'senior/lead-only title'
    if PRIMARY.search(title):
        return 'candidate', 'AI/ML primary title'
    if SECONDARY.search(title) and TECH.search(description):
        return 'candidate', 'AI-heavy related role (description evidence)'
    # When the dataset omits description, do not silently throw away promising
    # knowledge-graph, software-with-AI, or forward deployed engineer titles.
    if SECONDARY.search(title) and TECH.search(title):
        return 'review', 'related role with AI evidence in title; description needed'
    if re.search(r'\b(?:ai|ki)\b', title, re.I):
        return 'review', 'AI title needs human relevance check'
    return 'skip', 'not a matching AI/ML role'


def location_decision(job):
    loc, description = job['location'], job['description']
    if BAD_CONTRACT.search(job['title']):
        return 'skip', 'freelance/contractor title'
    if DE_LOC.search(loc):
        return 'candidate', 'German job location stated; contract/Blue Card not proven'
    if DE_CONTRACT.search(description):
        return 'candidate', 'German employment wording in description; verify details'
    if OTHER_ONLY.search(loc):
        return 'review', 'foreign location; German employment contract not evidenced'
    if not loc:
        return 'review', 'no location in dataset; verify Germany eligibility'
    if re.search(r'\b(?:remote|europe|eu|worldwide|anywhere)\b', loc, re.I):
        return 'review', 'remote location; Germany employment permission unverified'
    return 'review', 'unknown city/country; do not exclude German city automatically'


def domain_match(host, domains):
    return any(host == d or host.endswith('.' + d) for d in domains)


def link_decision(url):
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or '').lower()
        if p.scheme != 'https' or not host or p.username or p.password:
            return 'review', 'invalid/incomplete link'
        if domain_match(host, BOARD_DOMAINS):
            return 'review', 'job-board link; official employer URL required'
        if domain_match(host, ATS_DOMAINS):
            return 'candidate', 'recognized ATS domain'
        return 'review', 'employer custom domain; official ownership not validated'
    except ValueError:
        return 'review', 'malformed link'


def verify_official_open(job):
    """Limited heuristic only. Never makes claims about German immigration approval."""
    kind, reason = link_decision(job['url'])
    if kind != 'candidate':
        return False, reason
    try:
        raw, redirected = request(job['url'], {'Accept': 'text/html'}, timeout=20)
        final_kind, _ = link_decision(redirected)
        if final_kind != 'candidate':
            return False, 'redirected outside recognized ATS'
        page = html.unescape(raw.decode('utf-8', 'ignore'))
        visible = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', page))
        if CLOSED_MARKERS.search(visible):
            return False, 'page says vacancy is closed'
        if not APPLY_MARKERS.search(visible):
            return False, 'no application marker on HTML (could be JS rendered)'
        # Avoid a generic careers homepage: try to locate a meaningful title phrase.
        title_words = [w for w in re.findall(r'[a-zA-ZäöüÄÖÜ]{3,}', job['title'].lower())
                       if w not in {'mwd', 'all', 'gender', 'genders', 'junior', 'full', 'time'}]
        matched = sum(1 for w in set(title_words) if w in visible.lower())
        if not title_words or matched < min(2, len(set(title_words))):
            return False, 'specific vacancy title not confirmed on page'
        return True, 'direct ATS page has matching title and apply action; immigration unverified'
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return False, f'ATS page not verifiable: {type(exc).__name__}'


def telegram(message):
    """Deliver one Telegram message, respecting 429 Retry-After when possible."""
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        raise RuntimeError('Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID')
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=urllib.parse.urlencode({'chat_id': chat, 'text': message[:3900],
                                     'disable_web_page_preview': 'true'}).encode(), method='POST')
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read())
                if not result.get('ok'):
                    raise RuntimeError('Telegram API did not accept message')
                return
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 5:
                try:
                    payload = json.loads(error.read())
                    retry_after = int(payload.get('parameters', {}).get('retry_after', 3))
                except (ValueError, TypeError, json.JSONDecodeError):
                    retry_after = 3
                time.sleep(min(max(retry_after, 1), 120))
                continue
            if 500 <= error.code < 600 and attempt < 5:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise
        except (urllib.error.URLError, RuntimeError):
            if attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 30))



def google_careers_url(job):
    """Search URL only; never claim an employer vacancy was verified."""
    query = ' '.join((job['company'], job['title'], 'careers jobs Germany'))
    return 'https://www.google.com/search?' + urllib.parse.urlencode({'q': query})


def review_message(job):
    """Single candidate for compact Telegram manual-review summary."""
    location = job['location'] or 'Not specified'
    return (f"🏢 {job['company']}\n💼 {job['title']}\n"
            f"📍 {location}\n🔎 Google careers: {google_careers_url(job)}\n")


def send_review_summary(jobs, on_sent=None):
    """Send ALL unverified relevant jobs in Telegram-sized batches.

    on_sent checkpoints IDs after each successful batch, so a later failure
    can resume without re-sending already delivered batches.
    """
    header = ('🔎 AI/ML JOBS FOR MANUAL REVIEW (NOT VERIFIED)\n'
              'Google links are searches, NOT confirmed vacancies. Check employer careers page '
              'and German employment contract/Blue Card requirements.\n\n')
    message, batch, sent = header, [], []

    def flush():
        nonlocal message, batch
        if not batch:
            return
        telegram(message)
        ids = [job['id'] for job in batch]
        sent.extend(ids)
        if on_sent:
            on_sent(ids)
        time.sleep(1)
        message, batch = header, []

    for job in jobs:
        part = review_message(job) + '\n'
        if len(message) + len(part) > 3800:
            flush()
        # Extremely long individual entries are shortened, without altering the URL.
        if len(header) + len(part) > 3800:
            part = (f"🏢 {job['company'][:120]}\n💼 {job['title'][:170]}\n"
                    f"🔎 Google careers: {google_careers_url(job)}\n\n")
        if len(header) + len(part) > 3800:
            raise ValueError('Unexpected oversized manual-review job entry')
        message += part
        batch.append(job)
    flush()
    return sent


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def run():
    sha, download_url = source()
    old = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    replay_key = f'{RULES_VERSION}:{REPLAY_PREVIOUS_COMMIT}' if REPLAY_PREVIOUS_COMMIT else ''
    replay_due = bool(replay_key and replay_key not in old.get('completed_replays', []))
    if old.get('sha') == sha and not replay_due:
        print(f'No new data/jobs.json blob revision; SHA {sha}; alerts suppressed')
        return

    # The dataset may be tens of MB. Unlike verify_official_open(), do not cap it at 2.5 MB.
    req = urllib.request.Request(download_url, headers={'User-Agent': AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=110) as r:
        current = load_dataset(r.read())
    now = datetime.now(timezone.utc).isoformat()
    if not old:
        atomic_json(STATE, {'sha': sha, 'seen_ids': sorted(current),
                            'all_seen_ids': sorted(current), 'alerted_ids': [],
                            'completed_replays': [], 'review_notified_ids': [], 'checked_at': now})
        print(f'Baseline initialized: {len(current)}; no job alerts on baseline')
        return

    history = set(old.get('all_seen_ids', old.get('seen_ids', [])))
    alerted = set(old.get('alerted_ids', []))
    review_notified = set(old.get('review_notified_ids', []))
    review = {key: value for key, value in current.items() if key not in history}
    discovered_count = len(review)
    if replay_due:
        prior_url = f'https://raw.githubusercontent.com/{REPO}/{REPLAY_PREVIOUS_COMMIT}/{DATA_PATH}'
        req = urllib.request.Request(prior_url, headers={'User-Agent': AGENT})
        with urllib.request.urlopen(req, timeout=110) as r:
            previous = load_dataset(r.read())
        replay_additions = {key: job for key, job in current.items() if key not in previous}
        review.update({key: job for key, job in replay_additions.items() if key not in alerted})
        print(f'Replay previous commit: {REPLAY_PREVIOUS_COMMIT}; currently present additions: {len(replay_additions)}')

    reasons = Counter()
    review_queue = []
    alerts = []
    for key, job in review.items():
        if key in alerted:
            continue
        role, reason = role_decision(job)
        if role == 'skip':
            reasons[reason] += 1
            continue
        location, loc_reason = location_decision(job)
        link, link_reason = link_decision(job['url'])
        status = 'review'
        detail = [reason, loc_reason, link_reason]
        if location == 'candidate' and link == 'candidate':
            yes, verify_reason = verify_official_open(job)
            detail.append(verify_reason)
            if yes and role == 'candidate':
                status = 'prechecked'
        if location == 'skip':
            reasons[loc_reason] += 1
            continue
        skills = [name for name, pattern in SKILLS.items()
                  if re.search(pattern, job['description'] + ' ' + job['title'], re.I)]
        entry = {'id': key, **job, 'status': status, 'reasons': detail,
                 'skills_in_dataset': skills,
                 'blue_card': 'UNVERIFIED: German contract, salary and residence rules require confirmation',
                 'first_observed_in_dataset': True,
                 'newly_posted_on_market': 'UNKNOWN'}
        if status == 'prechecked':
            alerts.append(entry)
        else:
            review_queue.append(entry)
            reasons[detail[-1]] += 1
    # Job-board and unknown location candidates are kept for manual official-link search.
    # Do not automatically send those links as though they are verified vacancies.
    def priority(job):
        title = title_text(job['title'])
        return (3 if JUNIOR.search(title) else 0) + (4 if PRIMARY.search(title) else 1) + min(4, len(job['skills_in_dataset']))
    alerts.sort(key=priority, reverse=True)
    review_queue.sort(key=priority, reverse=True)
    # Output review queue even when no Telegram alerts were sent. Workflow must commit
    # this file as well if users want the queue browsable between runs.
    atomic_json(REVIEW_FILE, {'sha': sha, 'rules_version': RULES_VERSION,
                              'generated_at': now, 'jobs': review_queue})
    print(f'Blob SHA: {sha}; dataset: {len(current)}; new: {discovered_count}; '
          f'reviewed: {len(review)}; ATS-prechecked: {len(alerts)}; '
          f'manual review: {len(review_queue)}')
    print('Review/skip counts:', dict(reasons))
    for job in review_queue[:25]:
        print('REVIEW:', job['title'][:90], '|', job['company'][:45], '|',
              job['url'], '|', '; '.join(job['reasons']))
    # Google-search fallback for promising jobs without a verified official URL.
    # Send only once per job ID, not every run; never present as verified.
    review_to_notify = [job for job in review_queue
                        if job['id'] not in review_notified and job['id'] not in alerted
                        and not job['reasons'][0].startswith('AI title needs human relevance check')
                        and 'foreign location; German employment contract not evidenced' not in job['reasons']]
    print(f'Google-search manual review candidates to notify: {len(review_to_notify)}; no cap')
    # Only direct ATS vacancies with title + apply marker; *not* Blue Card verified.
    def checkpoint():
        # Persist notification IDs without advancing SHA/replay status until success.
        pending = dict(old)
        pending['alerted_ids'] = sorted(alerted)
        pending['review_notified_ids'] = sorted(review_notified)
        atomic_json(STATE, pending)

    for job in alerts:
        telegram(f"🇩🇪 AI/ML job: ATS page prechecked\n"
                 f"🏢 {job['company']}\n💼 {job['title']}\n"
                 f"📍 {job['location'] or 'Not specified'}\n"
                 f"🔧 Skills seen: {', '.join(job['skills_in_dataset']) or 'No description provided'}\n"
                 f"🔗 Official ATS: {job['url']}\n"
                 f"⚠️ Newly observed on GitHub, NOT proven newly posted. "
                 f"EU Blue Card eligibility and German contract NOT confirmed.")
        alerted.add(job['id'])
        checkpoint()
        time.sleep(1)
    def checkpoint_review(ids):
        review_notified.update(ids)
        checkpoint()
    send_review_summary(review_to_notify, on_sent=checkpoint_review)
    completed = list(old.get('completed_replays', []))
    if replay_due:
        completed.append(replay_key)
    atomic_json(STATE, {'sha': sha, 'seen_ids': sorted(current),
                        'all_seen_ids': sorted(history | set(current)),
                        'alerted_ids': sorted(alerted),
                        'review_notified_ids': sorted(review_notified),
                        'completed_replays': completed,
                        'checked_at': now})


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        print(f'ERROR: {type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
