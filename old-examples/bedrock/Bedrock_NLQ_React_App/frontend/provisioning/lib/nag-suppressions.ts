import { NagSuppressions } from 'cdk-nag';
import { Stack } from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';


export function addFrontEndStackSuppressions(
  stack: Stack,
): void {
    
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
        reason: 'Bucket logging not required for the website and cloudfront logging buckets.',
    },
    {
        id: 'AwsSolutions-CFR1',
        reason: 'Geo restrictions not required for this demo deployment',
    },
    {
        id: 'AwsSolutions-L1',
        reason: 'Runtime used for BucketDeployment construct is not managed in a user deployed lambda.',
    },
    {
        id: 'AwsSolutions-S5',
        reason: 'Cloudfront distribution using L2 construct for OAC for secure access to S3 bucket.',
    },
    {
        id: 'AwsSolutions-CFR4',
        reason: 'Cloudfront distribution using the default viewer certificate for this demo deployment. Restricting TLS version using minimumProtocolVersion.',
    }


    
  ]);
 

}
