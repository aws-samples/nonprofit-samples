#!/bin/bash

# Define paths with absolute path for OUTPUT_DIR
LAMBDA_SRC_DIR="./lambdas"
OUTPUT_DIR="$(pwd)/lambda-output"

# Recreate the output directory to ensure it's clean and has correct permissions
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# Array to map folder paths to desired zip file names
folder_paths=(
    "database-action"
    "orchestrator-action/invoke-kb-agent-action"
    "orchestrator-action/invoke-api-agent-action"
    "orchestrator-action/invoke-db-agent-action"
    "rest-api-action"
)

zip_names=(
    "DatabaseActionLambda.zip"
    "OrchestratorKBActionLambda.zip"
    "OrchestratorAPIActionLambda.zip"
    "OrchestratorDBActionLambda.zip"
    "RestAPIActionLambda.zip"
)

# Loop through each folder path and corresponding zip name
for i in "${!folder_paths[@]}"; do
    lambda_dir="${folder_paths[$i]}"
    zip_file="${OUTPUT_DIR}/${zip_names[$i]}"
    
    # Go into the Lambda function directory
    cd "$LAMBDA_SRC_DIR/$lambda_dir" || { echo "Directory $LAMBDA_SRC_DIR/$lambda_dir not found"; exit 1; }

    # Create the zip file in the output directory
    zip -r "$zip_file" . -x "*.DS_Store"
    
    echo "Created zip for $lambda_dir at $zip_file"
    
    # Return to the original directory
    cd - > /dev/null
done
