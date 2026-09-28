import config
import json
import boto3
import logging
import sys
import os
import sample_queries as Samples

# Add services directory to our path so we can import our service scripts
sys.path.append(os.path.join(os.path.dirname(__file__), "services"))
from services import dynamodb, bedrock, athena, metadata

def generate_sql(user_query, id):
    ####################################################
    #### USE RETREIVED METADATA TO GENERATE SQL ####
    ####################################################
    
    schema_details = metadata.get_relevant_metadata(user_query)
    
    details = f"""
    Read database metadata inside the <database_metadata></database_metadata> tags to do the following:
    1. Create a syntactically correct awsathena query to answer the question.
    2. Never query for all the columns from a specific table, only ask for a few relevant columns given the question.
    3. Pay attention to use only the column names that you can see in the schema description. 
    4. Be careful to not query for columns that do not exist.
    5. When using WHERE clauses, be careful not to search for values that do not exist in the column. 
    6. When using WHERE clauses, add the LOWER() function and search for all terms in lowercase. 
    7. If you are writing CTEs then include all the required columns. 
    8. While concatenating a non string column, make sure cast the column to string.
    9. For date columns comparing to string , please cast the string input.
    10. Return the sql query inside the <SQL></SQL> tab.
    
    Refer to the example queries in the <sample_queries></sample_queries> tags for example output.

    """
    
    prompt = f"""\n\n{details}. <database_metadata> {schema_details} </database_metadata> <sample_queries> {Samples.sample_queries} </sample_queries> <question> {user_query} </question>"""

    attempt = 0
    max_attempts = 3
    query = ''

    while attempt < max_attempts:
        # Generate a SQL query and test the quality against athena
        try: 
            config.logger.info(f'Attempt {attempt+1}: Generating SQL')
                        
            # Pass user input to bedrock which generates sql 
            output_message, response = bedrock.call_bedrock(prompt, id)
                        
            # Extract the query out of the model response
            query = response.split('<SQL>')[1].split('</SQL>')[0]
            query = ' '.join(query.split())
            
            config.logger.info(f"Generated Query #{attempt +1}: {query}")
            
            # check the quality of the SQL query
            syntaxcheckmsg=athena.syntax_checker(query)
            
            state = syntaxcheckmsg.get('state')
            output = syntaxcheckmsg.get('output')
            
            config.logger.info(f"Syntax Checker: {syntaxcheckmsg}")
            
            if state =='PASSED':
                config.logger.info(f'Syntax check passed on attempt {attempt+1}')
                return query, output
            else: 
                
                # If the original query failed, augment the prompt to generate new SQL building off the failure reasons of the previous query
                
                prompt += f"""
                This a syntax error from the originally generated SQL: {output}. 
                To correct this, please generate an alternative SQL query which will correct the syntax error.
                The updated query should take care of all the syntax issues encountered.
                Follow the instructions mentioned above to remediate the error. 
                Update the below SQL query to resolve the issue:
                {query}
                Make sure the updated SQL query aligns with the requirements provided in the user's question"""
                
                attempt +=1 
                
        except Exception as e:
            config.logger.error(f"SQL Generation Failed: {str(e)}")
            attempt +=1 
    
    raise Exception("SQL query generation failed after maximum retries. Please try a different question.")


def final_output(user_query, id):
    ######################################################
    #### SHOWCASE THE SQL RESULTS IN NATURAL LANGUAGE ####
    ######################################################
     
    # Generate SQL from the user's question
    final_query, results = generate_sql(user_query, id)

    config.logger.info(f"FINAL GENERATED QUERY: {final_query}")

    prompt = f"""
    You are a helpful assistant providing users with information based on database 
    results. Your goal is to answer questions conversationally, summarizing the data
    clearly and concisely. When possible, display the results in a table using
    markdown syntax, and provide a short summary first. Avoid mentioning that the 
    data comes from a SQL query, and focus on giving direct, natural responses 
    to the user's question. 
    
    Markdown Table Format:
    - Use "|" to separate columns.
    - The first row should contain column headers, followed by a separator line with dashes ("---").
    - Each subsequent row should contain the data, also separated by "|".
    
    For example:
    
    | Column 1 | Column 2 |
    | --- | --- |
    | Data 1 | Data 2 |
    
    If a table format is not possible, return the results as a bulleted list or structured text.
    
    Question: {user_query}
    
    Results: {results}
    """
    
    # Synthesize the SQL results in a natural language response
    output_message, output = bedrock.call_bedrock(prompt, id)
    
    config.logger.info(f"OUTPUT FROM BEDROCK: {output}")
    
    # Write our key conversation history to DynamoDB for future chats to read as context 
    
    messages = [
    {
        "role": "user",
        "content": [
            {"text": user_query}
        ]
    },
    {
        "role": "assistant",
        "content": [
            {"text": json.dumps({
                "sql_query": final_query, 
                "results": output
            })}
        ]
    }
]
    
    dynamodb.write_history_to_dynamodb(messages, id)

    resp_json = {"answer": output, "sql_query": final_query}
    
    return resp_json

def lambda_handler(event, context):
    
    body = event.get('body', {})
    
    # If body is a string (e.g., from API Gateway), parse it
    if isinstance(body, str):
        body = json.loads(body)
    
    # pull the user's message and the generated id from the frontend event
    prompt = body.get('message')
    generated_uuid = body.get('id')
    
    try:
        output = final_output(prompt, generated_uuid)

        # Return the response expected by API Gateway
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',  # Enable CORS
            },
            'body': json.dumps(output)
        }
    except Exception as e:

        config.logger.error(f"Error: {str(e)}")
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',  # Enable CORS
            },
            'body': json.dumps({"answer": str(e), "sql_query": ""})
        }
    