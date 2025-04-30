import os
import logging
import boto3
from botocore.config import Config

# AWS Session & Clients
REGION = boto3.session.Session().region_name
RETRY_CONFIG = Config(retries={'max_attempts': 10})
session = boto3.Session(region_name=REGION)

# Initialize Bedrock client
agent_client = session.client('bedrock-agent-runtime', region_name=REGION, config=RETRY_CONFIG)

# Initialize DynamoDB for conversation history
dynamodb = boto3.resource('dynamodb')
dynamodb_table = dynamodb.Table(os.environ.get('TABLE_NAME'))

# Logger Configuration
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())


# Environment Variables
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID')
MODEL_ID = os.environ.get('MODEL_ID')