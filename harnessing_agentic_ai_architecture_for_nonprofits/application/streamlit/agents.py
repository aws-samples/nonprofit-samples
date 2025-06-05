import streamlit as st
import boto3
from botocore.config import Config
from botocore.exceptions import ReadTimeoutError
import time
import json
import logging
import pprint
from datetime import datetime

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# Configure the client with custom timeouts and retries
config = Config(
    connect_timeout=5,  # Connection timeout
    read_timeout=60,    # Read timeout
    retries={
        'max_attempts': 3,  # Number of retry attempts
        'mode': 'adaptive'  # Adaptive retry mode
    }
)    
# Set the page configuration
st.set_page_config(page_title="The National Council for Mental Wellbeing")

st.markdown("""
    <style>
    /* Main container */
    .stChatFloatingInputContainer {
        background-color: rgba(255, 255, 255, 0.1) !important;
        position: fixed !important;
        bottom: 0 !important;
        left: 0 !important;
        right: 0 !important;
        padding: 1rem !important;
        z-index: 100 !important;
    }
    
    /* Messages container */
    .stChatMessageContainer {
        padding-bottom: 100px !important; /* Add space for input box */
        overflow-y: auto !important;
        max-height: calc(100vh - 100px) !important; /* Adjust height to account for input */
        margin-bottom: 0 !important;
    }
    
    /* The actual input field */
    .stChatInput textarea {
        background-color: rgba(255, 255, 255, 0.1) !important;
        border: 1px solid rgba(128, 128, 128, 0.3) !important;
    }

    /* Chat message styling */
    .stChatMessage {
        background-color: white !important;
        border: 1px solid #ddd !important;
        border-radius: 8px !important;
        padding: 10px !important;
        margin: 5px 0 !important;
    }
    
    /* Message content */
    .stMarkdown {
        background-color: white !important;
    }
    
    /* Background styling */
    .stApp {
        background-image: linear-gradient(rgba(255, 255, 255, 0.5), rgba(255, 255, 255, 0.5)), url("https://d1qg0m9urd24mb.cloudfront.net/Unity_Mission_homepage.png");
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
    }
    
    /* Remove box shadows */
    .stChatInput *, div[data-testid="stChatInput"] * {
        box-shadow: none !important;
    }
    </style>
""", unsafe_allow_html=True)


# Set up logging
logging.basicConfig(
    format='[%(asctime)s] p%(process)s {%(filename)s:%(lineno)d} %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Set up AWS clients
region = 'us-east-1'
session = boto3.Session(region_name=region)
lambda_client = session.client('lambda')
bedrock_agent_runtime_client = session.client('bedrock-agent-runtime', config=config)

# Background Image and Styling
st.markdown(
    """
    <style>
    .stApp {
        background-image: linear-gradient(rgba(255, 255, 255, 0.5), rgba(255, 255, 255, 0.5)), url("https://d1qg0m9urd24mb.cloudfront.net/Unity_Mission_homepage.png");
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
    }
    .supervisor-text {
        font-size: 32px;
        font-weight: bold;
        color: #333;
        margin-bottom: 20px;
        text-align: center; /* Center the supervisor text */
    }
    </style>
    """,
    unsafe_allow_html=True
)

def extract_source_info(metadata):
    """Extract and validate source information from metadata."""
    try:
        source_info = {}
        
        if metadata:
            # Extract all metadata fields
            for key, value in metadata.items():
                source_info[key] = value
            
            # If there's a location field, add it to source_info
            if 'location' in metadata:
                if 's3Location' in metadata['location']:
                    source_info['s3_uri'] = metadata['location']['s3Location']['uri']
                source_info['location_type'] = metadata['location']['type']
            
        return source_info
    except Exception as e:
        logger.error(f"Error extracting source info: {e}")
        return None

# Define the invokeAgent function
def invokeAgent(query, session_id, enable_trace=True, session_state=dict()):
    max_retries = 10
    base_delay = 1  # Base delay in seconds
    end_session = False
    for attempt in range(max_retries):
        try:
            agentResponse = bedrock_agent_runtime_client.invoke_agent(
                inputText=query,
                agentId='XXXXXXXXXX',
                agentAliasId='XXXXXXXXXX',
                sessionId=session_id,
                enableTrace=enable_trace, 
                endSession=end_session,
                sessionState=session_state
            )
        
            # if enable_trace:
            #     logger.info(pprint.pformat(agentResponse))
            
            event_stream = agentResponse['completion']
            #print(f"event_stream: {event_stream}")
            sources = []
            agent_answer = ""
            if event_stream:
                try:
                    for event in event_stream:        
                        if 'chunk' in event:
                            data = event['chunk']['bytes']
                            if enable_trace:
                                logger.info(f"Final answer ->\n{data.decode('utf8')}")
                            agent_answer = data.decode('utf8')
                            sources.append({"source": "none"})
                            return { 'answer': agent_answer, 'sources': sources }
                        elif 'trace' in event:
                            if enable_trace:
                                logger.info(json.dumps(event['trace'], indent=2, cls=DateTimeEncoder))
                            # Extract source information from trace
                            if 'knowledgeBaseSearchResults' in event['trace']:
                                for result in event['trace']['knowledgeBaseSearchResults']:
                                    if 'metadata' in result:
                                        source_info = extract_source_info(result['metadata'])
                                        if source_info:
                                            sources.append(source_info)
                        else:
                            raise Exception("unexpected event.", event)
                    return {
                        'answer': agent_answer,
                        'sources': sources
                    }
                except ReadTimeoutError as e:
                    if attempt == max_retries - 1:
                        raise Exception(f"Failed after {max_retries} attempts: {str(e)}")
                
                    # Calculate exponential backoff delay
                    delay = (2 ** attempt) * base_delay
                    logger.warning(f"Read timeout occurred. Retrying in {delay} seconds...")
                    time.sleep(delay)
                    continue
            else:
                agent_answer = "I apologize, but I do not have enough information to determine the answer to your question. If you are able to provide more details about the database schema or access to the actual donation data, I would be happy to try again to answer your question."
        except Exception as e:
            raise Exception("unexpected event.", e)

def main():
    # Centered Title
    #st.markdown("<h1 style='text-align: center;'>OCCRRA</h1>", unsafe_allow_html=True)
    
    # Centered Header
    #st.markdown("<h3 style='text-align: center;'>by Amazon Bedrock Agents</h3>", unsafe_allow_html=True)

    # Supervisor Text
    #st.markdown('<div class="supervisor-text">Orchestrator Agent:</div>', unsafe_allow_html=True)

    # Chat Input Container
    with st.container():
        if prompt := st.chat_input(key="supervisor", placeholder="How can I help you today?"):
            # Append user input only if it's new
            if not st.session_state.messages or st.session_state.messages[-1]['content'] != prompt:
                st.session_state.messages.append({"role": "user", "content": prompt})

            # Get agent response and append it only if it's new
            sessionId = st.session_state.get('sessionId', "None")
            result = invokeAgent(prompt, sessionId)
            print(f"Type of result: {type(result)}")
            print(f"Result value: {result}")
            # Display the answer
            if st.session_state.messages[-1]['content'] != result['answer']:
                st.session_state.messages.append({"role": "assistant", "content": result['answer']})
            
            # Display sources if available
            if result['sources']:
                with st.expander("View Sources"):
                    for source in result['sources']:
                        st.write("Source Information:")
                        for key, value in source.items():
                            if value:
                                st.write(f"{key}: {value}")
                        st.write("---")

    # Display previous chat messages
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    if 'sessionId' not in st.session_state:
        st.session_state['sessionId'] = "None"

    #for message in reversed(st.session_state.messages):
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # Footer
    #st.markdown(
    #    """
    #    <div class="footer">
    #        For inquiries, contact <a href="mailto:wchemz@amazon.com">wchemz@amazon.com</a>
    #    </div>
    #    """,
    #    unsafe_allow_html=True
    #)

if __name__ == '__main__':
    main()
