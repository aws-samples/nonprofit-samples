import config
import boto3
import os
import json
import time
from botocore.config import Config


def lambda_handler(event, context):

    # Define CORS headers
    headers = {
        'Access-Control-Allow-Origin': '*',  # Allow requests from any origin
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
    }
    
    # Handle preflight OPTIONS request
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({})
        }
    
    try:
        # Extract the body from the event if it exists (API Gateway integration)
        if 'body' in event and event['body']:
            if isinstance(event['body'], str):
                body = json.loads(event['body'])
            else:
                body = event['body']
            
            user_prompt = body.get('message')
            session_id = body.get('kb_session_id')
            
            config.logger.info(f'User prompt: {user_prompt}')
            config.logger.info(f'Session: {session_id}')

        else:
            # Direct Lambda invocation
            user_prompt = event.get('message')
            session_id = body.get('kb_session_id')
            
            config.logger.info(f'User prompt: {user_prompt}')
            config.logger.info(f'Session: {session_id}')
        
        if not user_prompt:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'Missing required parameter: user_prompt'})
            }
        
        # Invoke the model and get the response
        response_output, sql_query, response_session_id = invoke_model(user_prompt, session_id)
        
        config.logger.info(f'Output: {response_output}')
        config.logger.info(f'SQL: {sql_query}')
        config.logger.info(f'Session: {response_session_id}')
        
        # Write the exchange to DynamoDB for historical analysis
        write_history_to_dynamodb(user_prompt, response_output, sql_query, response_session_id)
        
        # Return the response with CORS headers
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({
                'answer': response_output,
                'sql_query': sql_query,
                'kb_session_id': response_session_id
            })
        }
    
    except Exception as e:
        # Handle any errors with CORS headers
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': str(e)})
        }

def invoke_model(user_prompt, session_id):

    retrieve_and_generate_args = {
        'input': {
            'text': user_prompt,
        },
        'retrieveAndGenerateConfiguration': {
            'type': 'KNOWLEDGE_BASE',
            'knowledgeBaseConfiguration': {
                'knowledgeBaseId': config.KNOWLEDGE_BASE_ID,
                'modelArn': config.MODEL_ID,
            }
        }
    }

    # Include session_id in the arguments when the session_id has been retrieved after the first turn
    if session_id:
        retrieve_and_generate_args['sessionId'] = session_id

    try:
        # Pass our arguments to the bedrock knowledge bases retrieve and generate request
        response = config.agent_client.retrieve_and_generate(**retrieve_and_generate_args)
    
        response_output = response['output']['text']
        sql_sample = response["citations"][0]["retrievedReferences"][0]["location"]["sqlLocation"]["query"]
        response_session_id = response.get('sessionId')
    
        return response_output, sql_sample, response_session_id
        
    except Exception as e:
        config.logger.error(f"Knowledge Base query failed: {str(e)}")
        

def write_history_to_dynamodb(user_prompt, response_output, sql_query, response_session_id):
    # Take in message from conversation history
    # Augment with session id and the timestamp
    # Write to DynamoDB
    
    timestamp = str(time.time())
    
    dynamodb_item = {
        "id": response_session_id,
        "timestamp": timestamp,
        "question": user_prompt,
        "response": response_output,
        "sql_query": sql_query
    }
    
    try:
        config.dynamodb_table.put_item(Item=dynamodb_item)
        config.logger.info(f"Chat history written to DynamoDB successfully.")

    except Exception as e:
        config.logger.error(f"Failed to write chat history to DynamoDB: {str(e)}")
        