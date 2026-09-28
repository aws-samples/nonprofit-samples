#!/usr/bin/env python3
"""Agentic loop for donor thank-you letter generation with SOP guidance.

This agent loads the donor-thank-you SOP and interactively gathers required
parameters before generating a personalized thank-you letter.
"""

import os
import sys
from strands import Agent


def load_sop(filepath):
    """Load SOP file content."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"SOP file not found: {filepath}")
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if not content.strip():
        raise ValueError(f"SOP file is empty: {filepath}")
    
    return content


def create_agent_with_sop(sop_content, model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0"):
    """Create a Strands agent with SOP guidance."""
    system_prompt = f"""You are a helpful assistant for a nonprofit organization helping with donor communications.

You must follow these Standard Operating Procedures:

{sop_content}

Always adhere to the quality standards and procedures outlined in the SOP above.

IMPORTANT: The progress tracking logs, resumability notes, and step completion messages in the SOP are for your internal process only. Do NOT include them in your final output.

CRITICAL INSTRUCTIONS FOR PARAMETER GATHERING:
- The SOP defines required parameters that you need to complete the task
- If you are missing ANY required parameters, you MUST ask the user for them
- You MUST ask for ALL missing parameters in a single prompt (don't ask one at a time)
- Once you have all required parameters, proceed to generate the thank-you letter
- Do NOT make up or assume parameter values - always ask the user

When asking for parameters, format your request clearly:
"I need the following information to write the thank-you letter:
- donor_name: [what you need]
- donation_amount: [what you need]
- etc."
"""
    
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


def run_agentic_loop():
    """Run the agentic loop for donor thank-you letter generation."""
    
    print("\n" + "=" * 80)
    print("NONPROFIT DONOR THANK-YOU AGENT")
    print("=" * 80 + "\n")
    
    # Load SOP
    sop_file = "sops/donor-thank-you.sop.md"
    try:
        print(f"📄 Loading SOP from {sop_file}...")
        sop_content = load_sop(sop_file)
        print(f"✓ SOP loaded ({len(sop_content)} characters)\n")
    except Exception as e:
        print(f"❌ Error loading SOP: {e}\n")
        sys.exit(1)
    
    # Create agent
    try:
        print("🤖 Creating agent with SOP guidance...")
        agent = create_agent_with_sop(sop_content)
        print("✓ Agent created\n")
    except Exception as e:
        print(f"❌ Error creating agent: {e}\n")
        sys.exit(1)
    
    # Initial prompt
    print("=" * 80)
    print("INSTRUCTIONS")
    print("=" * 80)
    print("Tell the agent what you'd like to do. For example:")
    print('  "Write a thank-you letter for a $500 donation from Sarah Johnson"')
    print('  "I need to thank a donor"')
    print("\nThe agent will ask for any missing information it needs.\n")
    print("=" * 80 + "\n")
    
    # Get initial user input
    user_input = input("You: ").strip()
    
    if not user_input:
        print("\n❌ No input provided. Exiting.\n")
        sys.exit(0)
    
    # Agentic loop
    conversation_history = []
    max_turns = 10  # Prevent infinite loops
    turn = 0
    
    while turn < max_turns:
        turn += 1
        
        print(f"\n{'─' * 80}")
        print(f"Turn {turn}")
        print(f"{'─' * 80}\n")
        
        # Build conversation context
        if conversation_history:
            context = "\n\n".join([
                f"{'User' if i % 2 == 0 else 'Assistant'}: {msg}" 
                for i, msg in enumerate(conversation_history)
            ])
            full_prompt = f"{context}\n\nUser: {user_input}"
        else:
            full_prompt = user_input
        
        # Get agent response
        try:
            print("🤖 Agent is thinking...\n")
            
            # Suppress Strands SDK output
            import io
            import contextlib
            
            # Capture stdout to suppress automatic printing
            f = io.StringIO()
            with contextlib.redirect_stdout(f):
                result = agent(full_prompt)
            
            # Extract text from AgentResult
            response = str(result)
        except Exception as e:
            print(f"❌ Error getting agent response: {e}\n")
            sys.exit(1)
        
        # Add to conversation history
        conversation_history.append(user_input)
        conversation_history.append(response)
        
        # Check if the agent is asking for more information
        # Simple heuristic: if response contains a question mark or asks for information
        response_lower = response.lower()
        asking_for_info = (
            "?" in response or
            "need" in response_lower or
            "provide" in response_lower or
            "missing" in response_lower or
            "information" in response_lower
        )
        
        # Check if response looks like a complete letter
        # Simple heuristic: contains "Dear" and is reasonably long
        looks_complete = (
            "Dear" in response and
            len(response) > 200 and
            ("Sincerely" in response or "gratitude" in response_lower or "With" in response or "Happy reading" in response)
        )
        
        if looks_complete:
            # Display the final letter cleanly
            print("\n" + "=" * 80)
            print("✅ THANK-YOU LETTER GENERATED")
            print("=" * 80)
            print(f"\n{response}\n")
            print("=" * 80)
            print("\nThe letter is complete and ready to use!")
            print("=" * 80 + "\n")
            break
        else:
            # Show intermediate response
            print(f"Agent: {response}\n")
        
        
        if asking_for_info:
            # Agent needs more information
            print("─" * 80)
            user_input = input("You: ").strip()
            
            if not user_input:
                print("\n❌ No input provided. Exiting.\n")
                break
        else:
            # Agent provided a response but it's not clear if it's done
            print("─" * 80)
            print("Would you like to:")
            print("  1. Provide more information")
            print("  2. Exit")
            choice = input("\nChoice (1/2): ").strip()
            
            if choice == "1":
                user_input = input("\nYou: ").strip()
                if not user_input:
                    print("\n❌ No input provided. Exiting.\n")
                    break
            else:
                print("\n✅ Exiting agent loop.\n")
                break
    
    if turn >= max_turns:
        print(f"\n⚠️  Reached maximum turns ({max_turns}). Exiting.\n")


def main():
    """Main entry point."""
    try:
        run_agentic_loop()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user. Exiting.\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
