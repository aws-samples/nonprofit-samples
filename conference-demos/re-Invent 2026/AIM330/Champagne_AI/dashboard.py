# ========== COST DASHBOARD SERVER ==========
# FastAPI server that receives cost reports from feature files and streams
# real-time updates to the browser via Server-Sent Events (SSE).
#
# Start with: python dashboard.py
# Access at:  http://localhost:8050

import asyncio
import json
import time
from decimal import Decimal

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

import config

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Champagne AI Cost Dashboard")

# ── Session state ─────────────────────────────────────────────────────────────

session_state = {
    "cumulative_cost_usd":    Decimal("0"),
    "cumulative_savings_usd": Decimal("0"),
    "cumulative_naive_usd":   Decimal("0"),
    "request_count":          0,
    "session_start_time":     time.monotonic(),
    "active_file":            "none",
}

sse_client_queues: list = []


# ── Request model ─────────────────────────────────────────────────────────────

class CostReportPayload(BaseModel):
    model_name:    str
    input_tokens:  int
    output_tokens: int
    cost_usd:      str
    naive_cost:    str = Field(alias="naive_cost_usd")
    request_label: str


# ── Helper: spend color ───────────────────────────────────────────────────────

def get_spend_color(spend: Decimal) -> str:
    """Return 'normal', 'yellow', or 'red' based on spend vs config thresholds."""
    if spend >= config.DANGER_SPEND_USD:
        return "red"
    if spend >= config.WARNING_SPEND_USD:
        return "yellow"
    return "normal"


# ── Helper: SSE snapshot ──────────────────────────────────────────────────────

def build_sse_snapshot() -> dict:
    """Build the JSON snapshot to push to connected SSE clients."""
    cumulative_cost    = session_state["cumulative_cost_usd"]
    cumulative_savings = session_state["cumulative_savings_usd"]
    cumulative_naive   = session_state["cumulative_naive_usd"]
    request_count      = session_state["request_count"]

    # Both daily projections use the assumed daily workload from config (10K reqs/day).
    if request_count > 0:
        cost_per_request      = cumulative_cost / request_count
        savings_per_request   = cumulative_savings / request_count
        estimated_daily_cost  = cost_per_request * config.ASSUMED_REQUESTS_PER_DAY
        daily_savings_usd     = savings_per_request * config.ASSUMED_REQUESTS_PER_DAY
    else:
        daily_savings_usd    = Decimal("0")
        estimated_daily_cost = Decimal("0")

    return {
        "session_cost":         f"{cumulative_cost:.4f}",
        "estimated_daily_cost": f"{estimated_daily_cost:.2f}",
        "savings_usd":          f"{cumulative_savings:.4f}",
        "daily_savings":        f"{daily_savings_usd:.2f}",
        "cumulative_naive":     f"{cumulative_naive:.4f}",
        "active_file":          session_state["active_file"],
        "spend_color":          get_spend_color(cumulative_cost),
    }


# ── POST /api/cost ────────────────────────────────────────────────────────────

@app.post("/api/cost")
async def post_cost(payload: CostReportPayload):
    """Receive cost data from a feature file and update session state."""
    cost_usd = Decimal(payload.cost_usd)

    # The page declares its own all-champagne baseline; the dashboard no longer
    # recomputes it from the reported token counts.
    naive_cost       = Decimal(payload.naive_cost)
    request_savings  = max(Decimal("0"), naive_cost - cost_usd)

    session_state["cumulative_cost_usd"]    += cost_usd
    session_state["cumulative_savings_usd"] += request_savings
    session_state["cumulative_naive_usd"]   += naive_cost
    session_state["request_count"]          += 1
    session_state["active_file"]             = payload.request_label

    snapshot_json = json.dumps(build_sse_snapshot())
    for queue in list(sse_client_queues):
        try:
            queue.put_nowait(snapshot_json)
        except asyncio.QueueFull:
            pass

    return {"status": "ok"}


# ── POST /api/reset ───────────────────────────────────────────────────────────

@app.post("/api/reset")
async def post_reset():
    """Clear all session metrics and reset the timer."""
    session_state["cumulative_cost_usd"]    = Decimal("0")
    session_state["cumulative_savings_usd"] = Decimal("0")
    session_state["cumulative_naive_usd"]   = Decimal("0")
    session_state["request_count"]          = 0
    session_state["session_start_time"]     = time.monotonic()
    session_state["active_file"]            = "none"

    snapshot_json = json.dumps(build_sse_snapshot())
    for queue in list(sse_client_queues):
        try:
            queue.put_nowait(snapshot_json)
        except asyncio.QueueFull:
            pass

    return {"status": "reset"}


# ── GET /events — SSE stream ──────────────────────────────────────────────────

@app.get("/events")
async def get_events():
    """Server-Sent Events endpoint — streams JSON snapshots to the browser."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    sse_client_queues.append(queue)
    await queue.put(json.dumps(build_sse_snapshot()))

    async def event_generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except Exception:
            pass  # nosec B110
        finally:
            if queue in sse_client_queues:
                sse_client_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── GET / — dashboard HTML page ───────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Champagne AI on a Beer Budget</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: #0d1117;
      color: #e6edf3;
      font-family: 'Segoe UI', system-ui, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 40px 24px;
    }

    h1 { font-size: 2rem; font-weight: 700; color: #58a6ff; margin-bottom: 8px; }
    .subtitle { font-size: 1rem; color: #8b949e; margin-bottom: 32px; }

    .active-file {
      font-size: 1.1rem;
      color: #3fb950;
      margin-bottom: 32px;
      font-family: monospace;
    }

    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 24px;
      width: 100%;
      max-width: 1400px;
      margin-bottom: 32px;
    }

    .metric-panel {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 32px 24px;
      text-align: center;
    }

    /* minimum 24px labels, minimum 48px values, >=7:1 contrast on dark bg */
    .metric-label {
      font-size: 24px;
      font-weight: 500;
      color: #8b949e;
      margin-bottom: 16px;
    }

    .metric-value {
      font-size: 48px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      letter-spacing: -1px;
    }

    .metric-value.color-normal  { color: #e6edf3; }  /* contrast >= 7:1 on #0d1117 */
    .metric-value.color-yellow  { color: #f0c33c; }
    .metric-value.color-red     { color: #f85149; }

    /* 72px savings banner displayed for 15 seconds after final pipeline request */
    #savings-percent-banner {
      display: none;
      background: #1c2128;
      border: 2px solid #3fb950;
      border-radius: 16px;
      padding: 40px 64px;
      text-align: center;
      width: 100%;
      max-width: 900px;
      margin-bottom: 32px;
    }

    #savings-percent-banner .savings-pct-value {
      font-size: 72px;
      font-weight: 800;
      color: #3fb950;
    }

    #savings-percent-banner .savings-pct-label {
      font-size: 28px;
      color: #3fb950;
      margin-top: 8px;
    }

    .controls { display: flex; gap: 16px; margin-top: 24px; }

    button {
      padding: 12px 32px;
      border-radius: 8px;
      border: none;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.15s;
    }

    button:hover { opacity: 0.85; }

    .btn-reset {
      background: #21262d;
      color: #e6edf3;
      border: 1px solid #30363d;
    }

    .connection-status { font-size: 0.85rem; color: #8b949e; margin-top: 24px; }
    .dot-connected { color: #3fb950; }
    .dot-disconnected { color: #f85149; }
  </style>
</head>
<body>
  <h1>Champagne AI on a Beer Budget</h1>
  <p class="subtitle">Real-Time Cost Dashboard — AWS re:Invent 2026</p>

  <div class="active-file" id="active-file">Waiting for feature file...</div>

  <div id="savings-percent-banner">
    <div class="savings-pct-value" id="savings-pct-value">0%</div>
    <div class="savings-pct-label">Savings vs Naive Baseline</div>
  </div>

  <div class="metrics-grid">
    <div class="metric-panel">
      <div class="metric-label">Current Session Cost</div>
      <div class="metric-value color-normal" id="session-cost">$0.0000</div>
    </div>
    <div class="metric-panel">
      <div class="metric-label">Est. Daily Cost (10K reqs)</div>
      <div class="metric-value color-normal" id="estimated-daily-cost">$0.00</div>
    </div>
    <div class="metric-panel">
      <div class="metric-label">Savings vs. Naive</div>
      <div class="metric-value color-normal" id="savings-usd">$0.0000</div>
    </div>
    <div class="metric-panel">
      <div class="metric-label">Est. Daily Savings (10K reqs)</div>
      <div class="metric-value color-normal" id="daily-savings">$0.00</div>
    </div>
  </div>

  <div class="controls">
    <button class="btn-reset" onclick="resetSession()">Reset Session</button>
  </div>

  <div class="connection-status">
    <span id="conn-dot" class="dot-disconnected">&#9679;</span>
    <span id="conn-status"> Connecting...</span>
  </div>

  <script>
    const es = new EventSource('/events');

    es.onopen = function() {
      document.getElementById('conn-dot').className = 'dot-connected';
      document.getElementById('conn-status').textContent = ' Connected';
    };

    es.onerror = function() {
      document.getElementById('conn-dot').className = 'dot-disconnected';
      document.getElementById('conn-status').textContent = ' Disconnected';
    };

    es.onmessage = function(event) {
      const data = JSON.parse(event.data);

      const costEl = document.getElementById('session-cost');
      costEl.textContent = '$' + data.session_cost;
      costEl.className = 'metric-value color-' + data.spend_color;

      document.getElementById('savings-usd').textContent = '$' + data.savings_usd;
      document.getElementById('daily-savings').textContent = '$' + data.daily_savings;
      document.getElementById('estimated-daily-cost').textContent = '$' + data.estimated_daily_cost;

      document.getElementById('active-file').textContent =
        data.active_file !== 'none' ? 'Running: ' + data.active_file : 'Waiting for feature file...';

      // Hide banner on reset (active_file goes to 'none' with zero values)
      if (data.active_file === 'none') {
        document.getElementById('savings-percent-banner').style.display = 'none';
      }

      // Banner percentage = cumulative savings / cumulative naive baseline.
      // savings_usd is clamped per request (max(0, naive - cost)) on the server
      // while cumulative_naive is not, so this can differ slightly from the raw
      // (naive - cost)/naive when any request's optimized cost exceeded its naive.
      const savingsVal = parseFloat(data.savings_usd);
      const naiveVal   = parseFloat(data.cumulative_naive);
      if (savingsVal > 0 && naiveVal > 0) {
        const pct = ((savingsVal / naiveVal) * 100).toFixed(1);
        document.getElementById('savings-pct-value').textContent = pct + '%';
        document.getElementById('savings-percent-banner').style.display = 'block';
      }
    };

    function resetSession() {
      fetch('/api/reset', { method: 'POST' });
      document.getElementById('savings-percent-banner').style.display = 'none';
    }
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Serve the dashboard HTML page."""
    return HTMLResponse(content=DASHBOARD_HTML, status_code=200)


# ── Start server ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Starting Cost Dashboard at http://localhost:8050")
    uvicorn.run(app, host="0.0.0.0", port=8050, log_level="warning")  # nosec B104
