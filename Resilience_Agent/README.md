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

If you don't want to send prompts via the command line, you can run [interactive.py](interactive.py) in a new console window (example: `python interactive.py`). If you run this, it will ask you for the tag/value/RTO/RPO of the workload you're interested in. After delivering an analysis, you can ask follow-up questions about the workload to improve its resilience.

## Next steps

If you want to take your agent to the next level and deploy with Bedrock AgentCore, here's all you need to do.

1. Open `agent.py` and comment out line 39 (`aws_profile = os.getenv("AWS_PROFILE", "default")`) and line 45 (`aws_session = boto3.session.Session(profile_name=aws_profile, region_name=aws_region)`)

2. Uncomment line 46 (`#aws_session = boto3.session.Session(region_name=aws_region)`). Basically, when you run this in AgentCore Runtime, it needs a role, not a local profile. 

3. Open [Dockerfile](Dockerfile) and modify lines 18-20 to align with your environment.

4. Go to your AWS account and create a new ECR repo to store the AgentCore Runtime container in. 

5. Ensure docker is running. Open the CLI in this directory and run:

```
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin XXXXXXXXXXX.dkr.ecr.YYYYYY.amazonaws.com
```

Where `XXXXXXXXXXX` is your AWS account ID and `YYYYYY` is the region you're operating in. 

Then run:

```
docker buildx build --platform linux/arm64 -t XXXXXXXXXXX.dkr.ecr.YYYYYY.amazonaws.com/ZZZZZZZZ:latest --push .
```

Where `ZZZZZZZZ` is the name you gave your ECR repository. This command builds and uploads your container. Note: containers MUST be built as ARM64!

6. Once the container is uploaded, go to ECR and copy the image URI from the artifact listed as `latest`. 

7. Go to AgentCore Runtime and click `Host agent`. Select `ECR container` and specify the URI you just copied. Take note of the role name. Click `Host agent`. 

8. Once the agent runtime has been created, navigate to the created role in the IAM console. 

9. Grant the role the `AWSCloudFormationReadOnlyAccess` managed permission (so it can read your CloudFormation stacks). Create a new inline policy that looks like this:

```
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VisualEditor0",
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject"
            ],
            "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/*"
        },
        {
            "Sid": "VisualEditor1",
            "Effect": "Allow",
            "Action": "s3:ListBucket",
            "Resource": [
                "arn:aws:s3:::YOUR-BUCKET-NAME",
                "arn:aws:s3:::YOUR-BUCKET-NAME/*"
            ]
        }
    ]
}
```

Where `YOUR-BUCKET-NAME` is the name of the bucket you're using to store session information. 

10. Once that's complete, you should be able to run your agent running in AgentCore runtime! Open [invoke-agentcore.py](invoke-agentcore.py) and modify lines 13-15 for your environment. Then call `python agentcore-invoke.py`.