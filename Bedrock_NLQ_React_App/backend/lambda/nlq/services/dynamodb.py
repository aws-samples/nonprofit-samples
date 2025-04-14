import config
import time
import boto3
import json

################ DYNAMO DB CONVO HISTORY ################

def write_history_to_dynamodb(history, id):
    # Take in message from conversation history
    # Augment with session id and the timestamp
    # Write to DynamoDB
    
    timestamp = str(time.time())
    
    # Add an index to differentiate messages written at the same time (avoid overwrites)
    for idx, item in enumerate(history):
        
        dynamodb_item = {
            "id": id,
            "timestamp": f"{timestamp}_{idx}",
            "message": item  
        }
        
        config.dynamodb_table.put_item(Item=dynamodb_item)
        
        config.logger.info(f"\n\nItem with ID {id} and timestamp {timestamp} written to table {config.dynamodb_table}\n\n")

def read_history_from_dynamodb(id):
    
    return_items = []

    # Retrieve all the conversation history for the current session ID
    response = config.dynamodb_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('id').eq(id),
    )
    
    items = response.get('Items', []) # get the items from the response, or an empty list if none
    
    for item in items:
        message_data = item.get("message", "{}")
        
        # Add message to our items list
        # The final list will contain the full session conversation history
        return_items.append(message_data)
        
    return return_items