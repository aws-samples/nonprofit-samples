import json
import urllib.parse
import logging
import os
import http.client
import ssl
from urllib.parse import urlparse

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class SafeResponse:
    """Wrapper class to mimic urllib response interface"""
    def __init__(self, response):
        self.response = response
        self.data = None
        
    def getcode(self):
        return self.response.status
        
    def read(self):
        if self.data is None:
            self.data = self.response.read()
        return self.data
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self.response, 'close'):
            self.response.close()

def _make_http_request(url):
    """
    Make an HTTP request using http.client with scheme validation.
    Internal function not exposed directly.
    """
    parsed_url = urlparse(url)
    if parsed_url.scheme == 'https':
        conn = http.client.HTTPSConnection(parsed_url.netloc)
    else:
        conn = http.client.HTTPConnection(parsed_url.netloc)
    
    path = parsed_url.path
    if parsed_url.query:
        path += '?' + parsed_url.query
        
    conn.request('GET', path, headers={'User-Agent': 'Mozilla/5.0'})
    return conn.getresponse()

def safe_url_open(url):
    """
    Safely open a URL with strict scheme validation.
    Only allows http and https schemes.
    """
    parsed_url = urlparse(url)
    if parsed_url.scheme not in ['http', 'https']:
        raise ValueError(f"Unsupported URL scheme: {parsed_url.scheme}")
    
    response = _make_http_request(url)
    return SafeResponse(response)

def extract_search_term(input_text):
    # Look for text between single or double quotes
    import re
    
    # Find all text within quotes (single or double)
    matches = re.findall(r'["\'](.+?)["\']', input_text)
    
    # Return the first quoted string if found, otherwise None
    return matches[0] if matches else None
    
    return None

def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")
    
    message_version = event.get("messageVersion", "1.0")
    session_id = event.get("sessionId", "")
    api_path = event.get("apiPath", "/search")
    http_method = event.get("httpMethod", "GET")
    action_group = event.get("actionGroup", "")
    session_attributes = event.get("sessionAttributes", {})
    prompt_session_attributes = event.get("promptSessionAttributes", {})
    
    # Get the input text from the event
    input_text = event.get("inputText", "")
    search_term = extract_search_term(input_text)
    logger.info(f"Received api_path: {api_path}")
    logger.info(f"Extracted search term: {search_term}")
    
    response_body = {}

    if api_path == "/search" and http_method == "GET":
        try:
            # Use the search term in the API URL, fallback to * if no term found
            query = search_term if search_term else "*"
            # URL encode the query parameter
            encoded_query = urllib.parse.quote(query)
            url = f"https://projects.propublica.org/nonprofits/api/v2/search.json?q={encoded_query}"
            logger.info(f"Making API request to {url}")
            status_code = 200
            
            # Use the safe URL opener function
            with safe_url_open(url) as response:
                status_code = response.getcode()
                logger.info(f"API response status code: {status_code}")
                
                if status_code == 200:
                    nonprofits_data = json.loads(response.read().decode())
                    logger.info(f"API response data: {json.dumps(nonprofits_data)}")
                    
                    response_body = {
                        "status": "success",
                        "nonprofits": nonprofits_data.get("nonprofits", {}),
                        "message": f"Retrieved {len(nonprofits_data['organizations'])} {search_term if search_term else ''} nonprofits."
                    }
                    
                    action_response = {
                        'actionGroup': action_group,
                        'apiPath': api_path,
                        'httpMethod': http_method,
                        'httpStatusCode': 200,
                        'responseBody': response_body
                    }
                elif status_code == 404 or status_code == 500:
                    status_code = 200
                    response_body = {
                        "status": "not_found",
                        "message": "The requested resource was not found by the nonprofit REST API."
                    }

                    logger.error(f"API error: {json.dumps(response_body)}")

                    action_response = {
                        'actionGroup': action_group,
                        'apiPath': api_path,
                        'httpMethod': http_method,
                        'httpStatusCode': status_code,
                        'responseBody': response_body
                    }
                else:
                    response_body = {
                        "status": "error",
                        "message": f"Failed to fetch nonprofits. Status code: {status_code}"
                    }
                    
                    logger.error(f"API error: {json.dumps(response_body)}")
                    
                    action_response = {
                        'actionGroup': action_group,
                        'apiPath': api_path,
                        'httpMethod': http_method,
                        'httpStatusCode': status_code,
                        'responseBody': response_body
                    }

        except Exception as e:
            #status_code = response.getcode()
            if status_code == 404 or status_code == 500 or status_code == 200:
                    status_code = 200
                    response_body = {
                        "status": "not_found",
                        "message": "The requested resource was not found by the nonprofit REST API."
                    }

                    logger.error(f"API error: {json.dumps(response_body)}")

                    action_response = {
                        'actionGroup': action_group,
                        'apiPath': api_path,
                        'httpMethod': http_method,
                        'httpStatusCode': status_code,
                        'responseBody': response_body
                    }
            else:
                response_body = {
                    "status": "error",
                    "message": "An error occurred while fetching the nonprofits list.",
                    "error": str(e)
                }
            
                logger.error(f"Exception occurred: {str(e)}")
                
                action_response = {
                    'actionGroup': action_group,
                    'apiPath': api_path,
                    'httpMethod': http_method,
                    'httpStatusCode': 500,
                    'responseBody': response_body
                }

    elif api_path == "/organizations/{ein}" and http_method == "GET":
        try:
            # Extract the EIN from the parameters list
            parameters = event.get("parameters", [])
            ein = None
            for param in parameters:
                if param.get("name") == "ein":
                    ein = param.get("value")
                    break
            
            if not ein:
                logger.info("EIN parameter is missing")
                ein = "None"
                #raise ValueError("EIN parameter is missing")

            url = f"https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"
            logger.info(f"Making API request to {url}")
            
            try:
                # Use the safe URL opener function
                with safe_url_open(url) as response:
                    status_code = response.getcode()
                    logger.info(f"API response status code: {status_code}")
                    
                    if status_code == 200:
                        nonprofits_data = json.loads(response.read().decode())
                        logger.info(f"API response data: {json.dumps(nonprofits_data)}")
                        
                        response_body = {
                            "status": "success",
                            "nonprofits": nonprofits_data.get("organization", {}),
                            "message": f"Retrieved nonprofit data for EIN: {ein}"
                        }
                        
                        action_response = {
                            'actionGroup': action_group,
                            'apiPath': api_path,
                            'httpMethod': http_method,
                            'httpStatusCode': 200,
                            'responseBody': response_body
                        }
            except Exception as e:
                if hasattr(e, 'code') and e.code == 404:
                    response_body = {
                        "status": "not_found",
                        "message": f"Organization with EIN {ein} not found"
                    }
                    
                    action_response = {
                        'actionGroup': action_group,
                        'apiPath': api_path,
                        'httpMethod': http_method,
                        'httpStatusCode': 200,
                        'responseBody': response_body
                    }
                else:
                    raise  # Re-raise other HTTP errors

        except Exception as e:
            response_body = {
                "status": "error",
                "message": "An error occurred while fetching the nonprofits list.",
                "error": str(e)
            }
            
            logger.error(f"Exception occurred: {str(e)}")
            
            action_response = {
                'actionGroup': action_group,
                'apiPath': api_path,
                'httpMethod': http_method,
                'httpStatusCode': 500,
                'responseBody': response_body
            }
    else:
        response_body = {
            "status": "error",
            "message": "Invalid input or API path."
        }
        
        logger.error(f"Invalid input or API path: {json.dumps(response_body)}")

        action_response = {
            'actionGroup': action_group,
            'apiPath': api_path,
            'httpMethod': http_method,
            'httpStatusCode': 400,
            'responseBody': response_body
        }

    api_response = {
        'messageVersion': '1.0', 
        'response': action_response,
        'sessionAttributes': session_attributes,
        'promptSessionAttributes': prompt_session_attributes
    }

    logger.info(f"Final API response: {json.dumps(api_response)}")
    return api_response