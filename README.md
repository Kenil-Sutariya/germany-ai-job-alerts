# Germany AI Job Alerts (Telegram)

Independent GitHub Actions monitor of `ashishtiwari03/work-in-germany/data/jobs.json`.

## Setup

1. Create a **private** GitHub repository with README on the `main` branch (suggested name `germany-ai-job-alerts`).
2. Upload `monitor.py`, `.github/workflows/job-alerts.yml`, `.gitignore`, `tests/test_monitor.py`, and README. **Preserve the `.github/workflows` folder path.**
3. In Telegram, message @BotFather, run `/newbot`, and copy the bot token. Do not share the token.
4. Open your new bot's chat and press **Start**, then send any message such as `hello`.
5. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in your private browser. Find the numeric `message.chat.id`. Do not publish the token or full API URL.
6. GitHub repository **Settings > Secrets and variables > Actions > New repository secret**. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
7. Repository **Settings > Actions > General > Workflow permissions**: choose **Read and write permissions** (needed to save state). Save.
8. Repository **Actions > Germany AI Jobs -> Telegram > Run workflow**. The first run creates a baseline and sends one setup message only. Future source revisions report newly observed CV-title matches.
9. Check Actions logs if errors appear. To run tests locally: `python -m unittest discover -s tests -v`.

## Schedule / costs

Runs at UTC minutes 02, 07, 12, 17, 22, 27, 32, 37, 42, 47, 52 and 57. Scheduling is best-effort and might be delayed or dropped. To reduce GitHub Actions usage, change the cron to `2/15 * * * *` (every 15 min). Runs on GitHub-hosted Actions consume minutes on private repositories, even when no jobs were found. Use GitHub billing/usage controls to avoid unexpected charges.

## What this actually verifies

- Checks GitHub file **blob SHA**; downloads JSON only if changed.
- On first run, saves current job IDs without calling all 50k+ listings new.
- Later compares IDs and filters titles by AI/ML terms and obvious seniority exclusions.
- Rejects known third-party job boards; **does not prove that an application is currently open**. The message explicitly asks you to verify each employer's page and language requirements.
- Keeps state in `state/seen.json` committed by workflow. Do not delete it unless you intend to rebuild the baseline.
- Records first-seen source entries, not actual job-market posting dates.
- Contains no automatic CV parsing, LLM ranking, browser verification, or visa eligibility checks.

## Important limitations

Repository JSON schema could change. Monitor accepts common list and grouped JSON formats but intentionally fails without overwriting state if parsing fails. Titles may be incomplete or mislabeled. This version cannot guarantee perfect CV matching or 1-minute Telegram delivery. State writing may fail under branch protection rules or GitHub Actions policies. **Never commit Telegram bot tokens to repository.**
