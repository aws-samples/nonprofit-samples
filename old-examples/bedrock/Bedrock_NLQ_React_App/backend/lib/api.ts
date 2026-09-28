import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaNodejs from "aws-cdk-lib/aws-lambda-nodejs";
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as athena from 'aws-cdk-lib/aws-athena';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as waf from "aws-cdk-lib/aws-wafv2";
import * as agw from "aws-cdk-lib/aws-apigateway";
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from "constructs";
import * as path from "path";
import { addAPIStackSuppressions } from "./nag-suppressions";


/**
 * API Stack
 * 
 * Purpose:
 * Creates a secure API infrastructure with AWS API Gateway, Lambda, WAF protection,
 * and Cognito authentication integration. Our Lambda includes Natural Language Query (NLQ) capabilities
 * using AWS Athena and Bedrock.
 * 
 * Resources Created:
 * 
 * 1. API Gateway:
 *    - REST API with CORS enabled
 *    - Protected by Cognito authorizer
 * 
 * 2. Lambda Function:
 *    - NLQ Function:
 *      - Custom layer for PyAthena and SQLAlchemy
 *      - Permissions for S3, Athena, Glue, Bedrock, and DynamoDB
 * 
 * 3. WAF (Web Application Firewall):
 *    - Restricts traffic that can access our API endpoint
 *    - IP-based allowlist (set in cdk.json)
 *    - AWS Managed Rules:
 *      - Common Rule Set
 *      - IP Rule Set for allowed ranges
 * 
 * 4. API Endpoints:
 *    - POST /nlq: Natural Language Query endpoint
 *    Endpoint requires Cognito authentication
 * 
 * Required Props:
 * - userPool: Cognito User Pool for authentication
 * - tableName: DynamoDB table name
 * - athenaQueryBucketName: S3 bucket for Athena queries
 * - glueDatabaseName: Glue database name for Athena
 *
 */
 
interface APIStackProps extends cdk.StackProps {
  userPool: cognito.UserPool;
  table: dynamodb.Table;
  athenaQueryBucket: s3.Bucket;
  sampleDataBucket: s3.Bucket;
  glueDatabaseName: string;
  workgroupName: string;
}

export class APIStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: APIStackProps) {
    super(scope, id, props);

    const authorizer = new agw.CognitoUserPoolsAuthorizer(this, "Authorizer", {
      cognitoUserPools: [props.userPool], // pass in the user pool created in our Auth stack
    });
    
    // Create the Lambda function
    const lambdaFn = new lambda.Function(this, 'MyLambdaFunction', {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'lambda_function.lambda_handler',  
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/nlq')), 
      timeout: cdk.Duration.seconds(300),
      environment: {
        ATHENA_OUTPUT: `s3://${props.athenaQueryBucket.bucketName}`, // use the S3 bucket created for Athena query results in our data stack
        GLUE_CATALOG: 'AwsDataCatalog', // use the default glue catalog
        GLUE_DB: props.glueDatabaseName, // use the glue database created in our Data stack
        TABLE_NAME: props.table.tableName, //use the DynamoDB table name created in our Data stack
        ATHENA_WORKGROUP: props.workgroupName, // use the Athena workgroup created in our Data stack
        MODEL_ID: scope.node.tryGetContext("modelId")
      }
    });
    
    // Add permissions for the Lambda function to access resources from our Data stack
    props.table.grantReadWriteData(lambdaFn);
    props.athenaQueryBucket.grantRead(lambdaFn); 
    props.athenaQueryBucket.grantWrite(lambdaFn);
    props.sampleDataBucket.grantRead(lambdaFn); 
    props.sampleDataBucket.grantWrite(lambdaFn);
    
    // Add custom permissions for resources without CDK grant methods
    lambdaFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock:InvokeModel'
      ],
      resources: [
        `arn:aws:bedrock:*::foundation-model/*`,
        `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/*`,
      ]
    }));
    
    lambdaFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'glue:GetTable',
        'glue:GetPartition',
        'glue:GetPartitions',
        'glue:GetDatabase',
        'glue:GetTables'
      ],
      resources: [
        `arn:aws:glue:${this.region}:${this.account}:catalog`,
        `arn:aws:glue:${this.region}:${this.account}:database/${props.glueDatabaseName}`,
        `arn:aws:glue:${this.region}:${this.account}:table/${props.glueDatabaseName}/*`
      ]
    }));
    
    lambdaFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'athena:StartQueryExecution',
        'athena:GetQueryExecution',
        'athena:GetQueryResults',
        'athena:StopQueryExecution'
      ],
      resources: [
        `arn:aws:athena:${this.region}:${this.account}:workgroup/*`
      ],
    }));
    
    
    // Create a Lambda function with Bedrock Knowledge Bases as NLQ pipeline
    const lambdaFnKB = new lambda.Function(this, 'MyLambdaFunctionKB', {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'lambda_function.lambda_handler',  
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/nlq-kb')), 
      timeout: cdk.Duration.seconds(300),
      environment: {
        KNOWLEDGE_BASE_ID: scope.node.tryGetContext("BedrockKnowledgeBaseId"),
        MODEL_ID: scope.node.tryGetContext("modelId"),
        TABLE_NAME: props.table.tableName, 
      }
    });
    
    props.table.grantReadWriteData(lambdaFnKB);
    
    lambdaFnKB.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        "bedrock:InvokeModel",
        "bedrock:QueryKnowledgeBase",
        "bedrock:GenerateQuery",
        "bedrock:Retrieve",
        "bedrock:RetrieveAndGenerate",
        "bedrock:GetInferenceProfile",
      ],
      resources: ["*"]
    }));
    
    // Create a Web Application Firewall (WAF) to restrict traffic to our API endpoint 
    
    // Retrieve IP ranges from the CDK context (cdk.json)
    const ipRanges: string[] = scope.node.tryGetContext(
      "allowedIpAddressRanges"
    );

    const wafIPSet = new waf.CfnIPSet(this, `IPSet`, {
      name: "BackendWebAclIpSet",
      ipAddressVersion: "IPV4",
      scope: "REGIONAL",
      addresses: ipRanges,
    });

    // Create Web Access Control List (ACL)
    const apiWaf = new waf.CfnWebACL(this, "waf", {
      defaultAction: { block: {} },
      scope: "REGIONAL",
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        sampledRequestsEnabled: true,
        metricName: "ApiGatewayWAF",
      },
      rules: [
        // AWSManagedRulesCommonRuleSet
        {
          priority: 1,
          overrideAction: { none: {} },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: "AWS-AWSManagedRulesCommonRuleSet",
          },
          name: "AWSManagedRulesCommonRuleSet",
          statement: {
            managedRuleGroupStatement: {
              vendorName: "AWS",
              name: "AWSManagedRulesCommonRuleSet",
            },
          },
        },
        // AWSManagedRulesKnownBadInputsRuleSet
        // Only allow traffic from the IPs defined in our IP ranges
        {
          priority: 2,
          name: "BackendWebAclIpRuleSet",
          action: { allow: {} },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: "BackendWebAclIpRuleSet",
          },
          statement: {
            ipSetReferenceStatement: {
              arn: wafIPSet.attrArn,
            },
          },
        },
      ],
    });

    // Create log group for API Gateway
    const logGroup = new logs.LogGroup(this, 'ApiGatewayAccessLogs', {
      retention: logs.RetentionDays.ONE_MONTH, // Adjust retention as needed
    });

    // Definition of API Gateway
    const api = new agw.RestApi(this, "NLQApi", {
      deployOptions: {
        stageName: "api",
        accessLogDestination: new agw.LogGroupLogDestination(logGroup),
        accessLogFormat: agw.AccessLogFormat.clf(),
        loggingLevel: agw.MethodLoggingLevel.INFO,
        dataTraceEnabled: true,
        metricsEnabled: true
      },
      cloudWatchRole: true,
      defaultCorsPreflightOptions: {
        allowOrigins: agw.Cors.ALL_ORIGINS,
        allowMethods: agw.Cors.ALL_METHODS,
      },
      endpointConfiguration: {
        types: [agw.EndpointType.REGIONAL], 
      },
    });
    
    // Associate WAF with API Gateway
    const region = cdk.Stack.of(this).region;
    const restApiId = api.restApiId;
    const stageName = api.deploymentStage.stageName;
    new waf.CfnWebACLAssociation(this, "apply-waf-apigw", {
      webAclArn: apiWaf.attrArn,
      resourceArn: `arn:aws:apigateway:${region}::/restapis/${restApiId}/stages/${stageName}`,
    });
    
    
    // Create request validator
    const validator = new agw.RequestValidator(this, 'DefaultValidator', {
      restApi: api,
      validateRequestBody: true,
      validateRequestParameters: true,
    });
    
    // Create the request model - ensure that the request coming from the chatbot is in the form [message, id]
    const requestModel = new agw.Model(this, 'RequestModel', {
      restApi: api,
      contentType: 'application/json',
      modelName: 'MessageRequest',
      schema: {
        type: agw.JsonSchemaType.OBJECT,
        required: ['message', 'id', 'kb_session_id'], 
        properties: {
          message: { 
            type: agw.JsonSchemaType.STRING,
          },
          id: { 
            type: agw.JsonSchemaType.STRING,
          },
          kb_session_id: { 
            type: agw.JsonSchemaType.STRING,
          }
        },
        additionalProperties: false
      }
    });
    
    // Get the NLQ pipeline mode from the cdk.json context
    const nlqPipelineMode = scope.node.tryGetContext("nlqPipelineMode");
    
    // Validate the pipeline mode
    if (nlqPipelineMode !== "S3" && nlqPipelineMode !== "KB") {
      throw new Error(
        `Invalid nlqPipelineMode in cdk.json: "${nlqPipelineMode}". ` +
        `Valid options are "S3" or "KB". Please update your cdk.json file.`
      );
    }
    
    // Determine which Lambda function to use based on the mode
    const nlqLambdaFunction = nlqPipelineMode === "KB" 
      ? lambdaFnKB // Bedrock Knowledge Base implementation
      : lambdaFn; // Default to S3 implementation with Athena

    // POST: /nlq
    const userinfoNLQ = api.root.addResource("nlq");
    userinfoNLQ.addMethod("POST", new agw.LambdaIntegration(nlqLambdaFunction), {
      authorizer: authorizer,
      authorizationType: agw.AuthorizationType.COGNITO,
      requestValidator: validator,
      requestModels: {
        'application/json': requestModel
      }
    });
    
    
    // Suppressions for CDK Nag security warnings
    addAPIStackSuppressions(this);
  }
}
