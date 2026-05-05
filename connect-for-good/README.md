# Connect for Good — Amazon Connect Nonprofit Contact Center Demo

A reusable, one-click deployable Amazon Connect solution for the Nonprofit SA team. Demonstrates Amazon Connect capabilities tailored to nonprofit use cases: donor services, crisis helplines, and member services. Includes a CloudFront-hosted chat demo website.

## Architecture

```
                    ┌─────────────────────────────────┐
                    │     Amazon Connect Instance       │
                    │        (Contact Lens enabled)     │
                    │                                   │
                    │  ┌───────────┐ ┌───────────────┐ │
                    │  │  Donor    │ │   Crisis      │ │
                    │  │  Services │ │   Helpline    │ │
                    │  │  Flow     │ │   Flow        │ │
                    │  └─────┬─────┘ └───────┬───────┘ │
                    │  ┌─────┴───────────────┴───────┐ │
                    │  │    Member Services Flow      │ │
                    │  └─────────────┬───────────────┘ │
                    └────────────────┼─────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
        ┌─────▼─────┐        ┌──────▼──────┐       ┌───────▼──────┐
        │  Lex V2   │        │ S3 Bucket   │       │  CloudFront  │
        │  Chatbot  │        │ (Recordings │       │  + S3 Chat   │
        │           │        │  + Transcr.)│       │  Demo Site   │
        └─────┬─────┘        └─────────────┘       └──────────────┘
              │
        ┌─────▼─────┐
        │  Lambda   │
        │  Lex      │
        │  Fulfill. │
        └───────────┘
```

## What Gets Deployed

| Resource | Description |
|----------|-------------|
| Amazon Connect Instance | `connect-for-good-{account}` with Contact Lens enabled |
| 4 Contact Flows | Main IVR (entry point), Donor Services (Lex + queue routing), Crisis Helpline (priority routing), Member Services (IVR + Lex) |
| Hours of Operation | 24/7 for demo availability |
| 3 Queues | Donor Services, Crisis Helpline, Member Services |
| Routing Profile | Demo profile with voice + chat concurrency |
| 2 Users | Admin and Demo Agent (soft phone) |
| Lex V2 Bot | `ConnectForGoodBot` with 4 intents + fallback |
| Lambda: Lex Fulfillment | Handles DonationStatus, MakeADonation, VolunteerSignup, FAQ |
| S3: Call Recordings | Encrypted, 365-day lifecycle (recordings + transcripts) |
| S3 + CloudFront: Chat Demo | Static website for demoing Connect chat functionality |
| Amazon Q in Connect | Assistant domain + CUSTOM knowledge base |
| S3: Knowledge Base | Sample nonprofit documents (donation policy, volunteer handbook, membership guide, crisis resources, events) |
| Contact Lens | Built-in post-contact summaries and real-time sentiment analysis |

## Prerequisites

- AWS CLI v2 configured with credentials (e.g. via `ada credentials update`)
- Node.js 18+
- AWS CDK v2 (`npm install -g aws-cdk`)
- CDK bootstrapped in target account/region

## Environment Setup

### 1. Install dependencies

```bash
# Install Node.js 18+ (if not already installed)
# macOS with Homebrew:
brew install node

# Install AWS CDK globally
npm install -g aws-cdk

# Install project dependencies
npm install
```

### 2. Configure AWS credentials

```bash
# Using ADA (Amazon internal):
ada credentials update --account=<ACCOUNT_ID> --provider=isengard --role=<ROLE_NAME> --once

# Or configure AWS CLI directly:
aws configure
```

### 3. Bootstrap CDK (first time only)

```bash
npx cdk bootstrap aws://<ACCOUNT_ID>/<REGION>
```

### 4. Deploy

```bash
npx cdk deploy --require-approval never
```

CDK will output the Connect Instance ID, Lex Bot ID, S3 bucket names, and the Chat Demo URL.

### 5. Post-deployment setup

After CDK deploys, complete these steps in the Connect console:

1. **Log in to the Connect admin console:**
   - Go to the [AWS Console](https://console.aws.amazon.com/connect/) → Amazon Connect
   - Click on your `connect-for-good-*` instance
2. **Log in to the Connect CCP** with the pre-configured users:
   - Admin: username `admin`, password `ConnectDemo2026!`
   - Agent: username `agent`, password `ConnectDemo2026!`
   - Both users have custom security profiles with Connect Assistant (Amazon Q) permissions
3. **Claim a phone number** and associate it with the "Connect for Good - Main IVR" contact flow (this is the single entry point that routes to all three services)
4. **Amazon Q in Connect** — automatically configured:
   - The CDK deploys an AI agent domain, S3 knowledge base bucket with sample documents, and an AppIntegrations data integration
   - Documents sync automatically from S3 on a daily schedule
   - To associate the domain with your instance: Connect console > AI Agents > select the `connect-for-good-assistant-*` domain
   - Sample documents included: donation policy, volunteer handbook, membership guide, crisis resources, events and programs
5. **Configure the chat demo website:**
   - Open the [Amazon Connect console](https://console.aws.amazon.com/connect/)
   - Select your `connect-for-good-*` instance
   - Go to **Channels** → **Chat** → **Communications widget**
   - Click **Add communications widget**
   - Choose the "Donor Services" contact flow
   - Enable both **Voice** and **Chat** channels on the widget
   - Customize the widget appearance (optional)
   - Under **Allowed domains**, add your CloudFront URL from the CDK output `ChatDemoUrl` (e.g. `https://d1234abcd.cloudfront.net`)
   - Click **Save** and copy the generated `<script>` snippet
   - Open `website/index.html` and replace the existing widget script with the new snippet
   - Set `iconType` to `'CHAT'` for text chat or `'VOICE'` for click-to-call in the `amazon_connect('styles', ...)` block
   - Redeploy with `npx cdk deploy` to push the updated HTML
6. **Test** by calling the claimed phone number or visiting the CloudFront chat demo URL

> **Note:** When opening contact flows in the Connect flow designer for the first time, the blocks may appear stacked on top of each other. This is a known limitation of deploying flows via CloudFormation — the visual layout metadata isn't part of the flow JSON. Simply drag the blocks apart to arrange them. This is a one-time step per flow.

## Demo Scenarios

### 1. Donor Services
- Caller checks donation status, makes a pledge, or asks FAQs
- Lex chatbot handles self-service; transfers to agent queue for payments
- Contact Lens provides post-contact summaries automatically

### 2. Crisis / Helpline
- Priority 1 routing with sentiment analysis (Contact Lens)
- Call recording enabled for both agent and customer
- Hours-of-operation check with after-hours callback messaging
- Supervisor barge-in ready

### 3. Member Services
- IVR menu (DTMF: press 1/2/3) + natural language (Lex)
- Membership renewals, event info, general inquiries
- Skills-based routing to appropriate queue

### 4. Chat Demo Website
- CloudFront-hosted static site at the `ChatDemoUrl` output
- Demonstrates Amazon Connect chat widget integration
- Connects to the Lex bot for self-service chat

## SA Demo Guide

### Quick Demo (5 minutes)
1. Open the Connect instance in the AWS Console
2. Show the 3 contact flows in the flow designer
3. Call the demo number → walk through Donor Services scenario
4. Show Contact Lens analytics and post-contact summaries
5. Open the chat demo website and walk through a chat interaction
6. Show the Amazon Q panel in the agent workspace — search for "donation policy" or "volunteer requirements"

### Full Demo (30 minutes)
1. Walk through all 3 scenarios with live calls
2. Show Lex chatbot handling self-service (voice + chat)
3. Demonstrate Contact Lens real-time sentiment and post-contact summaries
4. Demo the chat website with the Connect widget
5. Show Amazon Q in Connect agent assist:
   - While on a chat or call, open the Amazon Q panel in the agent workspace
   - Search for topics like "tax deductions", "membership tiers", "crisis resources", "volunteer programs"
   - Show how agents get instant answers from the knowledge base without putting the customer on hold
   - Highlight that the knowledge base auto-syncs from S3 — just add/update documents and redeploy
6. Discuss pricing: pay-per-minute vs competitor per-seat
7. Highlight AWS Imagine Grant for nonprofit credits

## Customization Guide

Tailor the solution for a specific customer demo by editing these files. Redeploy with `npx cdk deploy` after making changes.

### Website Branding (`website/index.html`)
- Update the `<h1>` and `<title>` with the customer's organization name
- Change the header gradient colors in the `<style>` section (default: `#2d6a4f`, `#40916c`)
- Swap the card titles and descriptions to match the customer's use cases
- Add a logo image to the header

### Bot Responses (`lambda/lex-fulfillment/index.py`)
This is where all the chatbot reply text lives. The Lambda handles fulfillment for each intent:
- `donation_status()` — customize the simulated donation lookup response
- `make_donation()` — update the payment transfer message
- `volunteer_signup()` — change program areas and confirmation text
- `faq()` — update the FAQ answers (hours, tax info, locations, employer matching) and add new topics to the `answers` and `keyword_map` dictionaries

### Contact Flow Messages (`lib/connect-for-good-stack.ts`)
- Update welcome messages in each flow (search for `MessageParticipant` actions)
- Customize the after-hours, transfer, and agent-unavailable messages
- Change the flow names to match the customer's branding

### Lex Bot Prompts (`lib/connect-for-good-stack.ts`)
- Update slot elicitation prompts (e.g. "How much would you like to donate?")
- Add or modify sample utterances to match the customer's terminology
- Add new intents for customer-specific use cases

### Chat Widget (Connect Console — post-deployment)
- Customize widget colors, icon style, and greeting text
- Set `iconType` to `CHAT`, `VOICE`, or `CHAT_VOICE` in the widget snippet

## Useful Commands

```bash
npx cdk diff      # Compare local stack with deployed
npx cdk synth     # Generate CloudFormation template
npx cdk deploy    # Deploy stack to AWS
npx cdk destroy   # Tear down all resources
npm run build     # Compile TypeScript
npm run test      # Run tests
```

## Cleanup

```bash
npx cdk destroy
```

This removes all deployed resources including the Connect instance, Lex bot, Lambda, S3 buckets, and CloudFront distribution.

## Cost Estimate (Demo Environment)

| Service | Estimated Monthly Cost |
|---------|----------------------|
| Amazon Connect (with Contact Lens) | ~$5 (minimal demo usage) |
| Lex V2 | ~$1 (< 1000 requests) |
| Lambda | ~$0 (free tier) |
| S3 | ~$0.10 |
| CloudFront | ~$0 (free tier) |
| **Total** | **~$6/month** |
