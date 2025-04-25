#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { Aspects } from 'aws-cdk-lib';
import { RedshiftStack } from "../lib/redshift";
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';

const app = new cdk.App();

// Create the authentication stack
const redshift = new RedshiftStack(app, "RedshiftStack"); 

Aspects.of(app).add(new AwsSolutionsChecks());