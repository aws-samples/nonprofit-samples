# Resilience Agent

This folder contains a sample resilience agent built using the [Strands Agent SDK](https://strandsagents.com/latest/). This solution was demoed at the 2025 AWS re:Invent in WPS202 - **Chaos & Continuity: Using Gen AI to improve humanitarian workload resilience** and AIM336 - **Using AI to improve humanitarian workload resilience**.

This solution consists of 2 pieces: an AI agent and a very simple chatbot that allow you to interact with the agent.

## Key Features
The solution uses a number of features that might be intersting for someone new to AI agents or Strands to review:
- The Strands SDK
- Control over the LLM and LLM parameters
- The use_aws tool
- Use of a custom tool
- The AWS MCP Documentation tool
- Session management

## How to use it

Install the requirements: `pip install -r requirements.txt`

Open [agent.py](agent.py) and update lines 39-42 so it corresponds to your environment. Note that line 42 is especially important, as this is the name of the S3 bucket where your agent's session data will be stored. 

To use the agent, start `agent.py` in one console (example: `python agent.py`). In another console you can call it directly via commands like this:

```
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{
    "input": {"message": "What is artificial intelligence?", "session-id": "504d629e-8b23-484f-b399-26af086a4f9d"}
  }'
```

If you don't want to send prompts via the command line, you can run `interactive.py` in a new console window (example: `python interactive.py`). If you run this, it will ask you for the tag/value/RTO/RPO of the workload you're interested in. After delivering an analysis, you can ask follow-up questions about the workload to improve its resilience.