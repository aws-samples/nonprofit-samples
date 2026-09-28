#!/bin/bash

# Check if bucket name is provided
if [ $# -ne 1 ]; then
    echo "Usage: $0 <bucket-name>"
    exit 1
fi

BUCKET_NAME=$1
LAMBDA_PATH="s3://${BUCKET_NAME}/donations/lambda"
LAYER_PATH="s3://${BUCKET_NAME}/donations"

# Verify bucket exists
if ! aws s3 ls "s3://${BUCKET_NAME}" >/dev/null 2>&1; then
    echo "Error: Bucket ${BUCKET_NAME} does not exist or is not accessible"
    exit 1
fi

# Copy psycopg2 layer to donations directory
echo "Copying psycopg2 layer..."
if ! aws s3 cp layers/psycopg2.zip "${LAYER_PATH}/psycopg2.zip"; then
    echo "Error: Failed to copy psycopg2.zip"
    exit 1
fi

# Copy all zip files from zips directory to lambda subdirectory
echo "Copying Lambda function zip files..."
if ! aws s3 cp zips/ "${LAMBDA_PATH}/" --recursive --exclude "*" --include "*.zip"; then
    echo "Error: Failed to copy zip files from zips directory"
    exit 1
fi

echo "All files copied successfully!"
echo "Layer copied to: ${LAYER_PATH}/psycopg2.zip"
echo "Lambda files copied to: ${LAMBDA_PATH}/"
