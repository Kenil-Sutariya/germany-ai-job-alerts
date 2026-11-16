#!/usr/bin/env python3
"""Germany AI job monitor v3.1. Stdlib only. Conservative alerts, transparent review logs."""
import hashlib
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

REPO = os.getenv("SOURCE_REPO", "ashishtiwari03/work-in-germany")
DATA_PATH = "data/jobs.json"
STATE = Path("state/seen.json")
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "12"))
# One-time replay of additions between these two commits; disable after successful run.
# Only jobs present in the current dataset are considered.
REPLAY_PREVIOUS_COMMIT = os.getenv("REPLAY_PREVIOUS_COMMIT", "1c8a3633f12dab43329be3adadeae08560ebd930").strip()
AGENT = "GermanyAIJobAlerts/3.1"

BOARD_HOSTS = ("linkedin.com", "indeed.com", "stepstone.de", "glassdoor.com",
               "jobrapido.com", "adzuna.com", "jooble.org", "monster.com",
               "xing.com", "reddit.com", "github.com")
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com",
             "myworkdayjobs.com", "workdayjobs.com", "jobs.personio.de",
             "jobs.personio.com", "recruitee.com", "teamtailor.com",
             "bamboohr.com", "join.com")
ROLE = re.compile(
    r"\b(?:ai|ml|ki)\s+(?:developer|engineer|specialist|consultant|solutions?\s+engineer|"
    r"application\s+engineer|automation\s+(?:engineer|specialist))\b|"
    r"\b(?:machine learning|maschinelles lernen|applied ai|generative ai|genai|"
    r"llm|large language model|data scientist|data science|mlops|agentic ai|"
    r"nlp engineer|computer vision|deep learning|ki entwickler|ki ingenieur|"
    r"künstliche intelligenz|ai acoustic engineer)\b", re.I)
SECONDARY = re.compile(r"\b(?:data engineer|dateningenieur|research engineer|"
                       r"research scientist|automation engineer|software engineer|"
                       r"r\s*&\s*d engineer|research and development engineer)\b", re.I)
ML = re.compile(r"\b(?:machine learning|maschinelles lernen|ml|llm|rag|pytorch|"
                r"tensorflow|langgraph|model training|ai agents|generative ai|"
                r"artificial intelligence|künstliche intelligenz|neural network|"
                r"deep learning|predictive modeling|computer vision)\b", re.I)
SENIOR = re.compile(r"\b(?:senior|sr|staff|principal|lead|head of|director|"
                    r"vp|chief)\b", re.I)
ENTRY = re.compile(r"\b(?:junior|graduate|entry level|associate|trainee|"
                   r"berufseinsteiger)\b", re.I)
SKILLS = {
    "Python": r"\bpython\b", "SQL": r"\bsql\b", "PyTorch": r"\bpytorch\b",
    "scikit-learn": r"\bscikit[- ]learn\b", "LangGraph": r"\blanggraph\b",
    "RAG": r"\brag\b|retrieval.augmented", "LLM": r"\bllms?\b|large language model",
    "AI agents": r"\b(?:ai|llm) agents?\b|agentic",
    "Vector DB": r"vector (?:database|db|store)|\b(?:pinecone|weaviate|milvus|qdrant|faiss)\b",
    "FastAPI": r"\bfastapi\b", "Docker": r"\bdocker\b",
    "AWS": r"\baws\b|amazon web services", "Azure": r"\bazure\b",
    "MLflow": r"\bmlflow\b", "n8n": r"\bn8n\b",
    "Time series": r"time.series|forecasting|zeitreihen",
}
GERMANY = re.compile(r"\b(?:germany|deutschland|deutsch|bundesweit|"
                     r"berlin|hamburg|münchen|munich|frankfurt|stuttgart|"
                     r"köln|cologne|düsseldorf|dortmund|leipzig|dresden|"
                     r"hannover|bremen|bonn|nürnberg|nuremberg|"
                     r"karlsruhe|mannheim|heidelberg|wiesbaden|mainz|"
                     r"augsburg|freiburg|essen|bochum|aachen|kiel|"
                     r"potsdam|göttingen|regensburg|ulm|braunschweig)\b", re.I)
FOREIGN = re.compile(r"\b(?:switzerland|schweiz|austria|österreich|"
                     r"united kingdom|united states|usa|india|poland|"
                     r"netherlands|france|spain|italy)\b", re.I)
GERMAN_CONTRACT = re.compile(
    r"\b(?:german employment contract|german labor contract|german labour contract|"
    r"german payroll|german entity|employment in germany|"
    r"deutscher arbeitsvertrag|anstellung in deutschland)\b", re.I)
CLOSED = re.compile(r"no longer accepting applications|job (?:is )?(?:closed|expired)|"
                    r"position (?:has been )?filled|stelle (?:ist )?besetzt", re.I)
APPLY = re.compile(r"\b(?:apply now|apply for this job|submit application|"
                   r"apply for this position|jetzt bewerben|bewerben)\b", re.I)


def fetch(url, headers=None, timeout=45):
    h = {"User-Agent": AGENT, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl()


def source():
    token = os.getenv("GITHUB_SOURCE_TOKEN", "").strip()
    headers = {"Authorization": "Bearer " + token} if token else {}
    raw, _ = fetch(f"https://api.github.com/repos/{REPO}/contents/{DATA_PATH}", headers)
    info = json.loads(raw)
    if not info.get("sha"):
        raise ValueError("Source SHA missing")
    return info["sha"], info.get("download_url") or f"https://raw.githubusercontent.com/{REPO}/main/{DATA_PATH}"


def pick(job, *keys):
    for k in keys:
        v = job.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for sub in ("name", "title", "value", "url", "city"):
                if isinstance(v.get(sub), str) and v[sub].strip():
                    return v[sub].strip()
        if isinstance(v, list):
            vals = [x for x in v if isinstance(x, str)]
            if vals:
                return ", ".join(vals)
    return ""


def jobs_in(data):
    found = []
    def walk(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, list):
            for x in node:
                walk(x, depth + 1)
        elif isinstance(node, dict):
            if set(node) & {"title", "job_title", "position", "jobTitle"} and \
               set(node) & {"url", "link", "apply_url", "job_url", "application_url", "applyUrl"}:
                found.append(node)
            else:
                for x in node.values():
                    if isinstance(x, (dict, list)):
                        walk(x, depth + 1)
    walk(data)
    return found


def normalize(j):
    return {
        "title": pick(j, "title", "job_title", "position", "jobTitle"),
        "url": pick(j, "url", "apply_url", "application_url", "job_url", "link", "applyUrl"),
        "company": pick(j, "company", "company_name", "employer", "organization") or "Unknown company",
        "location": pick(j, "location", "city", "job_location", "jobLocation", "locations"),
        "description": pick(j, "description", "job_description", "summary", "snippet", "requirements", "tags"),
    }


def identity(j):
    p = urllib.parse.urlsplit(j["url"])
    qs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
          if not k.lower().startswith("utm_") and k.lower() not in ("ref", "source", "trk")]
    url = urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(),
                                  p.path.rstrip("/"), urllib.parse.urlencode(qs), ""))
    key = url or "|".join((j["company"], j["title"], j["location"]))
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def load_jobs(raw):
    data = json.loads(raw)
    rows = jobs_in(data)
    if not rows:
        raise ValueError("No jobs recognized; state not updated")
    return {identity(n): n for n in map(normalize, rows)}


def role_check(j):
    title = re.sub(r"[-_/]+", " ", j["title"]).strip()
    description = j["description"]
    if SENIOR.search(title):
        return False, "Senior/lead-only title"
    if ROLE.search(title):
        return True, "Primary AI/ML role"
    if SECONDARY.search(title) and ML.search(description):
        return True, "AI-heavy secondary role"
    return False, "Role not clearly AI/ML"


def location_check(j):
    loc = j["location"]
    desc = j["description"]
    if GERMANY.search(loc):
        return True, "Germany location listed; German contract/Blue Card not verified"
    if FOREIGN.search(loc) and GERMAN_CONTRACT.search(desc):
        return True, "Foreign employer with German employment wording; verify contract"
    if re.search(r"\bremote\b", loc, re.I) and GERMAN_CONTRACT.search(desc):
        return True, "Remote with German employment wording; verify contract"
    return False, "German employment location/contract not established"


def url_check(url):
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").lower()
        if p.scheme != "https" or not host or p.username or p.password:
            return False, "Invalid URL"
        if any(host == b or host.endswith("." + b) for b in BOARD_HOSTS):
            return False, "Third-party job board"
        if not any(host == a or host.endswith("." + a) for a in ATS_HOSTS):
            return False, "Unrecognized employer/ATS domain; manual review"
        return True, "Recognized ATS domain"
    except ValueError:
        return False, "Malformed URL"


def ats_check(url):
    ok, why = url_check(url)
    if not ok:
        return False, why
    try:
        raw, final_url = fetch(url, {"Accept": "text/html"}, timeout=18)
        if len(raw) > 2_000_000:
            return False, "ATS page too large for verification"
        html = raw.decode("utf-8", "ignore")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        if CLOSED.search(text):
            return False, "ATS indicates closed role"
        if not APPLY.search(text):
            return False, "Application action not found (may be JavaScript)"
        # A generic ATS landing page can contain 'apply' but not this vacancy.
        return True, "ATS page reachable and application action found; employer/Blue Card confirmation pending"
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return False, f"ATS request failed: {type(e).__name__}"


def send(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise RuntimeError("Missing Telegram secrets")
    body = urllib.parse.urlencode({
        "chat_id": chat, "text": message[:3900], "disable_web_page_preview": "true"
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                if not json.loads(resp.read()).get("ok"):
                    raise RuntimeError("Telegram rejected message")
            return
        except (urllib.error.URLError, RuntimeError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(STATE)


def main():
    sha, url = source()
    old = json.loads(STATE.read_text()) if STATE.exists() else {}
    replay_key = f"replay:{REPLAY_PREVIOUS_COMMIT}" if REPLAY_PREVIOUS_COMMIT else ""
    replay_due = bool(replay_key and replay_key not in old.get("completed_replays", []))
    if old.get("sha") == sha and not replay_due:
        print("No new jobs.json revision; no alert")
        return

    raw, _ = fetch(url, timeout=100)
    current = load_jobs(raw)
    if not old:
        save({"sha": sha, "seen_ids": sorted(current), "all_seen_ids": sorted(current),
              "alerted_ids": [], "completed_replays": [],
              "checked_at": datetime.now(timezone.utc).isoformat()})
        print(f"Baseline initialized: {len(current)} jobs; no alert")
        return

    history = set(old.get("all_seen_ids", old.get("seen_ids", [])))
    alerted = set(old.get("alerted_ids", []))
    newly_seen = {k: j for k, j in current.items() if k not in history}
    review = dict(newly_seen)
    if replay_due:
        previous_url = f"https://raw.githubusercontent.com/{REPO}/{REPLAY_PREVIOUS_COMMIT}/{DATA_PATH}"
        prev_raw, _ = fetch(previous_url, timeout=100)
        previous = load_jobs(prev_raw)
        replay_additions = {k: j for k, j in current.items() if k not in previous}
        review.update({k: j for k, j in replay_additions.items() if k not in alerted})
        print(f"Replay prior commit: {REPLAY_PREVIOUS_COMMIT}; "
              f"replay additions currently present: {len(replay_additions)}")
    print(f"Current: {len(current)}; newly discovered: {len(newly_seen)}; "
          f"reviewing: {len(review)}; historic IDs: {len(history)}")

    reasons = Counter()
    candidates = []
    for key, j in review.items():
        eligible, reason = role_check(j)
        if not eligible:
            reasons[reason] += 1
            print(f"FILTERED [{reason}]: {j['title'][:100]} | {j['company'][:60]}")
            continue
        loc_ok, loc_reason = location_check(j)
        if not loc_ok:
            reasons[loc_reason] += 1
            print(f"REVIEW [{loc_reason}]: {j['title'][:100]} | {j['company'][:60]} | {j['url']}")
            continue
        verified, verification = ats_check(j["url"])
        if not verified:
            reasons[verification] += 1
            print(f"REVIEW [{verification}]: {j['title'][:100]} | {j['company'][:60]} | {j['url']}")
            continue
        if key in alerted:
            continue
        text = j["title"] + " " + j["description"]
        matched = [s for s, pattern in SKILLS.items() if re.search(pattern, text, re.I)]
        score = (5 if ROLE.search(re.sub(r"[-_/]+", " ", j["title"])) else 2) + \
                (3 if ENTRY.search(j["title"]) else 0) + min(5, len(matched))
        candidates.append((score, key, j, matched, loc_reason))
    candidates.sort(reverse=True, key=lambda row: row[0])
    print(f"Filtered/review counts: {dict(reasons)}")
    print(f"Blob SHA: {sha}; dataset: {len(current)}; new: {len(newly_seen)}; "
          f"ATS prechecked: {len(candidates)}")

    # No automated assertion that Blue Card is guaranteed. Explicitly label gaps.
    for score, key, j, matched, loc_reason in candidates[:MAX_ALERTS]:
        send(f"🇩🇪 AI/ML job, newly observed in GitHub dataset\n"
             f"🏢 {j['company']}\n💼 {j['title']}\n📍 {j['location'] or 'Not specified'}\n"
             f"🔧 CV skills mentioned: {', '.join(matched) or 'Not stated in dataset'}\n"
             f"🔗 ATS link: {j['url']}\n"
             f"⚠️ ATS page prechecked, not proof of a newly posted vacancy. "
             f"German employment contract, salary and EU Blue Card eligibility NOT confirmed.")
        alerted.add(key)
    completed = list(old.get("completed_replays", []))
    if replay_due:
        completed.append(replay_key)
    save({"sha": sha, "seen_ids": sorted(current),
          "all_seen_ids": sorted(history | set(current)),
          "alerted_ids": sorted(alerted),
          "completed_replays": completed,
          "checked_at": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
