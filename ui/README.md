# Review app

Reviews a day of the FDE activity log item by item, and answers the flow's review pause (finalize, or requests
for the agent). Deployed on its own, on Heroku; the AMP flow (`flows/daily_log`) does not depend on
it and is not changed by it. The two share only MongoDB documents, described in `server/review_api/contract.py`.

| Path | What it holds |
|---|---|
| `web/` | The frontend: React + Vite + TypeScript, in the CrewAI design system (`web/src/styles/tokens.css`) |
| `server/` | The thin API (FastAPI): direct edits, AMP's Human in the Loop webhook, answering the pause, GitHub sign-in, serving `web/dist` |
| `Dockerfile`, `heroku.yml` | The Heroku image: builds `web/`, installs only `server/`'s dependencies |

## Configuration

Everything the app reads is in `ui/.env.example`, with where each value comes from. Copy it to `ui/.env` for local
runs; on Heroku the same variables are the app's config vars (all but `NGROK_DOMAIN`).

For the daily log the app needs no AMP URL or token: AMP sends every review pause to the app's webhook,
signed with `HITL_WEBHOOK_SECRET`, and the app answers on the callback URL that comes with it. (The account report
and project builder flows are optional AMP calls: `AMP_REPORT_*` starts reports, `AMP_BUILDER_*` sends build
messages as chat turns; without them, both run locally.) AMP, in turn, needs
the app's URL twice: the webhook (Deployment > Settings > Human in the Loop > Webhooks, `https://<app>/api/hitl/webhook`)
and `REVIEW_UI_URL` in the flow's env vars (the review message links to the day in the app).

## Local, behind ngrok

With `ui/.env` filled in, from the repo root:

```bash
bin/start          # builds web/ when it changed, serves it with the API, tunnels https://$NGROK_DOMAIN
bin/start --dev    # Vite dev server with hot reload instead of a build
```

## Heroku

The app is `fde-activity-log` (https://fde-activity-log-d6a311d9e845.herokuapp.com). Only this folder is deployed:
Heroku builds the Docker image of `heroku.yml`/`Dockerfile`, not the flow package at the repo root.

### Once

```bash
heroku stack:set container -a fde-activity-log      # build from heroku.yml (a buildpack stack builds the wrong thing)
heroku git:remote -a fde-activity-log               # adds the `heroku` git remote
# config vars: the names in ui/.env.example, except NGROK_DOMAIN; GITHUB_CLIENT_ID/SECRET come from the GitHub
# OAuth app whose callback is https://<heroku host>/auth/callback (not the ngrok one)
heroku config:set -a fde-activity-log NAME=value …
heroku ps:scale web=1 -a fde-activity-log           # only if `heroku ps` shows no web dyno after the first deploy
```

Atlas must accept Heroku's connections (Network Access), since Heroku has no fixed IPs.

### Updating

Commit, then from the repo root:

```bash
bin/deploy --heroku              # checks the committed ui/ builds, pushes only ui/, waits until the app answers
bin/deploy --heroku --no-check   # skip the local Docker build
bin/deploy --heroku --dry-run    # show what would be pushed
bin/deploy                       # the review app and the AMP flow together (see the main README)
```

It refuses to run with uncommitted changes in `ui/` (Heroku only gets committed files, and anything `.gitignore`
excludes is missing there even if it is on your disk). If Heroku rejects the push as non-fast-forward (its history
differs, for example after amending a deployed commit), run it again with `--force`, which only replaces
what is on Heroku.

By hand, the push is `git push heroku "$(git subtree split -q --prefix ui):refs/heads/main"` (in zsh keep it
exactly like this, or `:r` is read as a modifier).

Config changes need no push: `heroku config:set -a fde-activity-log NAME=value` restarts the app with the new value.
To go back to the previous version: `heroku releases -a fde-activity-log`, then `heroku rollback -a fde-activity-log`.
