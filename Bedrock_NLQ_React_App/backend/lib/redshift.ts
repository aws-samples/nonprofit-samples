import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as redshiftserverless from 'aws-cdk-lib/aws-redshiftserverless';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as path from "path";
import { Construct } from "constructs";
import { addRedshiftStackSuppressions } from "./nag-suppressions";
import { StringParameter } from 'aws-cdk-lib/aws-ssm';

export class RedshiftStack extends cdk.Stack {
  public readonly redshiftNamespaceName: string;
  public readonly redshiftWorkgroupName: string;

  public constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    
    // Pull in the sample data bucket name from the parameter store
    const sampleDataBucket = StringParameter.valueForStringParameter(
      this, 
      '/nlqCDK/sampleData/bucketName'
    );

    const redshiftIamRole = new iam.Role(this, 'RedshiftIAMRole', {
      assumedBy: new iam.ServicePrincipal('redshift.amazonaws.com'),
    });

    redshiftIamRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        's3:GetObject',
        's3:ListBucket',
      ],
      resources: [
        `arn:aws:s3:::${sampleDataBucket}/*`,
        `arn:aws:s3:::${sampleDataBucket}`,
      ],
    }));

    const redshiftKmsKey = new kms.Key(this, 'RedshiftKmsKey', {
      description: 'KMS Key for encrypting Redshift secret',
      enableKeyRotation: true,
    });

    const redshiftLoadDataLambdaRole = new iam.Role(this, 'RedshiftLoadDataLambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });

    // Add S3 access policy
    redshiftLoadDataLambdaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        's3:GetObject',
        's3:ListBucket',
      ],
      resources: [
        `arn:aws:s3:::${sampleDataBucket}/*`,
        `arn:aws:s3:::${sampleDataBucket}`,
      ],
    }));

    // Add Redshift Data API access
    redshiftLoadDataLambdaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'redshift-data:BatchExecuteStatement',
        'redshift-data:ExecuteStatement',
        'redshift-data:CancelStatement',
        'redshift-data:DescribeTable',
        'redshift-data:DescribeStatement',
        'redshift-data:GetStatementResult',
        'redshift-data:ListStatements',
        'redshift-data:ListTables',
        'redshift-data:ListSchemas',
        'redshift-data:ListDatabases',
        'redshift-data:GetStagingBucketLocation',
      ],
      resources: ['*'],
    }));

    // Add Redshift and Redshift Serverless access
    redshiftLoadDataLambdaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'redshift:DescribeClusterSnapshots',
        'redshift:DescribeEvents',
        'redshift:DescribeClusters',
        'redshift:GetClusterCredentials',
        'redshift-serverless:GetCredentials',
        'redshift-serverless:GetWorkgroup',
        'redshift-serverless:ListWorkgroups',
        'redshift-serverless:ListNamespaces',
        'redshift-serverless:ListRecoveryPoints',
        'redshift-serverless:ListSnapshots',
        'redshift-serverless:DescribeOneTimeCredit',
      ],
      resources: [
        `arn:aws:redshift-serverless:${this.region}:${this.account}:workgroup/*`,
        `arn:aws:redshift-serverless:${this.region}:${this.account}:namespace/*`,
      ],
    }));

    // Add CloudWatch Logs access
    redshiftLoadDataLambdaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
      ],
      resources: ['arn:aws:logs:*:*:*'],
    }));


    const redshiftSecret = new secretsmanager.Secret(this, 'RedshiftSecret', {
      secretName: `redshift-secret-${this.account}`,
      description: 'Secret for Redshift Serverless Namespace',
      encryptionKey: redshiftKmsKey,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'adminuser' }),
        generateStringKey: 'password',
        passwordLength: 16,
        excludeCharacters: '\"@/\\\"\' \\',
      },
    });

    // Add tags to the secret
    cdk.Tags.of(redshiftSecret).add('Redshift-Serverless', 'True');

    const redshiftNamespace = new redshiftserverless.CfnNamespace(this, 'RedshiftNamespace', {
      namespaceName: `redshift-serverless-namespace-${this.account}`,
      dbName: 'mydatabase',
      adminUsername: redshiftSecret.secretValueFromJson('username').unsafeUnwrap(),
      adminUserPassword: redshiftSecret.secretValueFromJson('password').unsafeUnwrap(),
      iamRoles: [redshiftIamRole.roleArn],
    });

    const redshiftWorkgroup = new redshiftserverless.CfnWorkgroup(this, 'RedshiftWorkgroup', {
      workgroupName: `redshift-serverless-workgroup-${this.account}`,
      namespaceName: redshiftNamespace.ref,
      publiclyAccessible: true,
      baseCapacity: 32,
    });

    const redshiftLoadDataLambda = new lambda.Function(this, 'RedshiftLoadDataLambda', {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'index.lambda_handler',
      role: redshiftLoadDataLambdaRole,
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/redshiftLoader')), 
      timeout: cdk.Duration.seconds(300),
      environment: {
        'WORKGROUP_NAME': redshiftWorkgroup.ref,
        'DATABASE_NAME': 'mydatabase',
        'IAM_ROLE': redshiftIamRole.roleArn,
        'BUCKET_NAME': sampleDataBucket,
      },
    });
    

    // Custom Resource to trigger the crawler during stack deployment
    const redshiftTriggerResource = new cdk.CustomResource(this, 'TriggerRedshiftLoad', {
      serviceToken: redshiftLoadDataLambda.functionArn,
    });
    
    
    // Outputs
    this.redshiftNamespaceName = redshiftNamespace.ref;
    new cdk.CfnOutput(this, 'CfnOutputRedshiftNamespaceName', {
      key: 'RedshiftNamespaceName',
      description: 'Name of the Redshift Serverless namespace.',
      value: this.redshiftNamespaceName!.toString(),
    });
    this.redshiftWorkgroupName = redshiftWorkgroup.ref;
    new cdk.CfnOutput(this, 'CfnOutputRedshiftWorkgroupName', {
      key: 'RedshiftWorkgroupName',
      description: 'Name of the Redshift Serverless workgroup.',
      value: this.redshiftWorkgroupName!.toString(),
    });
    
        
    
    // Suppressions for CDK Nag security warnings
    addRedshiftStackSuppressions(this);
    
  }
}





