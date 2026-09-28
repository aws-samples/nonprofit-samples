import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Chatbot from './Chatbot'
import { v4 as uuidv4 } from 'uuid'

// Generate a unique ID to track each conversational session
// A page refresh will generate a new conversation ID 
// If you want a longer lasting session ID, consider using the Cognito session ID tied to the logged in user
const generated_uuid: string = uuidv4();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Chatbot generated_uuid={generated_uuid} />
  </StrictMode>,
)
