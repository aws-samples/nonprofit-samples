import json
import boto3
import logging
import uuid
import os
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Bedrock Agent Runtime client
bedrock_agent_runtime_client = boto3.client('bedrock-agent-runtime')

def lambda_handler(event, context):
    try:
        agent = event['agent']
        actionGroup = event['actionGroup']
        function = event['function']
        parameters = event.get('parameters', [])
    except KeyError as e:
        logger.error("Missing key in event: %s", e)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": f"Missing key in event: {e}"})
        }
    agent_id = os.environ.get('AGENT_ID', 'DFQWOIRKJW')
    agent_alias_id = os.environ.get('AGENT_ALIAS_ID', 'TSTALIASID')
    try:
        input_text = next((param['value'] for param in parameters if param['name'] == 'input_text'), None)
        # Generate a unique session ID
        session_id = uuid.uuid4().hex
        logger.info(f"Generated session ID: {session_id}")

        # Invoke the Bedrock Agent
        logger.info("Attempting to invoke Bedrock agent...")
        agent_response = bedrock_agent_runtime_client.invoke_agent(
            sessionId=session_id,
            inputText=input_text,
            agentId=agent_id,
            agentAliasId=agent_alias_id
        )
        
        logger.info(f"Agent response: {json.dumps(agent_response, default=str)}")
        
        completion = ""
        for event in agent_response.get("completion"):
            chunk = event["chunk"]
            completion = completion + chunk["bytes"].decode()
        
        logger.info(f"Agent ccompletion: {completion}")
        
        session_attributes = event.get('sessionAttributes', {})
        prompt_session_attributes = event.get('promptSessionAttributes', {})
        
        function_response = {
            'actionGroup': actionGroup,
            'function': function,
            'functionResponse': {
                'responseBody': {
                    'TEXT': {
                        'body': json.dumps(completion)
                    }
                }
            }
        }
    
        # Process the response
        response_body = {
            'messageVersion': '1.0', 
            'response': function_response,
            'sessionAttributes': session_attributes,
            'promptSessionAttributes': prompt_session_attributes
        }

    except ClientError as e:
        logger.error(f"ClientError when invoking Bedrock agent: {e}")
        function_response = {
            'actionGroup': actionGroup,
            'function': function,
            'functionResponse': {
                'responseBody': {
                    'TEXT': {
                        'body': 'Failed to invoke Bedrock Agent'
                    }
                }
            }
        }
        response_body = {
            'messageVersion': '1.0', 
            'response': function_response,
            'sessionAttributes': session_attributes,
            'promptSessionAttributes': prompt_session_attributes
        }

    except Exception as e:
        function_response = {
            'actionGroup': actionGroup,
            'function': function,
            'functionResponse': {
                 'responseBody': {
                    'TEXT': {
                        'body': 'An unexpected error occurred'
                    }
                    
                }
            }
        }
        logger.error(f"Unexpected error: {e}")
        response_body = {
            'messageVersion': '1.0', 
            'response': function_response,
            'sessionAttributes': session_attributes,
            'promptSessionAttributes': prompt_session_attributes
        }
    
    logger.info(f"response_body: {response_body}")
    
    return response_body