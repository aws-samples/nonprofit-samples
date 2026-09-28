import config


def get_relevant_metadata(user_query):

    try:
        # Get all tables in the database
        response = config.glue_client.get_tables(DatabaseName=config.GLUE_DB_NAME)
        tables = response.get("TableList", [])
        
        schema_details = {}

        for table in tables:
            table_name = table["Name"]
            columns = table.get("StorageDescriptor", {}).get("Columns", [])

            # Extract column details
            schema_details[table_name] = [
                {"Name": col["Name"], "Type": col["Type"]} for col in columns
            ]
 
        config.logger.info(f"Metadata retrieved: {schema_details}")
        
        return schema_details

    except Exception as e:

        errorMessage = f"Metadata retrieval Failed: {str(e)}"
        config.logger.error(errorMessage)
        raise Exception(errorMessage)

    return schema_details