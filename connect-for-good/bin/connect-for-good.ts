#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { ConnectForGoodStack } from "../lib/connect-for-good-stack";

const app = new cdk.App();
new ConnectForGoodStack(app, "ConnectForGoodStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || "us-east-1",
  },
  description: "Connect for Good - Amazon Connect Nonprofit Contact Center Demo",
});
