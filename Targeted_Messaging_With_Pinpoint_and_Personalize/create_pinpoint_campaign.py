import boto3
import sys 
import time

client = boto3.client('pinpoint')

stack_name = sys.argv[1]
recommender_model_arn = sys.argv[2]
lambda_transform = sys.argv[3]
pinpoint_role = sys.argv[4]
pinpoint_application_id = sys.argv[5]
from_address = sys.argv[6]
bucket_name = sys.argv[7]


segment_s3_location = "s3://" + bucket_name + "/pinpoint/pinpoint-users.csv"

###################################################################################
#Get the account and region from the data passed in
accountInfo = recommender_model_arn.split(":")
accountId = accountInfo[4]
region = accountInfo[3]

###################################################################################
#Add the ML model
response = client.create_recommender_configuration(
    CreateRecommenderConfiguration={
        'Attributes': {
            'Recommendations.RecommendedItems': 'RECOMMENDED_ITEM',
            'Recommendations.FriendlyRecommendation': 'FRIENDLY_RECOMMENDATION'
        },
        'Name': stack_name + "-recommender",
        'RecommendationProviderIdType': 'PINPOINT_USER_ID',
        'RecommendationProviderRoleArn': pinpoint_role,
        'RecommendationProviderUri': recommender_model_arn,
        'RecommendationTransformerUri': lambda_transform,
        'RecommendationsPerMessage': 1
    }
)

recommenderId = response['RecommenderConfigurationResponse']["Id"]

###################################################################################
#Create email template
response = client.create_email_template(
    EmailTemplateRequest={
        'DefaultSubstitutions': '{"Recommendations.FriendlyRecommendation": ""}',
        'HtmlPart': '<!DOCTYPE html><html lang="en"><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head><body style="font-family: Arial, Helvetica, sans-serif; background-color: #f9f8f8;"><div><div style="float:left;">MyCharity</div><div style="float:right">#HelpForHaiti</div></div><div style="clear:both;font-size:30px;text-align:center">Help us help Haiti</div><p style="clear:both;">Hi {{User.UserAttributes.FirstName}}! </p><p>For many children in Haiti, a safe education is merely a dream. Tons of schools are in need of repair, while an alarming nubmer of communiites don''t have schools at all. This means that children like Martha don''t have a safe place to learn.</p><div style="clear:both; padding:20px; display:block; text-align: center;"> <img src="https://raw.githubusercontent.com/aws-samples/nonprofit-samples/main/Targeted_Messaging_With_Pinpoint_and_Personalize/support/sample_image.png" width="500"/> </div><p>At MyCharity, we believe that, together with your help, we can change that. </p><p>{{Recommendations.FriendlyRecommendation}}</p></body></html>',
        'RecommenderId': recommenderId,
        'Subject': 'Latest updates on our mission',
        'TextPart': 'Hi {{User.UserAttributes.FirstName}}! For many children in Haiti, a safe education is merely a dream. Tons of schools are in need of repair, while an alarming nubmer of communities don''t have schools at all. This means that children like Martha don''t have a safe place to learn. At MyCharity, we believe that, together with your help, we can change that. {{Recommendations.FriendlyRecommendation}}'
    },
    TemplateName=stack_name + "_template"
)

###################################################################################
#Enable the email channel
ses = boto3.client('ses')
response = ses.verify_email_identity(
    EmailAddress=from_address
)

#Ensure the email has been verified before proceeding...
response = ses.get_identity_verification_attributes(Identities=[from_address])
verifiedStatus = False 
try:
    if response['VerificationAttributes'][from_address]['VerificationStatus'] == 'Success':
        verifiedStatus = True
except: 
    pass 

if not verifiedStatus:
    print('')
    print('**********************************************************************************************')
    print("Waiting for SES email address to be verified. Please check your email and click the verification link.")

while not verifiedStatus:
    time.sleep(60)
    response = ses.get_identity_verification_attributes(Identities=[from_address])
    try:
        if response['VerificationAttributes'][from_address]['VerificationStatus'] == 'Success':
            verifiedStatus = True
            print('SES email address has been verified. Continuing with the build-out.')
        else:
            print('Waiting for SES email address to be verified. Please check your email...')    
    except: 
        print('Waiting for SES email address to be verified. Please check your email...')


#Email has been verified, so proceed
response = client.update_email_channel(
   ApplicationId=pinpoint_application_id,
    EmailChannelRequest={
        'Enabled': True,
        'FromAddress': from_address,
        'Identity': "arn:aws:ses:" + region + ":" + accountId + ":identity/" + from_address,
        'RoleArn': pinpoint_role
    }
)

###################################################################################
#Create campaign segment
response = client.create_import_job(
    ApplicationId=pinpoint_application_id,
    ImportJobRequest={
        'DefineSegment': True, 
        'Format': 'CSV',
        'RegisterEndpoints': True, 
        'RoleArn': pinpoint_role,
        'S3Url': segment_s3_location,
        'SegmentName': 'all_users'
    }
)

segmentId = response['ImportJobResponse']['Definition']['SegmentId']
#Sleep for 30 seconds to allow the job to complete
time.sleep(30)

###################################################################################
#Create email campaign
response = client.create_campaign(
    ApplicationId=pinpoint_application_id,
    WriteCampaignRequest={
        'Name': stack_name + '_campaign',
        'SegmentId': segmentId,
        'TemplateConfiguration': {
            'EmailTemplate': {
                'Name': stack_name + '_template'
            }
        },
        'HoldoutPercent': 0,
        'Schedule': {
            'IsLocalTime': False,
            'Timezone': 'UTC-07',
            'StartTime': 'IMMEDIATE'
        }
    }
)