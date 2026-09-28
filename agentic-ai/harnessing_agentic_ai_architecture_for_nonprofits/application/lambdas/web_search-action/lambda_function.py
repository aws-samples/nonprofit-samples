import logging
import json
import zipfile
from datetime import datetime, timedelta
import os
import boto3
import sys
import time
import tempfile

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def is_cache_valid(cache_path, max_age_hours=1):
    """Check if cached dependencies are still valid"""
    if not os.path.exists(cache_path):
        return False
    
    cache_time = datetime.fromtimestamp(os.path.getmtime(cache_path))
    age = datetime.now() - cache_time
    
    return age < timedelta(hours=max_age_hours)



def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")
    # Get API key from environment variables
    api_key = os.environ.get('NOVA_ACT_API_KEY')
    if not api_key:
        logger.error("NOVA_ACT_API_KEY environment variable not set")
        return {
            'statusCode': 500,
            'body': 'Missing NOVA_ACT_API_KEY environment variable'
        }

    # Create the full path structure that Python expects
    # Use a secure temporary directory created by tempfile.mkdtemp()
    tmp_dir = tempfile.mkdtemp(prefix="lambda_dependencies_")
    site_packages = os.path.join(tmp_dir, 'python', 'lib', 'python3.13', 'site-packages')
    zip_path = os.path.join(tmp_dir, f'package_{os.urandom(4).hex()}.zip')
    
    # Check if we already have valid cached dependencies
    if not is_cache_valid(site_packages):
        if not os.path.exists(site_packages):
            logger.info(f"Creating directory structure: {site_packages}")
            os.makedirs(site_packages, exist_ok=True)
            
        # Download and extract only if needed
        s3_client = boto3.client('s3')
        try:
            logger.info("Downloading package from S3")
            s3_client.download_file(
                'nova-act-vb',
                'nova_act_layer.zip',
                zip_path
            )
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmp_dir)
                
            logger.info(f"Extracted package.zip to {tmp_dir}")
            os.remove(zip_path)
        except Exception as e:
            logger.error(f"Failed to download and extract dependencies: {str(e)}")
            return {
                'statusCode': 500,
                'body': f'Failed to download and extract dependencies: {str(e)}'
            }
    
    # Add the site-packages directory to Python path
    if site_packages not in sys.path:
        sys.path.insert(0, site_packages)
    logger.info(f"Python path: {sys.path}")
    
    try:
        # Clear all nova_act related modules
        modules_to_remove = [m for m in sys.modules if m.startswith('nova_act')]
        for m in modules_to_remove:
            del sys.modules[m]
        
        # Import the module
        import nova_act
        logger.info("Successfully imported nova_act")

        # Print nova_act version and configuration if available
        logger.info(f"Nova Act Version: {getattr(nova_act, '__version__', 'unknown')}")
        logger.info(f"Nova Act Config: {getattr(nova_act, 'config', 'unknown')}")

        # Set API key in environment before creating instance
        os.environ['NOVA_ACT_API_KEY'] = api_key
        logger.info("Set API key in environment")

        try:
            # Initialize NovaAct with required starting_page argument and debug mode if available
            nova = nova_act.NovaAct(
                "https://www.zoocasa.com/"
            )
            logger.info("Created NovaAct instance")

            max_retries = 5  # Increased from 3 to 5
            startup_delay = 5  # Seconds to wait before first start attempt

            # Initial delay before first attempt
            logger.info(f"Waiting {startup_delay} seconds before first start attempt...")
            time.sleep(startup_delay)

            for attempt in range(max_retries):
                try:
                    # Check if client exists and its state
                    if hasattr(nova, '_state'):
                        logger.info(f"Client state before stop attempt: {nova._state}")

                    # Try to stop first if already running
                    try:
                        nova.stop()
                        logger.info("Stopped existing client")
                        time.sleep(3)  # Increased cool-down period
                    except Exception as stop_error:
                        logger.info(f"Stop attempt result: {str(stop_error)}")

                    # Log attempt details
                    logger.info(f"Starting client (attempt {attempt + 1}/{max_retries})...")
                    
                    # Start fresh with explicit error capture
                    try:
                        start_result = nova.start()
                        logger.info(f"Start command result: {start_result}")
                    except Exception as start_error:
                        logger.error(f"Start command error: {str(start_error)}")
                        raise start_error

                    # Add longer stabilization period after start
                    time.sleep(5)

                    # Verify client state after start
                    if hasattr(nova, '_state'):
                        logger.info(f"Client state after start: {nova._state}")

                    # Test if client is responsive with timeout
                    logger.info("Testing client responsiveness...")
                    test_start_time = time.time()
                    test_timeout = 10  # 10 seconds timeout for test
                    
                    while time.time() - test_start_time < test_timeout:
                        try:
                            test_result = nova.act("test")
                            if test_result:
                                logger.info(f"Client is responsive. Test result: {test_result}")
                                break
                        except Exception as test_error:
                            logger.warning(f"Test attempt failed: {str(test_error)}")
                            time.sleep(1)
                    else:
                        raise Exception("Client responsiveness test timed out")

                    # If we got here, client is working
                    logger.info("Client successfully started and tested")
                    break

                except Exception as retry_error:
                    logger.warning(f"Attempt {attempt + 1} failed: {str(retry_error)}")
                    
                    # On last attempt, gather diagnostic information
                    if attempt == max_retries - 1:
                        logger.error("All start attempts failed. Gathering diagnostics...")
                        try:
                            # Log system information if available
                            import psutil
                            logger.error(f"Memory usage: {psutil.Process().memory_info()}")
                            logger.error(f"CPU usage: {psutil.cpu_percent()}")
                        except ImportError:
                            logger.error("psutil not available for diagnostics")
                        
                        raise Exception(f"Failed to start client after {max_retries} attempts: {str(retry_error)}")
                    
                    # Wait longer between retries
                    retry_delay = (attempt + 1) * 5  # Progressive delay
                    logger.info(f"Waiting {retry_delay} seconds before next attempt...")
                    time.sleep(retry_delay)

            # If we get here, client is ready for operations
            logger.info("Proceeding with main operations...")

            # Rest of your code with additional error handling
            operations = [
                ("accept cookies", "accept cookies"),
                ("search", "search for houses in Toronto"),
                ("filter", "Find houses that have at least 3 bedrooms")
            ]

            for op_name, op_command in operations:
                try:
                    logger.info(f"Executing {op_name}...")
                    result = nova.act(op_command)
                    logger.info(f"{op_name} result: {result}")
                    if not result:
                        raise Exception(f"Failed to {op_name}")
                    time.sleep(2)
                except Exception as op_error:
                    logger.error(f"Operation {op_name} failed: {str(op_error)}")
                    raise

        except Exception as e:
            logger.error(f"Error during execution: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': str(e),
                    'details': getattr(e, 'details', None)
                })
            }
    finally:
        try:
            if hasattr(nova, 'stop'):
                logger.info("Attempting to stop client...")
                nova.stop()
                logger.info("Successfully stopped NovaAct client")
        except Exception as stop_error:
            logger.error(f"Error stopping client: {stop_error}")

    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Operations completed successfully'})
    }