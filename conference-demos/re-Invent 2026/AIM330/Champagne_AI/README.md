# Champagne AI on a Beer Budget
## Presenter Guide -- AWS re:Invent 2026 Live Code Talk

---

## Setup

### Prerequisites

- **Python 3.12+** -- check with `python3 --version`.
- **AWS credentials** -- configured in `~/.aws/credentials` or as environment variables.
- **IAM permissions** -- your AWS identity needs `bedrock:InvokeModel` on the three models below.
  The minimal policy scopes the resource to those models only, matching the model IDs in the
  "Bedrock model access" section just below (replace `<region>` with your Bedrock region):
  ```json
  {
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": [
        "arn:aws:bedrock:<region>::foundation-model/global.anthropic.claude-opus-5",
        "arn:aws:bedrock:<region>::foundation-model/us.amazon.nova-lite-v1:0",
        "arn:aws:bedrock:<region>::foundation-model/amazon.titan-embed-text-v2:0"
      ]
    }]
  }
  ```
- **Bedrock model access** -- enable these models in the Bedrock console for your region:
  - `global.anthropic.claude-opus-5` (the "champagne" model)
  - `us.amazon.nova-lite-v1:0` (the "beer" model)
  - `amazon.titan-embed-text-v2:0` (embeddings, used by the semantic cache segment)

### Install dependencies

```bash
pip install -r requirements.txt
```

### Pre-flight check

Run this before going on stage to validate credentials and Bedrock access:

```bash
python -c "
import boto3, json, sys
client = boto3.client('bedrock-runtime')
try:
    r = client.converse(
        modelId='us.amazon.nova-lite-v1:0',
        messages=[{'role':'user','content':[{'text':'ping'}]}]
    )
    print('OK -- Bedrock access confirmed. Model responded.')
except Exception as e:
    print(f'FAIL -- {e}')
    sys.exit(1)
"
```

### Start the dashboard

```bash
python dashboard.py
```

Open **http://localhost:8050** in a browser. Leave this running throughout the demo.

---

## Project structure

```
config.py            -- Model IDs, pricing constants, budget thresholds
helpers.py           -- Shared utilities (retry, cost math, prompt caching, parsing)
parallel.py          -- Threaded workload runner for fast batch execution
prompts.json         -- Demo prompts with per-task-type "instructions" field
dashboard.py         -- Live cost dashboard (Dash app)
dashboard_client.py  -- Client for reporting costs to the dashboard

01_naive.py          -- Baseline: everything goes to champagne model
02_router.py         -- Classify tasks, route cheap ones to beer model
02_cascading.py      -- Try beer first, escalate to champagne if confidence < 0.8
03_prompt_cache.py   -- Native Bedrock prompt caching (90% off repeated context)
03_semantic_cache.py -- Embed + cosine similarity cache, skip champagne on a hit
04_budget.py         -- Token budget with graceful degradation to beer model
05_pipeline.py       -- Everything combined: cascading + caching + budget
```

Each numbered file defines one `call_bedrock()` function (its strategy) and a `__main__` block that runs all prompts through it. Shared plumbing lives in `helpers.py`.

---

## Presenting

The demo is six segments using seven files. Walk through the code first (explain on screen), then run the file live. The audience sees real AWS costs appear on the dashboard.

---

### Segment 1 -- Problem setup (01_naive.py)

**Command:**
```bash
python 01_naive.py
```

**Expected output:** Each prompt prints its cost as it completes. Total cost climbs. Dashboard shows spend rising in real time.

**Talking points:**
- "This is what most teams start with. Every request goes to Claude Opus."
- "No filtering. No optimization. Just API calls and bills."
- Point to the dashboard. "Watch that number. This is what we are going to fix."

---

### Segment 2a -- Model routing (02_router.py)

**Command:**
```bash
python 02_router.py
```

**Expected output:** Each prompt is classified into a task type, then routed to the appropriate model. Simple tasks (classification, extraction, summarization) go to Nova Lite. Complex reasoning goes to Opus. Per-request savings printed.

**Talking points:**
- "Same prompts. But now we ask: does this actually need a $5-per-million-token model?"
- "Classification, extraction, summarization -- Nova Lite handles these at a fraction of the cost."
- "The classifier itself runs on Nova Lite. The routing overhead is negligible."

---

### Segment 2b -- Model cascading (02_cascading.py)

**Command:**
```bash
python 02_cascading.py
```

**Expected output:** Each prompt goes to beer model first. The model returns a confidence score. High confidence (>= 0.8) means we accept. Low confidence means we escalate to champagne. Most prompts resolve at beer tier.

**Talking points:**
- "What if we let the cheap model try first and only escalate when it is unsure?"
- "We ask for a confidence score in the response. Below 0.8, we call the expensive model."
- "Most nonprofit tasks are straightforward. In this demo's prompt set, beer handles 80%+ at high confidence; results vary by workload."
- "You only pay champagne prices on the genuinely hard prompts."

---

### Segment 3 -- Native prompt caching (03_prompt_cache.py)

**Command:**
```bash
python 03_prompt_cache.py
```

**Expected output:** First request per task type shows "CACHE WRITE." Subsequent requests of the same type show "CACHE HIT" with savings. Dashboard shows significant cost reduction.

**Talking points:**
- "Your system prompt and task instructions are the same for every request of a given type. Why pay full price to process them every time?"
- "One JSON block: `cachePoint` with type `default`. Bedrock handles the rest."
- "First request: 25% premium to warm the cache. Every subsequent request: 90% off those tokens, per AWS Bedrock prompt caching pricing."
- "Five-minute TTL, resets on every hit. For a chatbot handling one request per minute, the cache stays warm indefinitely."
- Show the system prompt in `config.py`. "This is your org context, your instructions. All cached."

---

### Segment 4 -- Semantic caching (03_semantic_cache.py)

**Command:**
```bash
python 03_semantic_cache.py
```

**Expected output:** Each prompt's variable content is embedded with Titan Embeddings V2 and checked against a growing cache of prior embeddings by cosine similarity. Requests run on 10 threads, same as every other segment, so hits and misses interleave in whatever order threads finish rather than in strict prompt order. `prompts.json` contains 25 distinct scenarios followed by 25 near-verbatim repeats, so expect roughly 25 CACHE MISS lines (each paying full champagne price) and 25 CACHE HIT lines (each paying only for the embedding call, similarity close to 1.000) and an overall savings figure in the 20% range.

**Talking points:**
- "Native prompt caching matches identical prefixes. This matches identical *meaning*."
- "A donor email asking about a tax receipt today looks a lot like one from last week, even if the wording is different. Literal caching misses that. Semantic caching catches it."
- "We embed the prompt, compare it to everything we've already answered, and if it's similar enough, skip the expensive model entirely."
- Point at a CACHE HIT line. "That request cost us a fraction of a cent for an embedding call instead of a full Opus call."
- "This runs on 10 threads like every other segment. A lock around the cache read-and-reserve step keeps two threads from racing the same near-duplicate prompt into two champagne calls."
- Note on the threshold: this file uses a similarity cutoff of 0.90, tighter than the 0.75 shown as `SIMILARITY_THRESHOLD` in `config.py`. "We measured this against our own prompt set. Different-but-similar-genre requests, like two distinct volunteer surveys, landed as high as 0.886. A 0.90 cutoff clears that with margin, so genuinely different scenarios stay separate."

---

### Segment 5 -- Token budgets with graceful degradation (04_budget.py)

**Command:**
```bash
python 04_budget.py
```

**Expected output:** Requests start on the champagne model. As the token budget depletes, an alert fires. Once budget hits zero, requests automatically degrade to the beer model. No requests are rejected -- the system stays available.

**Talking points:**
- "Nonprofits and startups have hard limits. You cannot let the bill run unchecked."
- "When the budget runs out, we do not reject requests. We degrade gracefully."
- "The system stays available. Quality drops slightly on complex tasks, but nobody gets turned away."
- Point to `KILL_SWITCH_ACTIVE` in the code. "One flag. Forces every request to the cheapest model instantly. Emergency cost control."

---

### Segment 6 -- Full optimized pipeline (05_pipeline.py)

**Command:**
```bash
python 05_pipeline.py
```

**Expected output:** All optimizations combined: prompt caching on the system prefix, cascading (beer first, escalate if unsure), budget degradation when tokens run out. Terminal shows RESOLVED/ESCALATED/DEGRADED per request. Savings percentage printed at the end.

**Talking points:**
- "This is everything working together."
- "Prompt caching on the system prefix. Cascading for model selection. Budget degradation as the safety net."
- Let the final savings number land. "That is real AWS charges, right now, on this batch."
- "Champagne quality. Beer budget."

---

## Beyond the demo: what else is in the toolbox

These Bedrock features were not demonstrated live but are worth calling out in closing remarks or Q&A.

### Batch inference / Flex processing (50% discount)

Submit a JSONL file to S3, get results within 24 hours, pay 50% less than on-demand, per AWS Bedrock batch inference pricing. For nonprofits running nightly donor classification or weekly grant summarization, this is the lowest-effort optimization.

The **Flex tier** uses the same real-time API (add `serviceTier: "flex"` to your request) but trades latency guarantees for the same 50% discount, per AWS Bedrock pricing.

### Cost allocation tags

Bedrock supports inference-level cost allocation tags. Track spending by program, grant, or cost center via AWS Cost Explorer.

### The cost optimization progression

1. **Model right-sizing** -- swap model IDs; per AWS Bedrock on-demand pricing, Nova Lite vs. Claude Opus 5 is roughly 50-80x cheaper per token depending on input/output mix (02_router.py)
2. **Model cascading** -- cheap model first, escalate only when unsure (02_cascading.py)
3. **Native prompt caching** -- add a cachePoint, 90% off repeated context, per AWS Bedrock prompt caching pricing (03_prompt_cache.py)
4. **Semantic caching** -- embed + cosine similarity, skip the model entirely on a hit (03_semantic_cache.py)
5. **Budget controls** -- enforce limits, degrade gracefully (04_budget.py)
6. **Batch / Flex** -- 50% off non-real-time workloads, per AWS Bedrock pricing
7. **Provisioned throughput** -- 40-60% off once volume is predictable, per AWS Bedrock provisioned throughput pricing
8. **Model distillation** -- up to 75% off high-volume specific tasks, per AWS published Bedrock Model Distillation benchmarks; actual savings depend on the task and distilled model chosen

A team that implements layers 1-5 captured 70%+ cost reduction against this demo's own prompt set (see `MIN_SAVINGS_PERCENT` in `config.py`). Results vary by workload and model selection.

---

## Troubleshooting

### AWS credentials not found

**Symptom:** `botocore.exceptions.NoCredentialsError`

**Recovery:** Set credentials before running any feature file:
```bash
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_REGION=us-east-1
```

---

### Bedrock throttling errors

**Symptom:** `ThrottlingException` printed during a run; some prompts retry.

**Recovery:** The retry logic handles transient throttling automatically (3 retries with exponential backoff). If errors persist:
- Reduce the number of prompts in `prompts.json`.
- Switch to a region with higher Bedrock quotas.
- Request a quota increase in the AWS Service Quotas console.

---

### Prompt caching not activating

**Symptom:** `cacheReadInputTokens` is always 0, every request shows CACHE WRITE.

**Recovery:**
1. Confirm the cached prefix exceeds the model's minimum. Claude Opus 5 requires 512 tokens per checkpoint. Nova Lite requires 1,024.
2. Confirm TTL has not expired. The 5-minute TTL resets on each cache hit. If more than 5 minutes pass between requests of the same task type, the cache evicts.
3. Verify the `cachePoint` block is correctly placed after the text in the system array.

---

### Dashboard not updating

**Symptom:** Browser shows static numbers; terminal shows "Warning: dashboard not reachable."

**Recovery:**
1. Confirm `python dashboard.py` is running in a separate terminal.
2. Open `http://localhost:8050` in the browser.
3. If the port is in use: edit `DASHBOARD_URL` in `config.py` and the port in `dashboard.py`.
4. The feature files run normally even if the dashboard is down.

---

### Feature file crashing mid-demo

**Recovery:**
1. JSON error: `python -c "import json; json.load(open('prompts.json'))"`
2. Bedrock error: check credentials and model access.
3. Skip to a later segment: just run the target file directly (e.g. `python 05_pipeline.py`).
4. Use the Reset button in the dashboard browser to clear state before re-running.