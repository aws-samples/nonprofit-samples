import os
import boto3
import json
import cfnresponse  # AWS CloudFormation response module

# Initialize Glue client
glue_client = boto3.client("glue")

def lambda_handler(event, context):
    print("Received event:", json.dumps(event))
    
    # Extract required parameters
    request_type = event.get("RequestType")
    response_data = {}

    try:
        crawler_name = os.getenv("CRAWLER_NAME")
        print(f"Starting Glue Crawler: {crawler_name}")

        if request_type in ["Create", "Update"]:
            # Start the Glue Crawler
            glue_client.start_crawler(Name=crawler_name)
            print(f"Glue Crawler {crawler_name} started successfully.")
            response_data["Message"] = f"Glue Crawler {crawler_name} started successfully."

        elif request_type == "Delete":
            # CloudFormation requires a response even on Delete, even if no specific delete action is required
            print("Delete request received. No action needed for Glue Crawler.")

        # Send SUCCESS response to CloudFormation
        cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data)

    except Exception as e:
        print("Error starting Glue Crawler:", str(e))
        response_data["Message"] = "Error starting Glue Crawler"
        response_data["Error"] = str(e)

        # Send FAILURE response to CloudFormation
        cfnresponse.send(event, context, cfnresponse.FAILED, response_data)
