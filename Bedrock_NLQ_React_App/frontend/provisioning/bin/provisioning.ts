#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { Aspects } from 'aws-cdk-lib';
import { WebAclStack } from "../lib/web-acl-stack";
import { FrontendStack } from "../lib/frontend-stack";
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';


const app = new cdk.App();
const waf = new WebAclStack(app, "FrontendWebAclStack");

new FrontendStack(app, "FrontendStack").addDependency(waf);

Aspects.of(app).add(new AwsSolutionsChecks());