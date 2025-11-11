# This code provides a sample solution for demonstration purposes. Organizations should implement security best practices, 
# including controls for authentication, data protection, and prompt injection in any production workloads. Please reference 
# the security pillar of the AWS Well-Architected Framework and Amazon Bedrock documentation for more information.

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
from datetime import datetime, timezone
from strands import Agent, tool
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient
from strands.session.s3_session_manager import S3SessionManager
from strands.models import BedrockModel
from strands_tools import use_aws
import logging
from pathlib import Path
import uvicorn
import os
import boto3.session

class InvocationRequest(BaseModel): input: Dict[str, Any]
class InvocationResponse(BaseModel): output: Dict[str, Any]

# Configure the root strands logger
loggingLevel = logging.INFO

logging.getLogger("strands").setLevel(loggingLevel)
logging.getLogger().setLevel(loggingLevel)

# Add a handler to see the logs
logging.basicConfig(
    format="%(levelname)s | %(name)s | %(message)s", 
    handlers=[logging.StreamHandler()]
)

app = FastAPI(title="Resilience Advisor", version="1.0.0")

# Set configs from environment variables or defaults
aws_profile = os.getenv("AWS_PROFILE", "default")
aws_region = os.getenv("AWS_REGION", "us-east-1")
llm_model_id = os.getenv("LLM_MODEL_ID", "us.anthropic.claude-3-7-sonnet-20250219-v1:0")
bucket_name = os.getenv("S3_BUCKET", "YOUR-S3-BUCKET-GOES-HERE")

# Configure boto3 to use the specified profile for all AWS calls
aws_session = boto3.session.Session(profile_name=aws_profile, region_name=aws_region)
#aws_session = boto3.session.Session(region_name=aws_region)

#Read in the system_prompt.md file as the system prompt
current_file = Path(__file__)
prompt_file = current_file.parent / "system_prompt.md"

with open(prompt_file, 'r', encoding='utf-8') as f:
    SYSTEM_PROMPT = f.read()

bedrock_model = BedrockModel(
    model_id=llm_model_id,
    temperature=0.1,
    boto_session=aws_session
)

#Use the AWS Documentation MCP server
aws_doc_mcp_client = MCPClient(lambda: streamablehttp_client("https://knowledge-mcp.global.api.aws"))

@tool 
def calculate_letter_grade(critical_vulns: int, high_vulns: int, medium_vulns: int, low_vulns: int) -> str:
    """Calculates a letter grade based on the number of resilience issues in the workload. A workload with a low RTO/RPO will have more resilience issues than the same workload with a high RTO/RPO.

    Args: 
        critical_vulns: the number of critical resilience issues in the workload
        high_vulns: the number of high severity resilience issues in the workload 
        medium_vulns: the number of medium severity resilience issues in the workload 
        low_vulns: the number of low severity resilience issues in the workload
    """
    
    if critical_vulns > 0:
        return "D"
    elif high_vulns > 0:
        return "C"
    elif medium_vulns > 0:
        return "B"
    else:
        return "A"

@app.post("/invocations", response_model=InvocationResponse)
async def invoke_agent(request: InvocationRequest):

    prompt = {}
    response = {}

    #Expecting the request to look like this: 
    #   {
    #       "input": { "tag-name": "value" , "tag-value", "value", "RTO": "value", "RPO": "value" }
    #   }

    try:
        #Pull out the tag value: 
        tag_name = request.input.get("tag-name", "")
        tag_value = request.input.get("tag-value", "")
        
        #Pull out the RTO and RPO values:
        rto = request.input.get("RTO", "")
        rpo = request.input.get("RPO", "")

        if tag_name and tag_value and rto and rpo:
            prompt = f"""
            The user workload is running in AWS and defined by the tag '{tag_name}' with tag value of '{tag_value}'. 
            This workload has RTO of '{rto}' and RPO of '{rpo}'.
            """
        else:
            prompt = request.input.get("message", "")
        logging.info(f"Prompt: {prompt}")


        # Create a session manager with a unique session ID
        session_id = request.input.get("session-id", "")
        if not session_id:
            raise HTTPException(
                status_code=400,
                detail="No session-id found in input. Please provide a 'session-id' key in the input."
            )
        logging.info(f"SessionId: {session_id}")
        session_manager = S3SessionManager(
            session_id=session_id, 
            bucket=bucket_name, 
            prefix="prod"
        )

        with aws_doc_mcp_client:
            aws_doc_tools = aws_doc_mcp_client.list_tools_sync()
            # Get or create agent instance for this session
            agent = Agent(model=bedrock_model,
                            system_prompt=SYSTEM_PROMPT,
                            tools=[use_aws, calculate_letter_grade, aws_doc_tools],
                            session_manager=session_manager)

            result = agent(prompt)
            response = {
                "message": result.message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": "strands-agent",
            }

        return InvocationResponse(output=response)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent processing failed: {str(e)}")

@app.get("/ping")
async def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)