import os
import logging
import boto3
from botocore.config import Config

# Environment Variables
ATHENA_RESULTS_S3 = os.environ.get('ATHENA_OUTPUT')
GLUE_CATALOG = os.environ.get('GLUE_CATALOG')
GLUE_DB_NAME = os.environ.get("GLUE_DB")
ATHENA_WORKGROUP = os.environ.get('ATHENA_WORKGROUP')
TABLE_NAME = os.environ.get('TABLE_NAME')
MODEL_ID = os.environ.get('MODEL_ID')

# Logger Configuration
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())

# AWS Session & Clients
REGION = boto3.session.Session().region_name
RETRY_CONFIG = Config(retries={'max_attempts': 10})
session = boto3.Session(region_name=REGION)

bedrock_client = session.client('bedrock-runtime', region_name=REGION, config=RETRY_CONFIG)
athena_client = session.client('athena', config=RETRY_CONFIG)
s3_client = session.client('s3', config=RETRY_CONFIG)
glue_client = session.client('glue', config=RETRY_CONFIG)


# DynamoDB Resource
dynamodb = boto3.resource('dynamodb')
dynamodb_table = dynamodb.Table(TABLE_NAME)
