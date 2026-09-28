import json
import os
import psycopg2
import logging
import boto3
import uuid
from decimal import Decimal
import time
from datetime import datetime
from botocore.exceptions import ConnectTimeoutError, BotoCoreError, ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))
    
    # Extracting the necessary details from the event
    try:
        agent = event['agent']
        actionGroup = event['actionGroup']
        function = event['function']
        #session_id = event.get('sessiond', str(uuid.uuid4()))
        parameters = event.get('parameters', [])
    except KeyError as e:
        logger.error("Missing key in event: %s", e)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": f"Missing key in event: {e}"})
        }
    
    # Extract the SQL query from parameters
    sql_query = next((param['value'] for param in parameters if param['name'] == 'sql_query'), None)
    if sql_query:
        sql_query = sql_query.rstrip(';')  # Remove trailing semicolon if present
    logger.info("Initial SQL query: %s", sql_query)
    
    if not sql_query:
        logger.error("No SQL query found in parameters")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "No SQL query found in parameters"})
        }
    
    user_question = next((param['value'] for param in parameters if param['name'] == 'user_question'), None)
    if user_question:
        logger.info("User question: %s", user_question)
        
    if not user_question:
        logger.error("User question not found in parameters")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "No User question found in parameters"})
        }
    
    # Fetch PostgreSQL connection details from environment variables
    rds_host = os.environ.get('DB_HOST', 'agentic-architecture-stack-rdsclusterinstance-t2gcpgf8o4x4.coguq9fhaevt.us-east-1.rds.amazonaws.com')
    rds_db = os.environ.get('DB_NAME', 'donations')
    rds_port = os.environ.get('DB_PORT', '5432')
    rds_username = os.environ.get('DB_USER', 'postgres')
    rds_password = os.environ.get('DB_PASSWORD', 'donationsmaster')

    # Fetch Query Correction AgentID from environment variables
    query_correction_agent_id = os.environ.get('QUERY_CORRECTION_AGENT_ID', 'ETR2JS9ZBI')

    # Initialize Bedrock Agent Runtime client
    # bedrock_agent_runtime_client = boto3.client('bedrock-agent-runtime')
    bedrock_agent_runtime_client=boto3.client(
            service_name="bedrock-agent-runtime", region_name='us-east-1'
        )
    logger.info("bedrock_agent_runtime_client: %s", bedrock_agent_runtime_client)

    # Define maximum number of retries to prevent infinite loops
    MAX_RETRIES = 10
    retries = 0

    while retries < MAX_RETRIES:
        try:
            # Establish the connection to the PostgreSQL RDS database
            connection = psycopg2.connect(
                host=rds_host,
                port=rds_port,
                database=rds_db,
                user=rds_username,
                password=rds_password
            )
            cursor = connection.cursor()
            
            logger.info("Executing SQL query: %s", sql_query)
            # Execute the SQL query
            cursor.execute(sql_query)
            
            # Commit the transaction if it's an INSERT, UPDATE, or DELETE statement
            if sql_query.strip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                connection.commit()
            
            # Fetch results if it's a SELECT statement
            if sql_query.strip().upper().startswith("SELECT"):
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                result = [dict(zip(columns, row)) for row in rows]
            else:
                result = {"message": "Query executed successfully"}
            
            logger.info("SQL result: %s", result)
            
            # Close the cursor and connection
            cursor.close()
            connection.close()
            
            response_body = {
                'TEXT': {
                    'body': json.dumps(result, cls=DateTimeEncoder)
                }
            }
            
            # If execution is successful, break out of the loop
            break
        
        except Exception as e:
            logger.error("Error executing query: %s", e, exc_info=True)
            
            # Create the inputText JSON object
            correctionInputText = {
                "SQL Statement": sql_query,
                "User question": user_question,
                "Error Message": str(e)
            }
            # formattedInputString = ', '.join([f"{key}={value}" for key, value in correctionInputText.items()])
            logger.info("correctionInputText: %s", correctionInputText)
            
            try:
                # Wait for 2 seconds
                # time.sleep(2)
                session_id = uuid.uuid4().hex
                logger.info("Generated session_id: %s", session_id)
                
                # Define additional parameters for the agent invocation
                enable_trace = True  # Set as needed
                end_session = True    # Set as needed

                # add query_correction_agent_id in the logger:
                logger.info("Invoke the Bedrock Query correctoin Agent to get a corrected SQL query, agent id is : %s", query_correction_agent_id)
                ###$
                # Invoke the Bedrock Agent to get a corrected SQL query
                agent_response = bedrock_agent_runtime_client.invoke_agent(
                    sessionId=session_id,
                    inputText=json.dumps(correctionInputText),
                    # inputText=correctionInputText,
                    agentId=query_correction_agent_id,
                    agentAliasId='LRLCWPMGGF'
                )
                
                logger.info("Agent response: %s", agent_response)
                
                completion = ""
                for event in agent_response.get("completion"):
                    chunk = event["chunk"]
                    completion = completion + chunk["bytes"].decode()
                ####
                #completion = invoke_agent(bedrock_agent_runtime_client, {
                #    'agentId': query_correction_agent_id,
                #    'agentAliasId': 'LRLCWPMGGF',
                #    'sessionId': session_id,
                #    'inputText': json.dumps(correctionInputText)
                #})
                logger.info("Agent completion: %s", completion)
                corrected_sql = completion
                
                if not corrected_sql:
                    logger.error("Agent did not return a corrected SQL query.")
                    response_body = {
                        'TEXT': {
                            'body': json.dumps({"error": "Agent failed to provide a corrected SQL query."})
                        }
                    }
                    break  # Exit the loop since we cannot proceed without a valid query
                
                logger.info("Agent provided corrected SQL query: %s", corrected_sql)
                sql_query = corrected_sql #.rstrip(';')  # Update sql_query with corrected version
                response_body = {
                    'TEXT': {
                        'body': json.dumps({"corrected_sql": corrected_sql})
                    }
                }
            
            except Exception as agent_error:
                logger.error("Error invoking Bedrock Agent: %s", agent_error, exc_info=True)
                response_body = {
                    'TEXT': {
                        'body': json.dumps({"error": f"Failed to invoke agent: {str(agent_error)}"})
                    }
                }
                break  # Exit the loop since agent invocation failed
            
            retries += 1
            logger.info("Retrying SQL execution (%d/%d)...", retries, MAX_RETRIES)
    
    else:
        # If maximum retries reached without success
        logger.error("Maximum retries reached. Unable to execute SQL query successfully.")
        response_body = {
            'TEXT': {
                'body': json.dumps({"error": "Maximum retries reached. Unable to execute SQL query successfully."})
            }
        }
    
    # Construct the function response
    function_response = {
        'actionGroup': actionGroup,
        'function': function,
        'functionResponse': {
            'responseBody': response_body
        }
    }
    
    session_attributes = event.get('sessionAttributes', {})
    prompt_session_attributes = event.get('promptSessionAttributes', {})
    
    # Construct the action response
    action_response = {
        'messageVersion': '1.0', 
        'response': function_response,
        'sessionAttributes': session_attributes,
        'promptSessionAttributes': prompt_session_attributes
    }
    
    logger.info("Response: %s", action_response)
    
    return action_response

async def invoke_agent_async(bedrock_agent_runtime_client, input_params):
    try:
        response = await bedrock_agent_runtime_client.invoke_agent(
            agentId=input_params['agentId'],
            agentAliasId=input_params['agentAliasId'],
            sessionId=input_params['sessionId'],
            inputText=input_params['inputText']
        )
        
        completion = ""
        # Properly consume the streaming response
        async for chunk_event in response['completion']:
            if 'chunk' in chunk_event:
                chunk = chunk_event['chunk']
                if 'bytes' in chunk:
                    decoded = chunk['bytes'].decode('utf-8')
                    completion += decoded

        return completion
        
    except Exception as e:
        print(f"Error invoking agent: {str(e)}")
        raise

def invoke_agent(bedrock_agent_runtime_client, input_params):
    try:
        response = bedrock_agent_runtime_client.invoke_agent(
            agentId=input_params['agentId'],
            agentAliasId=input_params['agentAliasId'],
            sessionId=input_params['sessionId'],
            inputText=input_params['inputText']
        )
        
        completion = ""
        for event in response.get("completion"):
            chunk = event["chunk"]
            completion = completion + chunk["bytes"].decode()

        return completion
        
    except Exception as e:
        logger.error(f"Error invoking agent: {str(e)}")
        raise

def handle_sql_results(query_results):
    try:
        # Serialize with the custom encoder
        serialized_results = json.dumps(query_results, cls=DateTimeEncoder)
        return json.loads(serialized_results)  # Convert back to dict if needed
    except Exception as e:
        print(f"Error serializing results: {str(e)}")
        raise

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)  # Convert Decimal to string to preserve precision
        return super().default(obj)