# ========== SHARED CONFIGURATION CONSTANTS ==========
# This file contains ONLY constants. No functions. No classes. No business logic.
# Any feature file may import from this file.

from decimal import Decimal

# -- Model identifiers --------------------------------------------------------
BEER_MODEL_ID      = "us.amazon.nova-lite-v1:0"
CHAMPAGNE_MODEL_ID = "global.anthropic.claude-opus-5"

# -- Routing table ------------------------------------------------------------
# Maps task type labels to Bedrock model IDs.
# classification, extraction, summarization -> cheap Nova Lite (the "beer")
# complex_reasoning -> powerful Claude Opus (the "champagne")
ROUTING_TABLE = {
    "classification":    BEER_MODEL_ID,
    "extraction":        BEER_MODEL_ID,
    "summarization":     BEER_MODEL_ID,
    "complex_reasoning": CHAMPAGNE_MODEL_ID,
}

# -- Pricing ------------------------------------------------------------------
# Pricing: Claude Opus 5 ($5/1M input, $25/1M output)
# Nova Lite ($0.06/1M input, $0.24/1M output)
# Using Decimal for exact arithmetic -- no floating-point rounding errors.
CHAMPAGNE_INPUT_COST_PER_1K_TOKENS  = Decimal("0.005000")
CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS = Decimal("0.025000")
BEER_INPUT_COST_PER_1K_TOKENS  = Decimal("0.000060")
BEER_OUTPUT_COST_PER_1K_TOKENS = Decimal("0.000240")

# Embedding pricing (Titan Embeddings V2): $0.02 / 1M tokens
EMBEDDING_COST_PER_1K_TOKENS = Decimal("0.00002")

# Model cost lookup: model_id -> (input_cost_per_1k, output_cost_per_1k)
MODEL_COSTS = {
    BEER_MODEL_ID: (BEER_INPUT_COST_PER_1K_TOKENS,  BEER_OUTPUT_COST_PER_1K_TOKENS),
    CHAMPAGNE_MODEL_ID:    (CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,     CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS),
}

# -- Native prompt caching (Bedrock feature) ----------------------------------
# Cached input tokens are charged at 0.1x the normal input rate (90% discount).
# Writing to cache costs 1.25x normal input rate (default 5-min TTL).
# Minimum tokens per checkpoint: 512 for Opus 5, 1,024 for Nova Lite. TTL resets on each hit.
CHAMPAGNE_CACHE_READ_COST_PER_1K_TOKENS  = Decimal("0.000500")   # $0.50/1M (0.1x input)
CHAMPAGNE_CACHE_WRITE_COST_PER_1K_TOKENS = Decimal("0.006250")   # $6.25/1M (1.25x input, 5-min TTL)
BEER_CACHE_READ_COST_PER_1K_TOKENS  = Decimal("0.000006")  # 0.1x of $0.00006
BEER_CACHE_WRITE_COST_PER_1K_TOKENS = Decimal("0.000075")  # 1.25x of $0.00006

# Shared system prompt used across the demo -- long enough to exceed the 1,024-token
# minimum for cache checkpoints. All feature files that enable prompt caching
# prefix their requests with this context.
NONPROFIT_SYSTEM_PROMPT = (
    "You are an AI assistant for a nonprofit organization. Your role is to help "
    "the development and fundraising team with donor communications, grant management, "
    "volunteer coordination, and program evaluation.\n\n"
    "ORGANIZATIONAL CONTEXT:\n"
    "- Organization: Pathways Forward, a 501(c)(3) nonprofit founded in 2003\n"
    "- Mission: Empowering underserved communities through education, mentorship, "
    "and workforce development programs\n"
    "- Annual budget: $2.4 million\n"
    "- Staff: 28 full-time employees across 3 program areas\n"
    "- Service area: Three counties in the greater metropolitan region\n"
    "- Key programs: Youth Mentorship (ages 12-18), Adult Workforce Readiness, "
    "Family Literacy Initiative, After-School STEM Labs\n"
    "- Donors: 1,200 active individual donors, 45 foundation relationships, "
    "12 corporate sponsors\n"
    "- Recent milestones: Received $500K capital campaign gift for new building; "
    "launched digital literacy program for seniors; achieved 4-star Charity Navigator rating\n\n"
    "COMMUNICATION GUIDELINES:\n"
    "- Maintain a warm, professional tone appropriate for donor-facing communications\n"
    "- Reference specific program outcomes and impact data when available\n"
    "- Follow AFP (Association of Fundraising Professionals) ethical standards\n"
    "- Protect donor privacy and confidential gift information\n"
    "- Use person-first language and inclusive terminology\n"
    "- Tailor formality level to the relationship stage (prospect vs. major donor vs. board)\n\n"
    "GRANT MANAGEMENT STANDARDS:\n"
    "- Track all deadlines with 2-week advance alerts\n"
    "- Extract and structure key terms from award letters for compliance tracking\n"
    "- Flag indirect cost caps, matching requirements, and special conditions\n"
    "- Maintain audit-ready documentation practices\n"
    "- Cross-reference funder requirements against organizational capacity\n\n"
    "DONOR STEWARDSHIP PROTOCOLS:\n"
    "- Classify communications by urgency for appropriate response time\n"
    "- Major gifts ($10,000+): 24-hour acknowledgment, personalized stewardship plan\n"
    "- Planned gifts: Immediate routing to Executive Director, legal review coordination\n"
    "- Lapsed donors: Re-engagement strategy aligned with last giving level\n"
    "- Corporate sponsors: Quarterly impact reports, benefit fulfillment tracking\n\n"
    "VOLUNTEER PROGRAM GUIDELINES:\n"
    "- Maintain scheduling system with 48-hour advance confirmation\n"
    "- Track training completion and certification status\n"
    "- Monitor satisfaction scores and address concerns within 72 hours\n"
    "- Recognition program: monthly spotlight, quarterly events, annual awards\n\n"
    "PROGRAM EVALUATION FRAMEWORK:\n"
    "- Report outcomes using logic model structure (inputs, activities, outputs, outcomes)\n"
    "- Benchmark against peer organizations when data is available\n"
    "- Cost-per-participant analysis for program continuation decisions\n"
    "- Longitudinal tracking at 6-month and 12-month follow-up intervals\n"
    "- Evidence-based decision making: continue, modify, or discontinue\n\n"
    "FINANCIAL ANALYSIS STANDARDS:\n"
    "- Revenue diversification assessment across all streams\n"
    "- Concentration risk identification (no single source > 30%)\n"
    "- Reserve adequacy: target 6 months operating expenses\n"
    "- Expansion readiness: revenue milestones before new commitments\n"
    "- Scenario planning: best case, expected, worst case projections\n\n"
    "DATA AND REPORTING STANDARDS:\n"
    "- All financial figures reported in USD with two decimal places\n"
    "- Percentages calculated to one decimal place\n"
    "- Date formats: Month Day, Year (e.g., January 15, 2026)\n"
    "- Citation format: include source document name and date when referencing data\n"
    "- Confidentiality levels: Public, Internal, Restricted, Board-Only\n"
    "- Data retention: 7 years for financial records, 3 years for program data\n"
    "- Annual report metrics: participants served, completion rates, outcome measures, "
    "cost efficiency ratios, donor retention rate, volunteer hours contributed\n\n"
    "COMPLIANCE AND ETHICS:\n"
    "- IRS Form 990 transparency: all information consistent with public filings\n"
    "- Gift acceptance policy: no gifts that create conflicts of interest\n"
    "- Donor Bill of Rights: respect privacy, provide accurate information, "
    "use gifts as designated, provide timely acknowledgment\n"
    "- Whistleblower protection: safe channel for reporting concerns\n"
    "- Conflict of interest: annual disclosure by all board members and senior staff\n"
    "- Anti-discrimination: programs and services available regardless of race, "
    "gender, religion, national origin, disability, or sexual orientation\n"
    "- Environmental responsibility: minimize waste in operations and events\n"
)

# -- Semantic cache -----------------------------------------------------------
SIMILARITY_THRESHOLD = 0.75      # cosine similarity; calibrated to Titan Embeddings V2 output range
MAX_CACHE_ENTRIES    = 10_000    # LRU eviction when exceeded

# -- Budget settings ----------------------------------------------------------
DEFAULT_USER_TOKEN_BUDGET = 7_500     # sized for the demo batch (~10k total); alert at 80%, rejects at end
DEFAULT_ORG_TOKEN_BUDGET  = 500_000   # tokens for the whole org
ALERT_THRESHOLD_PERCENT   = 80        # fire alert when this % of budget is consumed

# -- Dashboard thresholds -----------------------------------------------------
WARNING_SPEND_USD = Decimal("1.00")   # spend turns yellow at this amount
DANGER_SPEND_USD  = Decimal("5.00")   # spend turns red at this amount

# -- Dashboard connection ------------------------------------------------------
DASHBOARD_URL = "http://localhost:8050/api/cost"

# -- Estimated daily workload assumption --------------------------------------
# Fixed request volume used to project session metrics to a full day so the
# audience sees the savings "at scale". 10,000 requests/day is the figure shown
# on the dashboard labels ("10K reqs").
ASSUMED_REQUESTS_PER_DAY = 10_000

# -- Bedrock retry settings ---------------------------------------------------
MAX_RETRIES        = 3     # number of retries after initial failure
INITIAL_BACKOFF_MS = 500   # first retry delay in milliseconds; doubles each attempt

# -- Demo impact targets ------------------------------------------------------
# These are checked at runtime in 05_pipeline.py after the full batch completes.
MIN_SAVINGS_PERCENT   = 70               # pipeline must save at least this % vs naive
MIN_NAIVE_COST_USD    = Decimal("1.00")  # full naive batch must cost at least this
MIN_DAILY_SAVINGS_USD = Decimal("50.00") # projected daily savings must reach this
