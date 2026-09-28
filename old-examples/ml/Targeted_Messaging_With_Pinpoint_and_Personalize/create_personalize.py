import boto3
import time
import datetime
import sys

client = boto3.client('personalize')

stack_name = sys.argv[1]
solutionArn = sys.argv[2]

start = time.time()

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