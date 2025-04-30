import { fetchAuthSession } from "@aws-amplify/auth";
import { apiEndpoint } from "./Chatbot";


// Function to retrieve a session token from Cognito with the currently authenticated user
export const getToken = async () => {
  try {
    const session = await fetchAuthSession(); // Fetch the current authentication session

    if (!session.tokens?.idToken) {
      throw new Error("No access token found");
    }
  
    return `Bearer ${session.tokens?.idToken}`; // return the bearer token for the user
  } catch (error) {
    console.error("Error getting auth token:", error);
    throw error;
  }
};

interface MessageRequest {
  message: string;
  id: string; 
  kb_session_id: string;
}

export const postMessage = async (requestData: MessageRequest) => {
  try {
    const token = await getToken(); // retrieve the bearer token for the user 
    
    const res = await fetch(`${apiEndpoint}nlq`, {
      method: 'POST',
      headers: {
        "Authorization": token, // pass the cognito token to authorize our API call
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestData) 
    });
    
    const responseData = await res.json();
    
    // check for HTTP errors (400-599)
    if (!res.ok) {
      // pull custom error message from API response, if none provided then return generic error
      // this is where I pull the answer from the JSON. 
      throw new Error(responseData?.answer || `API error: ${res.status} ${res.statusText}`);
    }
    
    return responseData;
    
  } catch (error) {
      const err = error as Error;
      // Throw an error instead of returning a failed response
      throw new Error(
        err.message.includes("Failed to fetch") || err.message.includes("NetworkError")
          ? "Network error: Unable to reach server. Please check your connection."
          : err.message
      );
  }
};

