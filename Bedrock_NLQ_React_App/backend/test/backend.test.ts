import * as cdk from 'aws-cdk-lib';
import { App } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { APIStack } from '../lib/api';
import { AuthStack } from '../lib/auth';
import { DataStack } from '../lib/data';
import * as fs from 'fs';

describe('CDK Stack Synthesis', () => {
  let app: cdk.App;
  let env: cdk.Environment;
  let authStack: AuthStack;
  let dataStack: DataStack;
  let apiStack: APIStack;

  beforeAll(() => {
    app = new App();

    // Load CDK Context from cdk.json (ip addresses for WAF in API stack)
    const cdkJson = JSON.parse(fs.readFileSync('cdk.json', 'utf8'));
    for (const [key, value] of Object.entries(cdkJson.context)) {
      app.node.setContext(key, value);
    }

    env = { region: 'us-east-1' };

    // Instantiate dependent stacks
    authStack = new AuthStack(app, 'TestAuthStack', { env });
    dataStack = new DataStack(app, 'TestDataStack', { env });

    apiStack = new APIStack(app, 'TestAPIStack', { 
      userPool: authStack.userPool,
      tableName: dataStack.tableName,
      athenaQueryBucketName: dataStack.athenaQueryBucketName,
      glueDatabaseName: dataStack.glueDatabaseName,
      env,
    });
  });

  test('Auth Stack synthesizes successfully', () => {
    expect(() => Template.fromStack(authStack)).not.toThrow();
  });

  test('Data Stack synthesizes successfully', () => {
    expect(() => Template.fromStack(dataStack)).not.toThrow();
  });

  test('API Stack synthesizes successfully', () => {
    expect(() => Template.fromStack(apiStack)).not.toThrow();
  });
});
