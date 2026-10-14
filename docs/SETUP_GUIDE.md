# TenantWise: Build Guide From Empty Folder to Live Product

This guide walks through every step in order, with the reason behind each decision. Follow it top to bottom. Commands keep the flags and package names their tools require; everything else is plain English.

Estimated time is 7 to 10 days part time. Estimated running cost is under about $10 a month, enforced by hard caps.

## 0. Accounts and tools

Create these accounts (all have free tiers): GitHub, Neon (neon.tech), the Anthropic Console (console.anthropic.com), Google Cloud (with billing enabled; the free tier still needs a card), Vercel, and PyPI.

Install these tools: Python 3.12, Node 22, git, the Google Cloud CLI (`gcloud`), and `uv` (a fast Python package manager, used for the MCP server). Docker is optional; Cloud Build builds the image for you.

Why these: each one is free at portfolio scale, widely used in industry, and easy to explain in an interview.

## 1. Create the repository

1. On GitHub, create a new public repository named `tenantwise` with no template.
2. Clone it and copy the project files in:

```bash
git clone https://github.com/Jeffano/tenantwise.git
cd tenantwise
# unzip the project files here so backend/, frontend/, mcp_server/, docs/ and .github/ sit at the top level
git add .
git commit -m "Initial TenantWise scaffold"
git push origin main
```

Why public: GitHub Actions minutes are free for public repositories, and recruiters can read the code and the CI history.

Why a monorepo: the API, frontend, MCP server and evals change together. One repository means one pull request can change a prompt and prove it with an eval in the same CI run.

## 2. Python environment and unit tests

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q
```

All tests should pass. They cover PII redaction, citation validation, prompt tag injection, routing rules and the statute parser. None of them need a database or an API key.

Why split requirements: `requirements.txt` is what ships in the container. `requirements-dev.txt` adds parsing and test tools that production never needs, keeping the image smaller and its attack surface lower.

## 3. Database on Neon

1. In Neon, create a project named `tenantwise`. Pick the AWS US East (Ohio) region.
2. Open Connection Details, enable "Pooled connection", and copy the connection string. It ends with `?sslmode=require`.
3. Create your local env file:

```bash
cp .env.example .env
# edit .env and paste DATABASE_URL, then set IP_HASH_SALT to a long random string:
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

You do not need to run the schema by hand. The ingestion script applies `sql/schema.sql` every time, and the schema is written to be safe to run repeatedly.

Why Neon: free tier, supports pgvector, and suspends compute when idle so you pay nothing between visits.

Why Ohio: it is close to Google's us-central1 region, where the API runs. Database round trips happen several times per request, so keeping them short matters more than anything else for latency.

Why the pooled endpoint: Cloud Run can start several instances, and each opens connections. The pooler keeps Neon's connection count low. The code disables prepared statements (`prepare_threshold=None` in `app/db.py`) because the pooler runs in transaction mode, which does not support them.

## 4. Anthropic API key and spend limit

1. In the Anthropic Console, create an API key and paste it into `.env` as `ANTHROPIC_API_KEY`.
2. Under Limits, set a monthly spend limit of $10.
3. Check the current model names and prices on Anthropic's models and pricing pages. If they differ from the defaults in `app/config.py`, override them in `.env` (for example `SMALL_MODEL=...`). Update the price fields too, since they drive the cost numbers.

Why a console limit: it is the outer safety layer. Even if every other control failed, spending cannot exceed it.

## 5. Ingest the corpus

First do a dry run. It fetches and parses everything and prints samples, but writes nothing:

```bash
python -m ingestion.ingest --dry-run
```

The first run downloads the embedding model (about 130 MB) into `./.models`. Check the output for each source:

1. The Residential Tenancies Act should produce well over 200 sections, with references such as `RTA s. 116` and heading paths such as `Part VII Rules Relating To Rent > Notice of rent increase required`.
2. If a statute shows the "very few sections parsed" warning, the site may have changed its layout. Open the page in your browser, save it as HTML into `backend/ingestion/raw/`, replace `url:` with `file: yourfile.html` in `sources.yaml`, and rerun. If the parse is still off, adjust the patterns at the top of `ingestion/parsers.py` and add a test case in `tests/test_parsers.py` for the layout you found.
3. Add the Landlord and Tenant Board brochures. Open tribunalsontario.ca/ltb/brochures, copy the links to the current PDFs you want (maintenance, rent increases, eviction for personal use, and the N4, N12 and T2 form instructions), and add them to `sources.yaml` using the commented example.

Then run the real ingest, and run it a second time:

```bash
python -m ingestion.ingest
python -m ingestion.ingest   # second run should report every chunk as unchanged
```

Why the second run: it proves ingestion is idempotent. Content hashes mean only changed chunks are embedded, which is the same incremental processing idea as change data capture.

Why one transaction per source: if a fetch fails halfway, that source is left exactly as it was rather than half updated.

## 6. Run the API locally

```bash
uvicorn app.main:app --reload --port 8080
```

In another terminal:

```bash
curl -N -X POST localhost:8080/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How much notice does my landlord need to raise the rent?"}'

curl "localhost:8080/api/search?q=no%20pets%20clause"
curl "localhost:8080/api/stats"
```

The ask endpoint streams Server Sent Events: a `meta` event with the route, many `token` events, then a `done` event with the citations and a log id. Ask the same question again and the route will be `cached`, at zero cost.

Why this order inside `/api/ask` (see the docstring in `app/main.py`): every check runs cheapest first. Validation, redaction, rate limiting, the cache and the budget check are all nearly free. Retrieval is cheap and local. The LLM is the only expensive step, so it runs last and only when everything else passes.

Why redact before anything else: personal details never reach the logs, the database, or the model provider. That is privacy by design, not privacy by policy.

## 7. Run the frontend locally

```bash
cd ../frontend
cp .env.example .env.local      # NEXT_PUBLIC_API_URL=http://localhost:8080
npm install
npm run dev
```

Open localhost:3000. Ask a question, click a citation marker, and use the feedback buttons.

Why Next.js: you know React and TypeScript, Vercel hosts it free, and the stats page uses server side rendering with a five minute revalidation, so the API is not called on every visit.

Why POST with a stream reader instead of EventSource: EventSource only supports GET, which would put questions in URLs and access logs.

## 8. Evaluation: the most important step for interviews

### 8.1 Expand the golden set to 60 questions

`evals/golden_set.jsonl` ships with 16 starter items. Grow it to roughly 40 standard questions, 10 multi part scenarios, and 10 out of scope or adversarial questions.

For each in scope item:

1. Read the actual section text and confirm the answer is there.
2. Copy the `section_ref` exactly as ingestion produced it. The easiest check is `curl "localhost:8080/api/search?q=..."`.
3. Write two or three key facts as short, checkable statements.

Treat the starter items as drafts. Verify every one against the real text before relying on it.

Why write them by hand: it forces you to read the corpus, and it makes the eval reflect real renter questions instead of whatever is easy to generate.

### 8.2 Retrieval eval (free)

```bash
cd backend
python -m evals.run_evals --mode retrieval --sweep-scope --no-gate
python -m evals.run_evals --mode retrieval --retrieval vector --no-gate
```

The first command reports hit rate at 6, MRR, scope accuracy, and the best scope threshold for your corpus. Put that threshold in `.env` as `SCOPE_THRESHOLD` (and later as a Cloud Run env var). The second command measures vector only retrieval. The difference between the two runs is your evidence for hybrid search.

Look at the printed misses. Each miss is a lesson: a chunking problem, a vocabulary gap, or a wrong golden label.

When you are happy with the results, save a baseline so CI can detect regressions:

```bash
cp evals/reports/retrieval_hybrid_latest.json evals/reports/retrieval_hybrid_baseline.json
git add evals/reports/retrieval_hybrid_baseline.json
```

### 8.3 Full eval and the routing comparison

```bash
python -m evals.run_evals --mode full --strategy small --no-gate
python -m evals.run_evals --mode full --strategy large --no-gate
python -m evals.run_evals --mode full --strategy routed
```

Compare faithfulness, key fact coverage and average cost across the three reports in `evals/reports/`. This comparison is your routing resume bullet. Results are cached in `evals/.cache`, so rerunning with no changes costs nothing.

Why temperature 0: answers are repeatable, which makes evals and caching meaningful.

### 8.4 Calibrate the judge

1. Open `evals/reports/full_routed_hybrid_latest.json` and read 20 answers yourself.
2. For each one, write `{"id": "...", "faithful": true}` or `false` into `evals/human_labels.jsonl`.
3. Run:

```bash
python -m evals.run_evals --mode full --strategy routed --calibrate evals/human_labels.jsonl
```

This reports the judge's agreement with you. If it is below about 85 percent, tighten the judge prompt in `run_evals.py` and recheck.

Why calibrate: an LLM judge is itself an unverified model. Measuring its agreement with a human is what separates real evaluation from vibes.

## 9. MCP server

### 9.1 Test locally against your local API

Add this to your Claude Desktop config (Settings, Developer, Edit Config). Use the absolute path to your `mcp_server` folder:

```json
{
  "mcpServers": {
    "tenantwise-local": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/tenantwise/mcp_server", "run", "tenantwise-mcp"],
      "env": { "TENANTWISE_API_URL": "http://localhost:8080" }
    }
  }
}
```

Restart Claude Desktop and ask it a tenancy question. It should call `search_tenancy_law` and cite sections.

Why a thin client over your API: the package holds no secrets, so it is safe to publish, and it makes no LLM calls, so every MCP user costs you almost nothing. It also reuses the exact retrieval pipeline the website uses, so quality is identical everywhere.

### 9.2 Publish (after step 10, once the API is live)

1. Set the default `API_URL` in `tenantwise_mcp/server.py` to your live API address.
2. Check that the name `tenantwise-mcp` is free on PyPI. If it is taken, change `name` in `pyproject.toml`.
3. Create a PyPI API token, then run:

```bash
cd mcp_server
uv build
uv publish --token pypi-YOUR_TOKEN
```

4. List it in the official MCP Registry by following the publishing instructions at github.com/modelcontextprotocol/registry. The process uses a small `server.json` file and the registry's publisher tool, and it checks that you own the PyPI package.

## 10. Deploy the API to Google Cloud Run

### 10.1 Project, budget and APIs

```bash
gcloud auth login
gcloud projects create tenantwise-jj --name="TenantWise"   # project ids must be globally unique
gcloud config set project tenantwise-jj
# Link billing in the console: Billing, then Link a billing account.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com
```

In the console, open Billing, then Budgets and alerts, and create a $5 monthly budget with email alerts at 50, 90 and 100 percent.

Why us-central1: it is a lowest price tier region with the full Cloud Run free tier, it is close to the Neon database, and it supports custom domain mapping.

### 10.2 Secrets

```bash
printf '%s' 'sk-ant-...'            | gcloud secrets create anthropic-api-key --data-file=-
printf '%s' 'postgresql://...'      | gcloud secrets create database-url --data-file=-
printf '%s' 'your-long-random-salt' | gcloud secrets create ip-hash-salt --data-file=-

PROJECT_NUMBER=$(gcloud projects describe tenantwise-jj --format='value(projectNumber)')
gcloud projects add-iam-policy-binding tenantwise-jj \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Why Secret Manager: secrets never appear in code, images, or plain environment config, and access is controlled and audited by IAM.

### 10.3 First deploy by hand

```bash
cd backend
gcloud run deploy tenantwise-api --source . --region us-central1 --allow-unauthenticated \
  --min-instances 0 --max-instances 2 --concurrency 20 --memory 1Gi --cpu 1 --cpu-boost --timeout 60 \
  --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest,DATABASE_URL=database-url:latest,IP_HASH_SALT=ip-hash-salt:latest" \
  --set-env-vars "^;^ALLOWED_ORIGINS=http://localhost:3000;SCOPE_THRESHOLD=0.55"
```

Replace 0.55 with the threshold you found in step 8.2. When the deploy finishes, `curl` the printed URL plus `/health`.

Why each setting:

1. Minimum 0 instances: you pay nothing while idle. The tradeoff is a cold start of a few seconds after quiet periods.
2. Maximum 2 instances: a traffic spike or an attack cannot multiply your bill.
3. 1 GiB of memory: the embedding model needs headroom.
4. Startup CPU boost: shortens cold starts at no extra cost.
5. Concurrency 20: one instance handles many streaming requests, because they mostly wait on the model.

### 10.4 Keep image storage free

Artifact Registry gives 0.5 GB of free storage, and each image is several hundred MB. In the console, open Artifact Registry, select the `cloud-run-source-deploy` repository, and add a cleanup policy that keeps only the two most recent versions.

### 10.5 Custom domain (optional but recommended)

```bash
gcloud beta run domain-mappings create --service tenantwise-api \
  --domain api.tenantwise.jeffanojohn.com --region us-central1
```

Follow the printed instructions: verify the domain in Google Search Console if asked, then add the DNS record it shows (a CNAME to `ghs.googlehosted.com`) at your DNS provider. If you skip this, use the `run.app` URL everywhere instead.

## 11. Automated deploys with Workload Identity Federation

```bash
PROJECT_ID=tenantwise-jj
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
REPO=Jeffano/tenantwise

gcloud iam workload-identity-pools create github --location=global --display-name="GitHub"
gcloud iam workload-identity-pools providers create-oidc tenantwise-repo \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'"

gcloud iam service-accounts create github-deployer --display-name="GitHub deployer"
SA=github-deployer@${PROJECT_ID}.iam.gserviceaccount.com
for ROLE in roles/run.admin roles/iam.serviceAccountUser roles/cloudbuild.builds.editor \
            roles/artifactregistry.writer roles/storage.admin roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:${SA}" --role="$ROLE"
done

gcloud iam service-accounts add-iam-policy-binding $SA --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO}"

echo "GCP_WIF_PROVIDER = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/tenantwise-repo"
echo "GCP_DEPLOY_SA    = ${SA}"
```

In GitHub, open Settings, then Secrets and variables, then Actions. Add these repository secrets: `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`, `DATABASE_URL`, `ANTHROPIC_API_KEY`. Add two repository variables: `ALLOWED_ORIGINS`, set to your frontend URLs (step 12) separated by commas, and `SCOPE_THRESHOLD`, set to the value from step 8.2.

The deploy workflow passes env vars with `^;^` as the separator, which is gcloud's escape syntax for values that themselves contain commas, such as the list of origins.

Why Workload Identity Federation: GitHub receives a short lived token per run, limited to your repository by the attribute condition. No long lived Google Cloud key exists that could leak. This is current best practice and a common interview question.

From now on, every push to main runs CI, and only a green CI run triggers the deploy, followed by a health check smoke test.

## 12. Deploy the frontend to Vercel

1. In Vercel, import the GitHub repository and set the Root Directory to `frontend`.
2. Add the environment variable `NEXT_PUBLIC_API_URL` with your API URL.
3. Deploy, then add the domain `tenantwise.jeffanojohn.com` under Settings, Domains, and create the CNAME record Vercel shows.
4. Update the `ALLOWED_ORIGINS` repository variable to include the Vercel URL and your custom domain, then rerun the deploy workflow.

Why CORS restricted to your origins: other websites cannot embed your API in their own pages and spend your budget.

## 13. Protect main

In GitHub, open Settings, then Branches, add a rule for `main`, require pull requests, and require the `unit-tests`, `frontend` and `retrieval-eval` checks to pass. From then on, make every change on a branch and open a pull request. Prompt and retrieval changes will also trigger the full eval.

Why: quality becomes a release gate, the same way tests gate code. The CI history becomes visible proof that you work this way.

## 14. Launch checklist

1. Write a README with the live link, a short demo GIF, the architecture diagram, the eval results table (hybrid versus vector, small versus large versus routed, judge agreement), cost per query, and a "known limitations" section.
2. Ask five friends to break it: tricky questions, off topic requests, prompt injection attempts. Add every failure to the golden set.
3. Share it in Ontario renter communities and your university's student groups, following each community's rules on self promotion. Real users produce your real numbers.
4. Pin the repository on your GitHub profile and add the project to jeffanojohn.com.

## 15. Where your resume numbers come from

1. Users, questions, cache hit rate, p50 and p95 latency, cost per query, citation validity and helpful rate come from `GET /api/stats` and the public stats page.
2. Hit rate, MRR and the hybrid improvement come from the retrieval reports.
3. Routing savings come from comparing the three full eval reports: cost of routed versus large, and quality of routed versus large.
4. Judge agreement comes from the calibration run.

Useful extra query for the routing story, run in the Neon SQL editor:

```sql
SELECT route, route_reason, count(*), round(avg(cost_usd)::numeric, 5) AS avg_cost
FROM query_logs
WHERE created_at > now() - interval '30 days'
GROUP BY 1, 2
ORDER BY 3 DESC;
```

Only put measured numbers on your resume.

## 16. Monthly cost

| Item | Expected cost |
|---|---|
| Cloud Run, Secret Manager, Cloud Logging | $0 within free tiers |
| Artifact Registry | $0 with the cleanup policy |
| Neon Postgres | $0 on the free tier |
| Vercel Hobby | $0 |
| Embeddings | $0, local model |
| Claude usage | A fraction of a cent per answered question on the small model; capped at $0.30 a day by the app and $10 a month by the console |
| Eval runs | Cents per full run, and $0 when cached |

## 17. Troubleshooting

1. **Random "prepared statement does not exist" errors:** you are using a pooled URL without `prepare_threshold=None`. The code already sets it, so check that you are not connecting some other way.
2. **CORS errors in the browser:** the frontend origin is missing from `ALLOWED_ORIGINS`. It must match exactly, including `https://` and with no trailing slash.
3. **Streaming arrives all at once:** some proxies buffer. The API sends `X-Accel-Buffering: no`, and Cloud Run streams by default. Test with `curl -N` to isolate the problem.
4. **Every question is refused:** your `SCOPE_THRESHOLD` is too high. Rerun the sweep from step 8.2.
5. **Cold starts feel slow:** that is the scale to zero tradeoff. For a demo day, temporarily set `--min-instances 1` and set it back afterwards.
6. **The 429 limit triggers while testing:** raise `RATE_LIMIT_PER_HOUR` locally, or delete your rows from `query_logs`.
