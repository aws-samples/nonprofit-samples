# This code provides a sample solution for demonstration purposes. Organizations should implement security best practices, 
# including controls for authentication, data protection, and prompt injection in any production workloads. Please reference 
# the security pillar of the AWS Well-Architected Framework and Amazon Bedrock documentation for more information.

import requests
import uuid
from rich.console import Console 
from rich.markdown import Markdown
from colorama import Fore

# Function to clear the screen
def clear_scren():
    print("\033[H\033[J")

def main():
    print("🔧 AWS Resilience Advisor - Interactive Mode")
    print("=" * 50)
    print("Type 'quit' or 'exit' to end the session.\n")

    first_execution = True
    payload = {}

    session_id = str(uuid.uuid4())

    console = Console()

    while True:
        try:
            #The user must enter the tag name and tag value of their workload 

            if first_execution:
                tag_name = input("Enter the tag name of your workload: ").strip()
                tag_value = input("Enter the tag value of your workload: ").strip()
                rto = input("Enter the RTO of your workload: ").strip()
                rpo = input("Enter the RPO of your workload: ").strip()
            
                if tag_name and tag_value and rto and rpo:
                    payload = {
                        "input": {
                            "tag-name": tag_name,
                            "tag-value": tag_value,
                            "RTO": rto,
                            "RPO": rpo,
                            "session-id": session_id
                        }
                    }
                    first_execution = False
            else: 
                user_input = input(Fore.RESET + "You: ").strip()
                payload = {"input": { "message": user_input, "session-id": session_id}}
            
                if user_input.lower() in ['clear']:
                    clear_scren()
                    continue

                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("Goodbye!")
                    break
                    
                if not user_input:
                    continue
            
                    
            response = requests.post(
                "http://localhost:8080/invocations",
                json=payload,
                timeout=300
            )

            if response.status_code == 200:
                result = response.json()
                clear_scren()
                markdown = Markdown(result['output']['message']['content'][0]['text'])
                console.print(markdown)
            else:
                print(Fore.RED + f"\nError: {response.status_code} - {response.text}\n")
                    
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            print("Please try again.\n")

if __name__ == "__main__":
    main()