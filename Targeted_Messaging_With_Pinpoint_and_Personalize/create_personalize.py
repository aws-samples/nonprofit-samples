import boto3
import time
import datetime
import sys

client = boto3.client('personalize')

stack_name = sys.argv[1]
bucket_name = sys.argv[2]
roleArn = sys.argv[3]


start = time.time()

###################################################################################
#Create dataset group
print('Creating dataset group')
response = client.create_dataset_group(
    name=stack_name+'_dataset'
)

datasetGroupArn = response['datasetGroupArn']

response = client.describe_dataset_group(
    datasetGroupArn=datasetGroupArn
)

while response['datasetGroup']['status'] != "ACTIVE":
    #print(response['datasetGroup']['status'])
    time.sleep(10)
    response = client.describe_dataset_group(
        datasetGroupArn=datasetGroupArn
)

###################################################################################
#Create schema
print('Creating schema')
response = client.create_schema(
    name=stack_name + '_UserInteractionsSchema',
    schema='{"type": "record","name": "Interactions","namespace": "com.amazonaws.personalize.schema","fields": [{"name": "USER_ID","type": "string"},{"name": "ITEM_ID","type": "string"},{"name": "TIMESTAMP","type": "long"}],"version": "1.0"}'
)

schemaArn = response['schemaArn']
#print('schemaArn: {}'.format(schemaArn))

###################################################################################
#Create dataset 
print('Creating dataset')
response = client.create_dataset(
    name=stack_name + '_UserInteractions',
    schemaArn=schemaArn,
    datasetGroupArn=datasetGroupArn,
    datasetType='INTERACTIONS'
)

datasetArn = response['datasetArn']
#print('datasetArn: {}'.format(datasetArn))

response = client.describe_dataset(datasetArn=datasetArn)

while response['dataset']['status'] != "ACTIVE":
    #print(response['dataset']['status'])
    time.sleep(30)
    response = client.describe_dataset(datasetArn=datasetArn)


###################################################################################
#Create dataset import job
print('Importing dataset')
response = client.create_dataset_import_job(
    jobName=stack_name+'_import',
    datasetArn=datasetArn,
    dataSource={
        'dataLocation': 's3://'+ bucket_name +'/npo/UserInteractions.csv'
    },
    roleArn=roleArn
)

datasetImportJobArn = response['datasetImportJobArn']

response = client.describe_dataset_import_job(datasetImportJobArn=datasetImportJobArn)
while response['datasetImportJob']['status'] != "ACTIVE":
#    print(response['datasetImportJob']['status'])
    time.sleep(30)
    response = client.describe_dataset_import_job(datasetImportJobArn=datasetImportJobArn)


###################################################################################
#Create solution
print('Creating solution')
response = client.create_solution(name=stack_name+'_solution', performAutoML=True, datasetGroupArn=datasetGroupArn)

solutionArn = response['solutionArn']
#print("solutionArn: {}".format(solutionArn))
response = client.describe_solution(solutionArn=solutionArn)
while response['solution']['status'] != "ACTIVE":
#    print(response['solution']['status'])
    time.sleep(30)
    response = client.describe_solution(solutionArn=solutionArn)


###################################################################################
#Create solution version
print('Creating solution version')
response = client.create_solution_version(solutionArn=solutionArn, trainingMode='FULL')
solutionVersionArn = response['solutionVersionArn']
#print("solutionVersionArn: {}".format(solutionVersionArn))

response = client.describe_solution_version(solutionVersionArn=solutionVersionArn)
while response['solutionVersion']['status'] != "ACTIVE": 
#    print(response['solutionVersion']['status'])
    time.sleep(30)
    response = client.describe_solution_version(solutionVersionArn=solutionVersionArn)

###################################################################################
#Create campaign
print('Creating campaign')
response = client.create_campaign(name=stack_name+'_campaign', solutionVersionArn=solutionVersionArn, minProvisionedTPS=1)
campaignArn = response['campaignArn']

#print('campaignArn: {}'.format(campaignArn))

response = client.describe_campaign(campaignArn=campaignArn)
while response['campaign']['status'] != "ACTIVE": 
#    print(response['campaign']['status'])
    time.sleep(30)
    response = client.describe_campaign(campaignArn=campaignArn)


end = time.time()

elapsedSeconds = end - start 
print("Elapsed time: {}".format(datetime.timedelta(seconds=elapsedSeconds)))