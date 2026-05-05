# Connect for Good — Architecture

## High-Level Architecture

```
  ┌──────────┐     ┌──────────────┐
  │  Caller  │────▶│ Phone Number │
  │ (Phone)  │     │ (PSTN)       │
  └──────────┘     └──────┬───────┘
                          │
  ┌──────────┐     ┌──────▼───────────────────────────────────────────────┐
  │  Visitor │     │            Amazon Connect Instance                   │
  │ (Browser)│     │            connect-for-good-{account}                │
  └────┬─────┘     │                                                      │
       │           │  ┌────────────────┐ ┌──────────────┐ ┌────────────┐ │
       │           │  │ Donor Services │ │   Crisis     │ │  Member    │ │
       │           │  │ Contact Flow   │ │   Helpline   │ │  Services  │ │
       │           │  │                │ │   Flow       │ │  Flow      │ │
       │           │  │ • Lex bot      │ │              │ │            │ │
       │           │  │ • Intent check │ │ • Priority 1 │ │ • IVR menu │ │
       │           │  │ • Queue route  │ │ • Recording  │ │ • DTMF 1/2/3│
       │           │  │ • Payment xfer │ │ • Hours check│ │ • Lex bot  │ │
       │           │  └───────┬────────┘ └──────┬───────┘ └─────┬──────┘ │
       │           │          │                  │               │        │
       │           │  ┌───────▼──────────────────▼───────────────▼──────┐ │
       │           │  │              Unlimited AI                      │ │
       │           │  │  • Real-time sentiment analysis                │ │
       │           │  │  • Post-contact summaries                      │ │
       │           │  │  • Call transcription                          │ │
       │           │  └────────────────────────────────────────────────┘ │
       │           │                                                      │
       │           │  ┌──────────────────────────────────────────┐       │
       │           │  │            Queues                         │       │
       │           │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │       │
       │           │  │  │  Donor   │ │  Crisis  │ │  Member  │ │       │
       │           │  │  │ Services │ │ Helpline │ │ Services │ │       │
       │           │  │  │ (Pri: 1) │ │ (Pri: 1) │ │ (Pri: 2) │ │       │
       │           │  │  └──────────┘ └──────────┘ └──────────┘ │       │
       │           │  └──────────────────┬───────────────────────┘       │
       │           │                     │                                │
       │           │  ┌──────────────────▼───────────────────────┐       │
       │           │  │  Routing Profile: Connect for Good       │       │
       │           │  │  • Voice: concurrency 1                  │       │
       │           │  │  • Chat:  concurrency 3                  │       │
       │           │  └──────────────────┬───────────────────────┘       │
       │           │                     │                                │
       │           │  ┌──────────────────▼───────────────────────┐       │
       │           │  │  Users                                    │       │
       │           │  │  • admin (Admin security profile)         │       │
       │           │  │  • agent (Agent security profile)         │       │
       │           │  │  • Soft phone, 30s after-contact work     │       │
       │           │  └──────────────────────────────────────────┘       │
       │           │                                                      │
       │           │  Hours of Operation: 24/7                            │
       │           └──────────────────────────────────────────────────────┘
       │                          │
       │                          │
       │           ┌──────────────▼──────────────┐
       │           │    Amazon Lex V2             │
       │           │    ConnectForGoodBot          │
       │           │                              │
       │           │  Intents:                    │
       │           │  • DonationStatus            │
       │           │  • MakeADonation             │
       │           │  • VolunteerSignup           │
       │           │  • FAQ                       │
       │           │  • FallbackIntent            │
       │           └──────────────┬───────────────┘
       │                          │
       │           ┌──────────────▼──────────────┐
       │           │    AWS Lambda                │
       │           │    Lex Fulfillment           │
       │           │    (Python 3.12)             │
       │           │                              │
       │           │  Handles:                    │
       │           │  • Donation lookup (simulated)│
       │           │  • Donation processing       │
       │           │  • Volunteer registration    │
       │           │  • FAQ responses             │
       │           └─────────────────────────────┘
       │
       │
       │           ┌─────────────────────────────┐
       │           │    Amazon S3                 │
       │           │    Call Recordings Bucket    │
       │           │                              │
       │           │  • /recordings/  (calls)     │
       │           │  • /transcripts/ (chat)      │
       │           │  • AES-256 encryption        │
       │           │  • 365-day lifecycle          │
       │           └─────────────────────────────┘
       │
       │
       │    ┌──────▼──────────────────────────────┐
       │    │    Amazon CloudFront                 │
       │    │    Chat Demo Distribution            │
       │    │                                      │
       │    │  • HTTPS redirect                    │
       │    │  • Origin Access Control (OAC)       │
       │    │  • Caching optimized                 │
       │    └──────────────┬───────────────────────┘
       │                   │
       └───────────────────┘
                           │
            ┌──────────────▼──────────────────────┐
            │    Amazon S3                         │
            │    Chat Demo Website Bucket          │
            │                                      │
            │  • index.html (chat widget page)     │
            │  • Block all public access           │
            │  • Served via CloudFront only        │
            └─────────────────────────────────────┘
```

## Service Summary

| AWS Service | Resource | Purpose |
|-------------|----------|---------|
| Amazon Connect | Instance | Cloud contact center with Contact Lens AI |
| Amazon Connect | 3 Contact Flows | Donor Services, Crisis Helpline, Member Services call routing |
| Amazon Connect | 3 Queues | Route calls to appropriate agent groups |
| Amazon Connect | Routing Profile | Assign agents to queues with voice/chat concurrency |
| Amazon Connect | 2 Users | Admin and Demo Agent for testing |
| Amazon Connect | Hours of Operation | 24/7 availability for demo |
| Amazon Connect | Unlimited AI | Post-contact summaries, sentiment analysis, transcription |
| Amazon Lex V2 | ConnectForGoodBot | Natural language chatbot with 4 intents + fallback |
| AWS Lambda | Lex Fulfillment | Python function handling bot intent logic |
| Amazon S3 | Recordings Bucket | Stores call recordings and chat transcripts (encrypted, 365-day lifecycle) |
| Amazon S3 | Website Bucket | Hosts chat demo static site (private, CloudFront-only access) |
| Amazon CloudFront | Distribution | HTTPS CDN for the chat demo website with OAC |
| AWS CDK | Stack | Infrastructure as code for one-click deployment |

## Contact Flow Details

### Donor Services Flow
```
Welcome Message
    │
    ▼
Lex Bot (ConnectForGoodBot)
    │
    ├── MakeADonation ──▶ Payment Transfer Message ──▶ Transfer to Queue
    │
    ├── DonationStatus ──▶ (Lex fulfills via Lambda)
    │
    ├── VolunteerSignup ──▶ (Lex fulfills via Lambda)
    │
    ├── FAQ ──▶ (Lex fulfills via Lambda)
    │
    └── No Match ──▶ Transfer to Queue
                          │
                          ▼
                      Disconnect
```

### Crisis Helpline Flow
```
Welcome Message (911 advisory)
    │
    ▼
Set Priority = 1, callType = crisis
    │
    ▼
Enable Recording (Agent + Customer)
    │
    ▼
Check Hours of Operation
    │
    ├── In Hours ──▶ Hold Message ──▶ Transfer to Queue ──▶ Disconnect
    │
    └── After Hours ──▶ Callback Message ──▶ Disconnect
```

### Member Services Flow
```
Welcome Message (IVR menu)
    │
    ▼
Get Input (DTMF + Lex Bot)
    │
    ├── Press 1 ──▶ Renewal Message ──▶ Transfer to Queue
    │
    ├── Press 2 ──▶ Event Message ──▶ Transfer to Queue
    │
    ├── Press 3 ──▶ General Message ──▶ Transfer to Queue
    │
    ├── Lex Match ──▶ (Bot handles) ──▶ Transfer to Queue
    │
    └── No Match ──▶ Transfer to Queue
                          │
                          ▼
                      Disconnect
```
