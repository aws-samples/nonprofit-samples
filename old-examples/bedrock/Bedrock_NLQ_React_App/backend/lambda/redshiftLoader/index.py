import boto3
import os
import cfnresponse
import time

def lambda_handler(event, context):
  client = boto3.client('redshift-data')
  print("EVENT DATA: ", event)
  request = event.get('RequestType')
  print("REQUEST TYPE: ", request)
  response_data = {}
  sql_statements = [
      "CREATE TABLE DonationFact (DonationKey BIGINT PRIMARY KEY, DonorKey INTEGER NOT NULL, CampaignKey INTEGER NOT NULL, EventKey INTEGER, DateKey INTEGER NOT NULL, DonationAmount DECIMAL(10,2) NOT NULL, PaymentMethodKey INTEGER NOT NULL) DISTKEY(DateKey) SORTKEY(DateKey, CampaignKey);",
      "CREATE TABLE DonorDim (DonorKey INTEGER PRIMARY KEY, DonorID VARCHAR(10) NOT NULL, FirstName VARCHAR(50) NOT NULL, LastName VARCHAR(50) NOT NULL, Email VARCHAR(100), Phone VARCHAR(20), Address VARCHAR(200), JoinDate VARCHAR(100) NOT NULL, DonorStatus VARCHAR(100) NOT NULL) DISTSTYLE ALL;",
      "CREATE TABLE CampaignDim (CampaignKey INTEGER PRIMARY KEY, CampaignID VARCHAR(10) NOT NULL, CampaignName VARCHAR(100) NOT NULL, StartDate VARCHAR(100) NOT NULL, EndDate VARCHAR(100) NOT NULL, CampaignType VARCHAR(200) NOT NULL, TargetAmount DECIMAL(12,2) NOT NULL) DISTSTYLE ALL;",
      "CREATE TABLE EventDim (EventKey INTEGER PRIMARY KEY, EventID VARCHAR(10) NOT NULL, EventName VARCHAR(100) NOT NULL, EventDate VARCHAR(100) NOT NULL, EventLocation VARCHAR(100) NOT NULL, EventType VARCHAR(200) NOT NULL) DISTSTYLE ALL;",
      "CREATE TABLE DateDim (DateKey INTEGER PRIMARY KEY, Date VARCHAR(100) NOT NULL, Day INTEGER NOT NULL, Month INTEGER NOT NULL, Year INTEGER NOT NULL, Quarter INTEGER NOT NULL, IsHoliday BOOLEAN NOT NULL) DISTSTYLE ALL SORTKEY(Date);",
      "CREATE TABLE PaymentMethodDim (PaymentMethodKey INTEGER PRIMARY KEY, PaymentMethodName VARCHAR(50) NOT NULL) DISTSTYLE ALL;",
      f"COPY DonationFact FROM 's3://{os.environ['BUCKET_NAME']}/donations/DonationFact.csv' IAM_ROLE '{os.environ['IAM_ROLE']}' CSV IGNOREHEADER 1;",
      f"COPY DonorDim FROM 's3://{os.environ['BUCKET_NAME']}/donors/DonorDim.csv' IAM_ROLE '{os.environ['IAM_ROLE']}' CSV IGNOREHEADER 1;",
      f"COPY CampaignDim FROM 's3://{os.environ['BUCKET_NAME']}/campaigns/CampaignDim.csv' IAM_ROLE '{os.environ['IAM_ROLE']}' CSV IGNOREHEADER 1;",
      f"COPY EventDim FROM 's3://{os.environ['BUCKET_NAME']}/events/EventDim.csv' IAM_ROLE '{os.environ['IAM_ROLE']}' CSV IGNOREHEADER 1;",
      f"COPY DateDim FROM 's3://{os.environ['BUCKET_NAME']}/date/DateDim.csv' IAM_ROLE '{os.environ['IAM_ROLE']}' CSV IGNOREHEADER 1;",
      f"COPY PaymentMethodDim FROM 's3://{os.environ['BUCKET_NAME']}/payment/PaymentMethodDim.csv' IAM_ROLE '{os.environ['IAM_ROLE']}' CSV IGNOREHEADER 1;"
  ]

  def wait_for_statement_to_finish(statement_id):
      while True:
          status_response = client.describe_statement(Id=statement_id)
          status = status_response['Status']
          print(f"Statement {statement_id} status: {status}")
          if status in ['FINISHED', 'FAILED', 'ABORTED']:
              return status
          time.sleep(2)  # Wait before polling again

  if request in ['Create', 'Update']:
      try:
          for statement in sql_statements:
              print("SQL STATEMENT: ", statement)
              response = client.execute_statement(
                  WorkgroupName=os.environ['WORKGROUP_NAME'],
                  Database=os.environ['DATABASE_NAME'],
                  Sql=statement
              )
              statement_id = response['Id']

              # Wait for the statement to finish
              status = wait_for_statement_to_finish(statement_id)

              if status != 'FINISHED':
                  raise Exception(f"SQL statement failed with status: {status}")

          response_data = {'Message': 'Tables created and data loaded successfully'}

          cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data)

          return {'statusCode': 200, 'body': 'Tables created and data loaded successfully'}

      except Exception as e:
          print(str(e))
          response_data = {'Error': str(e)}
          cfnresponse.send(event, context, cfnresponse.FAILED, response_data)

          return {'statusCode': 500, 'body': f'Error: {str(e)}'}
  else:
      cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data)