import config
import time

#### HELPER FUNCTION TO CHECK THE SYNTAX OF THE GENERATED SQL  ####        

def syntax_checker(query):
    
    query_config = {"OutputLocation": config.ATHENA_RESULTS_S3 }
    query_execution_context = {
        "Catalog": config.GLUE_CATALOG,
        "Database": config.GLUE_DB_NAME
    }
    
    try:
        response = config.athena_client.start_query_execution(
            QueryString=query,
            ResultConfiguration=query_config,
            QueryExecutionContext=query_execution_context,
            WorkGroup=config.ATHENA_WORKGROUP
        )
    
        execution_id = response["QueryExecutionId"]

        config.logger.info(f"Query execution ID: {execution_id}")
        
        # Wait for the query to complete
        while True:
            response_wait = config.athena_client.get_query_execution(QueryExecutionId=execution_id)
            state = response_wait['QueryExecution']['Status']['State']
            
            if state in ['QUEUED', 'RUNNING']:
                config.logger.info("Query is still running...")
                time.sleep(1)  # Add small delay
                continue
            break
            
        config.logger.info(f"Query finished with state: {state}")
    
        # Check if the query completed successfully
        if state == 'SUCCEEDED':
            # Fetch query results
            results_response = config.athena_client.get_query_results(QueryExecutionId=execution_id)
            
            # Extract results
            result_data = []
            rows = results_response.get("ResultSet", {}).get("Rows", [])
            for row in rows:
                result_data.append([col.get("VarCharValue", "") for col in row.get("Data", [])])
            
            return {
               "state": "PASSED",
               "output": result_data 
            }
        else:
            config.logger.error(f"Query failed syntax check")
            message = response_wait['QueryExecution']['Status']['StateChangeReason']

            return {
                "state": "FAILED",
                "output": message
            }
            
    except Exception as e:
        errorMessage = f"An error occurred checking the SQL query syntax: {str(e)}"
        config.logger.error(errorMessage)
        raise Exception(errorMessage)

    return message
    

