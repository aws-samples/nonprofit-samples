import os
import boto3
import psycopg2

def lambda_handler(event, context):
    rds_host = os.environ['DB_ENDPOINT']
    db_name = os.environ['DB_NAME']
    username = os.environ['DB_USERNAME']
    password = os.environ['DB_PASSWORD']

    # Connect to the PostgreSQL database
    conn = psycopg2.connect(
        host=rds_host,
        database=db_name,
        user=username,
        password=password
    )
    cursor = conn.cursor()

    # Execute DDL commands
    with open('ddl.sql', 'r') as ddl_file:
        ddl_script = ddl_file.read()
        cursor.execute(ddl_script)

    # Execute data loading commands
    with open('data.sql', 'r') as data_file:
        data_script = data_file.read()
        cursor.execute(data_script)

    # Commit and close
    conn.commit()
    cursor.close()
    conn.close()

    return {"statusCode": 200, "body": "Data loaded successfully"}
