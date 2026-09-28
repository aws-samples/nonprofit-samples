import { NagSuppressions } from 'cdk-nag';
import { Stack } from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';


export function addAuthStackSuppressions(
  stack: Stack,
  userPool: cognito.UserPool,
): void {
    
  NagSuppressions.addResourceSuppressions(
    userPool,
    [
      {
        id: 'AwsSolutions-COG3',
        reason: 'Advanced Security Mode is not required for this development environment due to additional costs',
      },
      {
        id: 'AwsSolutions-COG2',
        reason: 'MFA not required to ensure a simple user experience for the demo environment.',
      },
    ],
    true
  );
 

}

export function addDataStackSuppressions(
  stack: Stack,
): void {
    
    // Stack level suppressions if needed
  NagSuppressions.addStackSuppressions(stack, [
    {
        id: 'AwsSolutions-IAM5',
        reason: 'Wilcard permissions used within resource partitions.',
    },
    {
        id: 'AwsSolutions-IAM4',
        reason: 'Managed policies are used for service roles with restricted actions',
    },
    {
        id: 'AwsSolutions-S1',
        reason: 'Bucket logging not required for the sample data and athena query buckets.',
    },
    {
        id: 'AwsSolutions-L1',
        reason: 'Runtime used for BucketDeployment construct is not managed in a user deployed lambda.',
    },
    {
        id: 'AwsSolutions-GL1',
        reason: 'Encryption for Glue Crawler cloudwatch logs is not neccesary for the crawler run as a deployment pre-requisite.',
    },
    
  ]);

}

export function addAPIStackSuppressions(
  stack: Stack,
): void {
    
    // Stack level suppressions if needed
  NagSuppressions.addStackSuppressions(stack, [
    {
        id: 'AwsSolutions-IAM5',
        reason: 'Wilcard permissions used within resource partitions.',
    },
    {
        id: 'AwsSolutions-IAM4',
        reason: 'Managed policies are used for service roles with restricted actions',
    },
  ]);

}

export function addRedshiftStackSuppressions(
  stack: Stack,
): void {
    
    // Stack level suppressions if needed
  NagSuppressions.addStackSuppressions(stack, [
    {
        id: 'AwsSolutions-IAM5',
        reason: 'Wilcard permissions used within resource partitions.',
    },
    {
        id: 'AwsSolutions-SMG4',
        reason: 'Secret rotation not required for the demo environment',
    },
  ]);

}

