#!/usr/bin/env python3
"""Nonprofit SOP Demo - Simple proof of concept showing how SOPs improve AI outputs.

This demo compares agent responses with and without Standard Operating Procedure guidance.

Copyright (c) 2026 Amazon Web Services
SPDX-License-Identifier: MIT
"""

import argparse
import sys
import os
from strands import Agent


# Built-in SOP scenarios with default prompts
SCENARIOS = {
    "donor-thank-you": {
        "sop_file": "sops/donor-thank-you.sop.md",
        "default_prompt": """Write a thank-you letter to Mary Major who donated $450 to our youth literacy program. Mary donated the money on December 31, 2025. For every $10 donation made, we're able to put 2 books into the hands of young readers. We plan to use the money to support our Joy of Reading program, which is one of the main programs we run at AnyCompany Nonprofit. As a next step, we'd like to invite the sponsor keep up to date with us through our newsletter.""",
        "context": "donor communications"
    },
    "grant-proposal": {
        "sop_file": "sops/grant-proposal.sop.md",
        "default_prompt": """Write an executive summary for a grant proposal. We're requesting $45,000 for a summer meals program that will provide free breakfast and lunch to 200 children ages 5-12 in the Riverside neighborhood. The program will run 5 days a week for 10 weeks during summer break. Currently 25% of children in Riverside (approximately 25,000 children) lack access to nutritious meals during the Summer. We're finding that children enrolled in Summer programs have lower average test scores than students during Fall and Winter periods. We find these students reading comprehension scores have dropped an average of 20% during summer months. We find these scores begin to rise several months after school has started up in the fall. We suspect food insecurity is a culprit in this problem. Other communities nearby don't have a food insecurity problem and don't see drops in reading comprehension among their students during summer programs. 
        
        We would like funding to support our Riverside Meals Program from June 2026 through August 2026.
        
        Our nonprofit is called AnyCompany Nonprofit and we've been operating for 10 years now. We currently run an after school food program called Riverside Snacks, where we support nearly 10,000 low income students who need additional food support. In this new program, we expect to serve 1,000 meals a day and we expect that we will not experience the test score drop across reading comprehension that we have historically seen during Summer programs. We hope this program will make a difference for 1,000 low income students.""",
        "context": "grant writing"
    }
}


def load_sop(filepath):
    """Load SOP file content."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"SOP file not found: {filepath}")
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if not content.strip():
        raise ValueError(f"SOP file is empty: {filepath}")
    
    return content


def create_agent(system_prompt, model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0"):
    """Create a Strands agent with the given system prompt."""
    try:
        return Agent(model=model_id, system_prompt=system_prompt)
    except Exception as e:
        error_str = str(e)
        if "AccessDeniedException" in error_str or "access denied" in error_str.lower():
            print(f"\n❌ Model access denied. Enable model access in Bedrock console:")
            print("   https://console.aws.amazon.com/bedrock → Model access\n")
        elif "credentials" in error_str.lower():
            print(f"\n❌ AWS credentials not configured. Run 'aws configure' or use IAM role.\n")
        raise


def run_comparison(prompt, agent_no_sop, agent_with_sop):
    """Run the same prompt through both agents."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    
    return {
        'no_sop': agent_no_sop(prompt),
        'with_sop': agent_with_sop(prompt)
    }


def display_comparison(results, prompt, scenario_name=None):
    """Display side-by-side comparison of agent responses."""
    print("\n" + "=" * 80)
    print("NONPROFIT SOP DEMO - COMPARISON RESULTS")
    if scenario_name:
        print(f"Scenario: {scenario_name}")
    print("=" * 80)
    
    print("\n📝 PROMPT:")
    print("-" * 80)
    print(prompt)
    
    print("\n" + "=" * 80)
    print("❌ WITHOUT SOP GUIDANCE")
    print("=" * 80)
    print(results['no_sop'])
    
    print("\n" + "=" * 80)
    print("✅ WITH SOP GUIDANCE")
    print("=" * 80)
    print(results['with_sop'])


def main():
    parser = argparse.ArgumentParser(
        description="Demonstrate how SOPs improve AI agent outputs for nonprofits",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run default donor thank-you scenario
  python demo.py
  
  # Run grant proposal scenario
  python demo.py --sop grant-proposal
  
  # Custom prompt with donor thank-you SOP
  python demo.py --prompt "Thank John Smith for his $1000 donation."
  
  # Use custom SOP file
  python demo.py --sop-file sops/my-custom-sop.md --prompt "Your prompt here"
        """
    )
    
    parser.add_argument(
        "--sop",
        choices=list(SCENARIOS.keys()),
        default="donor-thank-you",
        help="Built-in SOP scenario to use (default: donor-thank-you)"
    )
    
    parser.add_argument(
        "--sop-file",
        type=str,
        help="Path to custom SOP file (overrides --sop)"
    )
    
    parser.add_argument(
        "--prompt",
        type=str,
        help="Custom prompt to test (uses scenario default if not provided)"
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        help="Bedrock model ID to use"
    )
    
    args = parser.parse_args()
    
    # Determine SOP file and prompt
    if args.sop_file:
        sop_file = args.sop_file
        prompt = args.prompt or "Please help with this task."
        context = "various tasks"
        scenario_name = os.path.basename(args.sop_file)
    else:
        scenario = SCENARIOS[args.sop]
        sop_file = scenario["sop_file"]
        prompt = args.prompt or scenario["default_prompt"]
        context = scenario["context"]
        scenario_name = args.sop
    
    print("\n" + "=" * 80)
    print("NONPROFIT SOP DEMO - Initializing...")
    print("=" * 80 + "\n")
    
    # Load SOP
    try:
        print(f"📄 Loading SOP from {sop_file}...")
        sop_content = load_sop(sop_file)
        print(f"✓ SOP loaded ({len(sop_content)} characters)\n")
    except Exception as e:
        print(f"❌ Error loading SOP: {e}\n")
        sys.exit(1)
    
    # Create agents
    try:
        print("🤖 Creating agents...")
        
        agent_no_sop = create_agent(
            f"You are a helpful assistant for a nonprofit organization. You help with {context}."
        )
        
        agent_with_sop = create_agent(
            f"""You are a helpful assistant for a nonprofit organization. You help with {context}.

You must follow these Standard Operating Procedures:

{sop_content}

Always adhere to the quality standards and procedures outlined in the SOP above.

IMPORTANT: The progress tracking logs, resumability notes, and step completion messages in the SOP are for your internal process only. Do NOT include them in your final output. Only provide the final letter or document requested by the user."""
        )
        
        print("✓ Agents created\n")
        
    except Exception as e:
        print(f"❌ Error creating agents: {e}\n")
        sys.exit(1)
    
    # Run comparison
    print("🔄 Running comparison...")
    print(f"   Prompt: {prompt[:60]}{'...' if len(prompt) > 60 else ''}\n")
    
    try:
        results = run_comparison(prompt, agent_no_sop, agent_with_sop)
        display_comparison(results, prompt, scenario_name)
        print("✅ Demo completed successfully!\n")
        
    except Exception as e:
        print(f"❌ Error during comparison: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
