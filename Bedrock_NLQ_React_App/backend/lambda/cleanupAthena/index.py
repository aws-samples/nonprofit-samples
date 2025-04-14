import boto3
import os
import cfnresponse

athena_client = boto3.client("athena")

def lambda_handler(event, context):
    print("Received event:", event)

    workgroup_name = os.getenv("WORKGROUP_NAME")
    
    if event["RequestType"] == "Delete":
        try:
            print(f"Deleting Workgroup: {workgroup_name}")

            # Delete the workgroup with all associated resources
            athena_client.delete_work_group(WorkGroup=workgroup_name, RecursiveDeleteOption=True)

            print(f"Workgroup {workgroup_name} deleted successfully.")

            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        except Exception as e:
            print("Error cleaning Athena workgroup:", str(e))
            cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": str(e)})

    else:
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
