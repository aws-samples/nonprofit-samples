import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as athena from 'aws-cdk-lib/aws-athena';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as logs from 'aws-cdk-lib/aws-logs'
import { Construct } from 'constructs';
import * as path from "path";
import { addDataStackSuppressions } from "./nag-suppressions";
import { StringParameter } from 'aws-cdk-lib/aws-ssm';


/**
 * Data Stack
 * 
 * Purpose:
 * Creates and manages the data infrastructure for Natural Language Querying (NLQ),
 * including data storage, query processing, and analytics capabilities.
 * 
 * Resources Created:
 * 
 * 1. DynamoDB Table:
 *    - Stores chat history
 * 
 * 2. S3 Buckets:
 *    - Sample Data Bucket: Stores sample donor data for analysis
 *    - Athena Query Bucket: Stores Athena query results
 * 
 * 3. AWS Glue Resources:
 *    - Database: Catalogs metadata for data sources
 *    - Crawler: Automatically catalogs the sample data in our sample data bucket
 *    - IAM Role: Permissions for Glue crawler operations
 * 
 * 4. Amazon Athena:
 *    - Workgroup: Manages and organizes query executions
 *    - Query result location configured to the Athena query bucket
 * 
 * 5. Lambda Functions:
 *    - Glue Crawler Trigger: Initiates the crawler during deployment
 *    - Athena Cleanup: Handles workgroup cleanup during stack deletion
 * 
 * Data Flow:
 * 1. Sample data uploaded to S3
 * 2. Glue crawler catalogs the data
 * 3. Athena enables SQL queries against the cataloged data
 * 4. Query results stored in dedicated S3 bucket
 * 
 * Cleanup Handling:
 * - Custom resources manage proper resource cleanup
 * - Athena workgroup cleaned up via dedicated Lambda
 * - S3 buckets configured for automatic object deletion
 * 
 * Exported Values (for use in API stack):
 * - DynamoDB table name
 * - Athena query bucket name
 * - Glue database name
 * 
 */


export class DataStack extends cdk.Stack {
    public readonly table: dynamodb.Table;
    public readonly athenaQueryBucket: s3.Bucket;
    public readonly glueDatabaseName: string;
    public readonly sampleDataBucket: s3.Bucket;
    public readonly workgroupName: string;

    constructor(scope: Construct, id: string, props?: cdk.StackProps) {
        super(scope, id, props);
        
        // Service Role for Redshift User to use with Knowledge Bases 
        const kbRedshiftServiceRole = new iam.Role(this, 'KBRedshiftServiceRole', {
          roleName: 'KBRedshiftServiceRole_GenAI_Workshop',
          assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com'),
        });
    
        // Add inline policy for Redshift Data API permissions
        kbRedshiftServiceRole.addToPolicy(new iam.PolicyStatement({
          sid: 'RedshiftDataAPIStatementPermissions',
          effect: iam.Effect.ALLOW,
          actions: [
            'redshift-data:GetStatementResult',
            'redshift-data:DescribeStatement',
            'redshift-data:CancelStatement',
            'redshift-data:ExecuteStatement',
          ],
          resources: [
            `arn:aws:redshift-serverless:${this.region}:${this.account}:workgroup*`,
            `arn:aws:redshift-serverless:${this.region}:${this.account}:namespace*`,
            `arn:aws:redshift-data:${this.region}:${this.account}:statement/*`,
            "*"
          ],
        }));
    
        kbRedshiftServiceRole.addToPolicy(new iam.PolicyStatement({
          sid: 'GetCredentialsWithFederatedIAMCredentials',
          effect: iam.Effect.ALLOW,
          actions: ['redshift-serverless:GetCredentials'],
          resources: [
            `arn:aws:redshift-serverless:${this.region}:${this.account}:workgroup*`,
            `arn:aws:redshift-serverless:${this.region}:${this.account}:namespace*`,
          ],
        }));
    
        kbRedshiftServiceRole.addToPolicy(new iam.PolicyStatement({
          sid: 'SqlWorkbenchAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'sqlworkbench:GetSqlRecommendations',
            'sqlworkbench:PutSqlGenerationContext',
            'sqlworkbench:GetSqlGenerationContext',
            'sqlworkbench:DeleteSqlGenerationContext',
          ],
          resources: [
            `arn:aws:redshift-serverless:${this.region}:${this.account}:workgroup*`,
            `arn:aws:redshift-serverless:${this.region}:${this.account}:namespace*`,
            "*"
          ],
        }));
    
        kbRedshiftServiceRole.addToPolicy(new iam.PolicyStatement({
          sid: 'KbAccess',
          effect: iam.Effect.ALLOW,
          actions: ['bedrock:GenerateQuery'],
          resources: ["*"],
        }));
        
        // Create the DynamoDB table
        this.table = new dynamodb.Table(this, 'MyDynamoDBTable', {
          tableName: `NLQ-chat-history-${this.stackName}`, 
          partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
          sortKey: { name: 'timestamp', type: dynamodb.AttributeType.STRING },
          pointInTimeRecoverySpecification: {
            pointInTimeRecoveryEnabled: true,
          },
          billingMode: dynamodb.BillingMode.PROVISIONED, 
          readCapacity: 5,  
          writeCapacity: 5, 
          removalPolicy: cdk.RemovalPolicy.DESTROY, // Destroy table when stack is deleted
        });
        
        // Create S3 bucket for sample data
        this.sampleDataBucket = new s3.Bucket(this, 'SampleDataBucket', {
          enforceSSL: true,
          encryption: s3.BucketEncryption.S3_MANAGED,
          blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
          autoDeleteObjects: true,
        });
        
        // Submit sample bucket name to parameter store for use in future stacks
        new StringParameter(this, 'DataBucketNameParam', {
          parameterName: '/nlqCDK/sampleData/bucketName',
          stringValue: this.sampleDataBucket.bucketName,
        });
        
        // Upload local CSV data to the sample S3 bucket
        const dataUpload = new s3deploy.BucketDeployment(this, 'UploadCSV', {
          sources: [s3deploy.Source.asset(path.join(__dirname, '../sample_data'))], // Folder containing the CSV files
          destinationBucket: this.sampleDataBucket,
        });
        
        // Create S3 bucket for Athena query results
        this.athenaQueryBucket = new s3.Bucket(this, 'AthenaQueryBucket', {
          bucketName: `athena-results-bucket-cdk-stack-${this.account}`,
          enforceSSL: true,
          encryption: s3.BucketEncryption.S3_MANAGED,
          blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
          autoDeleteObjects: true,
        });
        
        // Create Glue database to catalog our sample data 
        this.glueDatabaseName = `glue_database_${this.stackName.toLowerCase()}`;
        
        const glueDatabase = new glue.CfnDatabase(this, 'GlueDatabase', {
          catalogId: this.account, // AWS Account ID as the Glue catalog ID
          databaseInput: {
            name: this.glueDatabaseName, 
            description: 'A Glue database created using AWS CDK',
          },
        });
        
        // Create an IAM Role for the Glue Crawler
        const glueCrawlerRole = new iam.Role(this, 'GlueCrawlerRole', {
          assumedBy: new iam.ServicePrincipal('glue.amazonaws.com'),
          managedPolicies: [
            iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSGlueServiceRole'),
          ],
        });
        
        // Attach inline policy for additional S3 permissions
        glueCrawlerRole.addToPolicy(new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: [
            's3:GetObject',
            's3:ListBucket',
            's3:PutObject',
          ],
          resources: [
            this.sampleDataBucket.bucketArn,
            `${this.sampleDataBucket.bucketArn}/*`,
          ],
        }));
      
            
        // Create Glue crawler to crawl the sample data S3 bucket
        const glueCrawler = new glue.CfnCrawler(this, 'GlueCrawler', {
          name: `GlueCrawler-${this.stackName}`,
          role:  glueCrawlerRole.roleArn,
          databaseName: this.glueDatabaseName,
          targets: {
            s3Targets: [{ path: `s3://${this.sampleDataBucket.bucketName}/` }],
          },
          tablePrefix: 'sample_',
          //crawlerSecurityConfiguration: securityConfig.name
        });
        
        // Create an Athena workgroup to segregate our queries from existing teams
        const workgroup = new athena.CfnWorkGroup(this, 'AthenaWorkgroup', {
          name: `AthenaWorkgroup-${this.stackName}`,
          description: 'Workgroup for Athena queries',
          state: 'ENABLED',
          workGroupConfiguration: {
            resultConfiguration: {
              outputLocation: `s3://${this.athenaQueryBucket.bucketName}/`,
            },
          },
        });
        
        this.workgroupName = workgroup.ref
        
        // Prevent direct deletion of the workgroup until explicitly removed by the custom resource (recusively empties query history then deletes)
        workgroup.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
        
        // Create an IAM Role for our Glue crawler Lambda
        const lambdaRoleCrawler = new iam.Role(this, 'LambdaGlueCrawlerRole', {
          assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
          managedPolicies: [
            iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
          ],
        });
        
        // Attach inline policy for Lambda function to run the Glue Crawler
        lambdaRoleCrawler.addToPolicy(new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: [
            'glue:StartCrawler',
          ],
          resources: [
            `arn:aws:glue:${this.region}:${this.account}:crawler/*`,
          ],
        }));
        
    
        // Create Lambda function to trigger Glue Crawler
        const glueCrawlerTriggerLambda = new lambda.Function(this, 'GlueCrawlerTriggerLambda', {
          runtime: lambda.Runtime.PYTHON_3_13,
          handler: 'index.lambda_handler',
          code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/glueCrawlerTrigger')),
          role: lambdaRoleCrawler,
          timeout: cdk.Duration.seconds(120),
          environment: {
            CRAWLER_NAME: glueCrawler.ref, // Pass the Glue Crawler name as an environment variable
          },
        });

        // Custom Resource to trigger the crawler during stack deployment
        const glueTriggerResource = new cdk.CustomResource(this, 'TriggerGlueCrawler', {
          serviceToken: glueCrawlerTriggerLambda.functionArn,
        });
        
        // Ensure the crawler runs after the data upload to avoid crawling an empty bucket
        glueTriggerResource.node.addDependency(dataUpload);
        
        // Custom Lambda to clean up Athena workgroup on stack DELETE
        const cleanupLambda = new lambda.Function(this, 'AthenaWorkgroupCleanupLambda', {
          runtime: lambda.Runtime.PYTHON_3_13,
          handler: 'index.lambda_handler',
          code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/cleanupAthena')),
          timeout: cdk.Duration.minutes(5),
          environment: {
            WORKGROUP_NAME: workgroup.name,
          },
        });
        
        // Attach IAM permissions to allow the Lambda to delete Athena workgroups
        cleanupLambda.addToRolePolicy(new iam.PolicyStatement({
          actions: [
            "athena:ListWorkGroups",      
            "athena:GetWorkGroup",         
            "athena:DeleteWorkGroup",      
            "athena:ListNamedQueries",     
            "athena:DeleteNamedQuery"      
          ],
          resources: [`arn:aws:athena:${this.region}:${this.account}:workgroup/${workgroup.name}`]  
        }));
        
        // Custom Resource to trigger cleanup before stack deletion
        const cleanupResource = new cdk.CustomResource(this, 'CleanupAthenaWorkgroup', {
          serviceToken: cleanupLambda.functionArn,
        });
        
        // Ensure the workgroup is deleted AFTER cleanupResource runs
        workgroup.node.addDependency(cleanupResource);
        
        // Suppressions for CDK Nag security warnings
        addDataStackSuppressions(this);
  }
}
