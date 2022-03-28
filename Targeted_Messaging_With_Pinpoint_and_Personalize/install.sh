#!/usr/bin/env bash

set -e 

##################################################################
#
# Set the following variables 
#
##################################################################
EMAIL_ADDRESS=email_address@goes.here
STACK_NAME=STACK_NAME_GOES_HERE

##################################################################
# 
# You shouldn't need to change anything below this line
#
##################################################################
#Check to make sure all required commands are installed
if ! command -v jq &> /dev/null
then
    echo "jq could not be found. Please install and then re-run the installer"
    exit
fi

if ! command -v aws &> /dev/null
then
    echo "aws could not be found. Please install and then re-run the installer"
    exit
fi

REGION=$(aws configure get region)
ACCOUNT_ID=$(aws sts get-caller-identity | jq '.Account' -r)

if [ -z "$REGION" ]; then
    echo "Please set a region by running 'aws configure'"
    exit
fi

python3 create_segment_data.py $EMAIL_ADDRESS

echo "Creating stack..."
STACK_ID=$( aws cloudformation create-stack --stack-name ${STACK_NAME} \
  --template-body file://pinpoint-infrastructure.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  | jq -r .StackId \
)

echo "Waiting on ${STACK_ID} create completion..."
aws cloudformation wait stack-create-complete --stack-name ${STACK_ID}
CFN_OUTPUT=$(aws cloudformation describe-stacks --stack-name ${STACK_ID} | jq .Stacks[0].Outputs)

BUCKET_NAME=$(echo $CFN_OUTPUT | jq '.[]| select(.OutputKey | contains("BucketName")).OutputValue' -r)
PERSONALIZE_ROLE=$(echo $CFN_OUTPUT | jq '.[]| select(.OutputKey | contains("PersonalizeRole")).OutputValue' -r)
LAMBDA_TRANSFORM=$(echo $CFN_OUTPUT | jq '.[]| select(.OutputKey | contains("LambdaTransform")).OutputValue' -r)
PINPOINT_APP_ID=$(echo $CFN_OUTPUT | jq '.[]| select(.OutputKey | contains("PinpointApplicationId")).OutputValue' -r)
PINPOINT_ROLE=$(echo $CFN_OUTPUT | jq '.[]| select(.OutputKey | contains("PinpointRole")).OutputValue' -r)


aws s3 cp UserInteractions.csv s3://$BUCKET_NAME/npo/UserInteractions.csv
aws s3 cp pinpoint-users.csv s3://$BUCKET_NAME/pinpoint/pinpoint-users.csv

echo "Creating the personalization model..."
PERS_STACK_ID=$( aws cloudformation create-stack --stack-name ${STACK_NAME}-personalize \
  --template-body file://personalize-infrastructure.yml \
  --parameters ParameterKey=ImportFileUri,ParameterValue=s3://${BUCKET_NAME}/npo/UserInteractions.csv ParameterKey=PersonalizeRoleArn,ParameterValue=${PERSONALIZE_ROLE} \
  | jq -r .StackId \
)

aws cloudformation wait stack-create-complete --stack-name ${PERS_STACK_ID}
CFN_OUTPUT=$(aws cloudformation describe-stacks --stack-name ${PERS_STACK_ID} | jq .Stacks[0].Outputs)
PERSONALIZE_SOL_ARN=$(echo $CFN_OUTPUT | jq '.[]| select(.OutputKey | contains("PersonalizeSolutionArn")).OutputValue' -r)

python3 create_personalize.py $STACK_NAME $PERSONALIZE_SOL_ARN

CAMPAIGN_ARN=$(aws personalize list-campaigns | jq '.campaigns[] | select(.name=="'${STACK_NAME}'_campaign") | .campaignArn' -r)

#echo "Building out the campaign..."
python3 create_pinpoint_campaign.py $STACK_NAME $CAMPAIGN_ARN $LAMBDA_TRANSFORM $PINPOINT_ROLE $PINPOINT_APP_ID $EMAIL_ADDRESS $BUCKET_NAME