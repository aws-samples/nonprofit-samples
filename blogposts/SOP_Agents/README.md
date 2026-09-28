# Nonprofit SOP Demo

A simple proof of concept demonstrating how Standard Operating Procedures (SOPs) improve AI agent outputs for nonprofit organizations. Uses AWS Bedrock with the Strands Agents SDK to show side-by-side comparisons.

**Copyright (c) 2026 Amazon Web Services**  
**License**: MIT (see LICENSE file)

## Getting Started

Use this procedure to set up and run the nonprofit SOP demo project. Before you begin, ensure you have Python 3.8 or later installed and AWS credentials configured.

```bash
# Install dependencies
pip install -r requirements.txt

# Configure AWS credentials (choose one method)
aws configure                    # AWS CLI
# OR use IAM role (automatic on AWS infrastructure)
# OR set environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

# Verify credentials
aws sts get-caller-identity

# Enable model access in AWS Bedrock console
# Navigate to: https://console.aws.amazon.com/bedrock
# Select "Model access" and enable Claude Sonnet 4.5

# Run the comparison demo
python demo.py

# OR run the interactive agent
python agent.py
```

## Usage

Use these procedures to run the demo in different modes depending on your needs.

### Interactive Agent (Recommended)

Use this procedure to run the interactive agent that gathers information step-by-step. The interactive agent uses an agentic loop to gather required information and generate thank-you letters.

```bash
python agent.py
```

The agent will:
1. Ask what you'd like to do
2. Identify missing required parameters from the SOP
3. Ask you for the missing information
4. Generate a complete thank-you letter once it has everything

**Example interaction:**
```
You: Write a thank-you letter for a donor

Agent: I need the following information to write the thank-you letter:
- donor_name: Full name of the donor
- donation_amount: Specific dollar amount donated
- donation_date: Date the donation was received
- program_name: Specific program or project supported
- impact_description: How the donation will be used and its impact
- organization_name: Name of the nonprofit organization

You: The donor is Sarah Johnson, she gave $500 on March 15, 2026 to our Youth Literacy Program...

Agent: [Generates complete thank-you letter]
```

### Built-in Scenarios

**Donor thank-you letters (default):**
```bash
python demo.py
```

**Grant proposals:**
```bash
python demo.py --sop grant-proposal
```

### Custom Prompts

Use these procedures to test the demo with your own prompts and scenarios.

**With built-in SOP:**
```bash
python demo.py --prompt "Thank John Smith for his $1000 donation to our education program."

python demo.py --sop grant-proposal --prompt "Write an executive summary for a $25,000 tutoring grant."
```

**With custom SOP file:**
```bash
python demo.py --sop-file sops/my-custom-sop.md --prompt "Your prompt here"
```

### Help

```bash
python demo.py --help
```

## What to Expect

### Interactive Agent (agent.py)

The agent follows the SOP and interactively gathers information:

**Benefits:**
- Automatically identifies missing required parameters
- Asks for all missing information at once
- Follows SOP constraints and quality standards
- Generates complete, professional thank-you letters
- Natural conversation flow

**Example flow:**
1. You: "I need to thank a donor"
2. Agent: "I need the following information: donor_name, donation_amount, ..."
3. You: [Provide the information]
4. Agent: [Generates complete letter following SOP]

### Comparison Demo (demo.py)

The demo runs the same prompt through two agents:

**Without SOP:**
- Generic, inconsistent responses
- May miss key elements
- Unpredictable tone and length

**With SOP:**
- Structured, complete responses
- Includes all required elements
- Consistent tone and appropriate length
- Follows documented procedures

## Project Structure

```
nonprofit-sop-demo/
├── agent.py                     # Interactive agentic loop (~200 lines)
├── demo.py                      # Comparison demo (~200 lines)
├── README.md                    # This file
├── requirements.txt             # Python dependencies
└── sops/                        # SOP documents
   ├── donor-thank-you.sop.md  # Donor communication SOP
   └── grant-proposal.sop.md   # Grant writing SOP
```

## Adding New SOPs

Use this procedure to create and test new Standard Operating Procedures for the demo. Before you begin, review the SOP format specification in the project documentation.

1. Create a markdown file in `sops/` directory with `.sop.md` extension:

```markdown
# [SOP Name]

## Overview
[A concise description of what the SOP does and when to use it]

## Parameters
- **parameter_name** (required): Description of the parameter
- **optional_param** (optional): Description of the optional parameter

## Steps

### 1. [Step Name]

[Natural language description of what happens in this step]

**Constraints:**
- You MUST [specific requirement] because [reason/context]
- You SHOULD [recommended behavior]
- You MAY [optional behavior]

### 2. [Next Step]

[Description]

**Constraints:**
- You MUST NOT [prohibited action] because [reason/context]
- [Additional constraints]

## Examples

### Example 1: [Description]

**Input Parameters:**
- parameter_name: "value"

**Output:**
```
[Example output]
```

### Example 2: Poor Example (What NOT to Do)

**Output:**
```
[Bad example]
```

**Why this fails:**
- [Reason 1]
- [Reason 2]

## Troubleshooting

### [Common Issue]
If [issue description], you should [resolution steps].
```

2. Test it:

```bash
python demo.py --sop-file sops/your-sop.sop.md --prompt "Your test prompt"
```

No code changes needed!


## Troubleshooting

**"Access denied to model"**
- [Enable model access in AWS Bedrock console](https://console.aws.amazon.com/bedrock) (navigate to Model access section)
- Wait a few minutes for access to propagate

**"Unable to locate credentials"**
- Run `aws configure` to set up credentials
- Or verify existing: `aws sts get-caller-identity`

**"Module 'strands' not found"**
- Install dependencies: `pip install -r requirements.txt`

**Rate limiting**
- Wait 1-2 minutes between requests
- The demo makes 2 API calls per run

## License

MIT License - Feel free to use and adapt for your nonprofit!
