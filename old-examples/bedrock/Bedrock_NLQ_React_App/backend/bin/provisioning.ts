#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { Aspects } from 'aws-cdk-lib';
import { APIStack } from "../lib/api";
import { AuthStack } from "../lib/auth";
import { DataStack } from "../lib/data";
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';

const app = new cdk.App();
//const env = {
//  region: "us-east-1", //select the deployment region for resources
//};

// Create the authentication stack
const auth = new AuthStack(app, "AuthStack"); 

//Create the data stack
const data = new DataStack(app, "DataStack");

//Create the API stack
//Pass in resource context from previous stacks
new APIStack(app, "APIStack", { 
  userPool: auth.userPool,
  table: data.table,
  athenaQueryBucket: data.athenaQueryBucket,
  sampleDataBucket: data.sampleDataBucket,
  glueDatabaseName: data.glueDatabaseName,
  workgroupName: data.workgroupName,
});

Aspects.of(app).add(new AwsSolutionsChecks());