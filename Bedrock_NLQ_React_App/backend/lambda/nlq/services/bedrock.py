import config
import dynamodb

#### HELPER FUNCTION TO CALL BEDROCK
def call_bedrock(prompt, id):
    
    # Get the latest conversation history from DynamoDB
    conversation_history = dynamodb.read_history_from_dynamodb(id)
    
    # Define the system prompts to guide the model's behavior and role.
    system_prompts = [{"text": "You are a helpful assistant. Keep your answers short and succinct."}]

    # payload with model paramters
    message = {"role": "user", "content": [{"text": prompt}]}  
    
    conversation_history.append(message)
    
    # Set the temperature for the model inference, controlling the randomness of the responses.
    temperature = 0.1

    # Set the top_k parameter for the model inference, determining how many of the top predictions to consider.
    top_k = 200

    try:
        # Call the converse method of the Bedrock client object to get a response from the model.
        response = config.bedrock_client.converse(
            modelId = config.MODEL_ID, # Amazon Bedrock model ID loaded in from the environment variables
            messages=conversation_history,
            system=system_prompts,
            inferenceConfig={"temperature": temperature},
            additionalModelRequestFields={"top_k": top_k}
        )
    
        # Extract the output message from the response.
        output_message = response['output']['message']
        
        answer = output_message['content'][0]['text']
        
        config.logger.info(f"BEDROCK OUTPUT: {answer}")
    
    except Exception as e:

        errorMessage = f"An error occurred calling Amazon Bedrock: {str(e)}"
        config.logger.error(errorMessage)
        raise Exception(errorMessage)
        
    return output_message, answer