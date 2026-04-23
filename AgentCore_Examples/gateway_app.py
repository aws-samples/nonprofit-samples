# This code provides a sample solution for demonstration purposes. Organizations should implement security best practices, 
# including controls for authentication, data protection, and prompt injection in any production workloads. Please reference 
# the security pillar of the AWS Well-Architected Framework and Amazon Bedrock documentation for more information.

"""
AgentCore Gateway - Streamlit Demo App

Connects to the MCP gateway deployed by gateway-with-interceptor.yaml and
demonstrates inbound auth (Cognito client_credentials) and outbound OAuth
(GitHub 3LO via AgentCore Identity).
"""

# ---------------------------------------------------------------------------
# Configuration — edit these values to point at your deployed stack
# ---------------------------------------------------------------------------
AWS_REGION              = ""
COGNITO_CLIENT_ID       = ""
COGNITO_CLIENT_SECRET   = ""
COGNITO_HOSTED_URL      = ""
GATEWAY_URL             = ""
GATEWAY_ID              = ""
# ---------------------------------------------------------------------------


import json
import os
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import boto3
import requests
import streamlit as st

COGNITO_TOKEN_URL = f"{COGNITO_HOSTED_URL}/oauth2/token"
MCP_VERSION = "2025-11-25"  # MCP protocol version — unlikely to need changing

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AgentCore Gateway Demo",
    page_icon="🔗",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar – status / cache controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("⚙️ Configuration")
    with st.expander("Show active config", expanded=False):
        st.code(f"""
AWS_REGION            = {AWS_REGION}
COGNITO_CLIENT_ID     = {COGNITO_CLIENT_ID}
COGNITO_TOKEN_URL     = {COGNITO_TOKEN_URL}
GATEWAY_URL           = {GATEWAY_URL}
""", language="python")
    st.caption("Edit the constants at the top of gateway_app.py to change these.")
    st.divider()
    if st.button("🗑️ Clear session cache"):
        for key in ["cognito_token", "cognito_token_expiry"]:
            st.session_state.pop(key, None)
        st.success("Cache cleared")

# use the module-level constants directly
region              = AWS_REGION
cognito_client_id   = COGNITO_CLIENT_ID
cognito_client_secret = COGNITO_CLIENT_SECRET
cognito_token_url   = COGNITO_TOKEN_URL
gateway_url         = GATEWAY_URL
mcp_version         = MCP_VERSION

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_cognito_token() -> str:
    """Return a valid Cognito access token, refreshing when within 60s of expiry."""
    now = time.time()
    if (
        "cognito_token" in st.session_state
        and st.session_state.get("cognito_token_expiry", 0) - now > 60
    ):
        return st.session_state["cognito_token"]

    resp = requests.post(
        cognito_token_url,
        data={"grant_type": "client_credentials", "scope": "gateway/invoke"},
        auth=(cognito_client_id, cognito_client_secret),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    st.session_state["cognito_token"] = data["access_token"]
    st.session_state["cognito_token_expiry"] = now + data.get("expires_in", 3600)
    return data["access_token"]


def call_gateway(method: str, params: dict) -> dict:
    """Send a JSON-RPC request to the gateway and return the parsed response."""
    token = get_cognito_token()
    resp = requests.post(
        gateway_url,
        headers={
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": mcp_version,
            "Content-Type": "application/json",
        },
        json={"jsonrpc": "2.0", "id": "1", "method": method, "params": params},
        timeout=30,
    )
    return resp.json()


def extract_elicitation_url(response: dict) -> str | None:
    """Pull the GitHub OAuth authorization URL out of an elicitations error."""
    try:
        return response["error"]["data"]["elicitations"][0]["url"]
    except (KeyError, IndexError, TypeError):
        return None


@st.cache_data(ttl=30)
def get_fresh_auth_url() -> str:
    """
    Fetch the current authorization URL directly from the control plane.
    Cached for 30s to avoid a signed API call on every Streamlit rerun.
    """
    try:
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.httpsession import URLLib3Session

        session = boto3.session.Session()
        credentials = session.get_credentials().get_frozen_credentials()
        http_session = URLLib3Session()

        def signed_get(url):
            req = AWSRequest(method="GET", url=url, headers={"Content-Type": "application/json"})
            SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(req)
            response = http_session.send(req.prepare())
            return json.loads(response.text)

        control_url = f"https://bedrock-agentcore-control.{region}.amazonaws.com/gateways/{GATEWAY_ID}/targets/"
        data = signed_get(control_url)

        pending_statuses = {"CREATE_PENDING_AUTH", "UPDATE_PENDING_AUTH"}
        for target in data.get("items", []):
            if target.get("status") in pending_statuses:
                target_data = signed_get(control_url + target["targetId"] + "/")
                auth_url = (
                    target_data.get("authorizationData", {})
                    .get("oauth2", {})
                    .get("authorizationUrl", "")
                )
                if auth_url:
                    return auth_url
    except Exception as e:
        st.sidebar.warning(f"Control plane check failed: {e}")
    return ""



# ---------------------------------------------------------------------------
# Local OAuth callback listener
# Starts an HTTP server on port 8080 that catches the GitHub redirect,
# extracts the session_id, and calls complete_resource_token_auth automatically.
# ---------------------------------------------------------------------------

CALLBACK_PORT = 8080
_listener_lock = threading.Lock()

# IPC between the background thread and the Streamlit poll loop via a temp
# file. In-memory state (threading.Event, module-level dicts) gets wiped
# whenever Streamlit's file watcher reloads the module, so a file is used
# instead — it survives reloads and process boundaries.
_RESULT_FILE = os.path.join(tempfile.gettempdir(), "agentcore_auth_result.json")


def _write_result(data: dict):
    with open(_RESULT_FILE, "w") as f:
        json.dump(data, f)


def _read_result() -> dict:
    try:
        with open(_RESULT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _clear_result():
    try:
        os.remove(_RESULT_FILE)
    except FileNotFoundError:
        pass


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # suppress default request logging
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        session_id = params.get("session_id", [None])[0]

        if session_id:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Authorization complete! You can close this tab.</h2></body></html>")
            _write_result({"session_id": session_id})
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Missing session_id. Please try again.</h2></body></html>")
            _write_result({"error": "Missing session_id in callback"})

        threading.Thread(target=self.server.shutdown, daemon=True).start()


def start_callback_listener(user_token: str, auth_url: str):
    """
    Open the auth URL in the browser and start a local HTTP server on
    port 8080 to catch the redirect. Writes outcome to a temp file so it
    survives Streamlit module reloads. Does NOT touch st.session_state.
    """
    _clear_result()

    with _listener_lock:
        server = None
        try:
            server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            webbrowser.open(auth_url)

            # Poll the result file for up to 120s
            deadline = time.time() + 120
            while time.time() < deadline:
                result = _read_result()
                if result:
                    break
                time.sleep(1)
            else:
                _write_result({"error": "Timed out waiting for GitHub callback."})
                return

            session_id = result.get("session_id")
            if not session_id:
                return

            client = boto3.client("bedrock-agentcore", region_name=region)
            response = client.complete_resource_token_auth(
                userIdentifier={"userToken": user_token},
                sessionUri=unquote(session_id),
            )
            _write_result({"success": True, "response": str(response)})
        except Exception as e:
            _write_result({"error": str(e)})
        finally:
            if server:
                try:
                    server.server_close()
                except Exception:
                    pass

# ---------------------------------------------------------------------------
# Startup check — surface pending auth without needing a gateway request
# ---------------------------------------------------------------------------
if get_fresh_auth_url():
    st.session_state["target_pending_auth"] = True
else:
    st.session_state.pop("target_pending_auth", None)

# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------
st.title("🔗 AgentCore Gateway Demo")
st.caption(
    "Inbound: Cognito client_credentials → Gateway  |  "
    "Outbound: GitHub OAuth 3LO via AgentCore Identity"
)

if st.session_state.get("target_pending_auth"):
    st.warning(
        "⚠️ GitHub authorization required before the gateway can serve tools. "
        "Go to the **GitHub Auth** tab to complete the flow.",
        icon="🔑",
    )

tab_tools, tab_search, tab_auth = st.tabs(
    ["🛠️ List Tools", "🔍 Search Repos", "🔑 GitHub Auth"]
)

# ---------------------------------------------------------------------------
# Tab: List Tools
# ---------------------------------------------------------------------------
with tab_tools:
    st.subheader("Available MCP Tools")
    if st.button("Fetch tools", key="btn_list_tools"):
        with st.spinner("Calling gateway…"):
            try:
                result = call_gateway("tools/list", {})
                if "error" in result:
                    auth_url = extract_elicitation_url(result)
                    st.error(f"Gateway error: {result['error'].get('message', result['error'])}")
                    if auth_url:
                        st.warning("GitHub authorization required — go to the **GitHub Auth** tab.")
                        st.session_state["pending_auth_url"] = auth_url
                elif "result" in result:
                    tools = result["result"].get("tools", [])
                    st.success(f"{len(tools)} tool(s) found")
                    for tool in tools:
                        with st.expander(f"**{tool['name']}**"):
                            st.write(tool.get("description", "_No description_"))
                            if "inputSchema" in tool:
                                st.json(tool["inputSchema"])
                else:
                    st.json(result)
            except Exception as e:
                st.error(f"Request failed: {e}")

# ---------------------------------------------------------------------------
# Tab: Search Repos
# ---------------------------------------------------------------------------
with tab_search:
    st.subheader("Search GitHub Repositories")

    with st.form("search_form"):
        query = st.text_input("Search query", placeholder="e.g. machine learning stars:>1000 language:python")
        col1, col2, col3 = st.columns(3)
        with col1:
            sort = st.selectbox("Sort", ["", "stars", "forks", "help-wanted-issues", "updated"])
        with col2:
            order = st.selectbox("Order", ["", "desc", "asc"])
        with col3:
            per_page = st.number_input("Results per page", min_value=1, max_value=100, value=10)
        minimal = st.checkbox("Minimal output", value=True)
        submitted = st.form_submit_button("Search")

    if submitted:
        if not query:
            st.warning("Enter a search query.")
        else:
            args: dict = {"query": query, "minimal_output": minimal}
            if sort:
                args["sort"] = sort
            if order:
                args["order"] = order
            args["perPage"] = per_page

            with st.spinner("Searching…"):
                try:
                    result = call_gateway(
                        "tools/call",
                        {"name": "github-copilot-mcp___search_repositories", "arguments": args},
                    )

                    if "error" in result:
                        auth_url = extract_elicitation_url(result)
                        st.error(f"Gateway error: {result['error'].get('message', result['error'])}")
                        if auth_url:
                            st.session_state["pending_auth_url"] = auth_url
                            st.session_state["pending_cognito_token"] = get_cognito_token()
                            st.warning("GitHub authorization required — go to the **GitHub Auth** tab.")
                    elif "result" in result:
                        content = result["result"].get("content", [])
                        for item in content:
                            if item.get("type") == "text":
                                text = item["text"]
                                try:
                                    parsed = json.loads(text)
                                    repos = parsed if isinstance(parsed, list) else parsed.get("items") or parsed.get("repositories") or parsed
                                    if isinstance(repos, list):
                                        for repo in repos:
                                            name = repo.get("full_name") or repo.get("name", "")
                                            desc = repo.get("description") or "_No description_"
                                            stars = repo.get("stargazers_count", repo.get("stars", ""))
                                            url = repo.get("html_url", "")
                                            lang = repo.get("language") or ""
                                            cols = st.columns([3, 1, 1])
                                            cols[0].markdown(f"**[{name}]({url})**  \n{desc}")
                                            cols[1].markdown(f"⭐ {stars:,}" if isinstance(stars, int) else f"⭐ {stars}")
                                            cols[2].markdown(f"`{lang}`" if lang else "")
                                    else:
                                        st.json(parsed)
                                except (json.JSONDecodeError, TypeError):
                                    st.markdown(text)
                            else:
                                st.json(item)
                    else:
                        st.json(result)
                except Exception as e:
                    st.error(f"Request failed: {e}")

# ---------------------------------------------------------------------------
# Tab: GitHub Auth (outbound 3LO)
# ---------------------------------------------------------------------------
with tab_auth:
    st.subheader("GitHub OAuth Authorization (Outbound 3LO)")
    st.markdown(
        """
        When the gateway needs to call GitHub on your behalf it returns an
        **elicitation URL**. Click **Authorize with GitHub** below — the app
        will open GitHub in your browser and automatically complete the flow
        when you approve. No copy-pasting required.
        """
    )

    # Show result only if it was produced in this session
    if st.session_state.get("auth_result"):
        _prev = st.session_state["auth_result"]
        if _prev.get("success"):
            st.success("Authorization complete! GitHub token stored in AgentCore token vault.")
        elif _prev.get("error"):
            st.error(f"Authorization failed: {_prev.get('error')}")

    default_token = st.session_state.get("pending_cognito_token", "")
    user_token = st.text_input(
        "User token (Cognito access token)",
        value=default_token,
        type="password",
        help="Identifies your session to AgentCore Identity. Auto-populated from gateway requests.",
    )
    if st.button("🔄 Refresh token"):
        try:
            tok = get_cognito_token()
            st.session_state["pending_cognito_token"] = tok
            st.success("Token refreshed.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to get token: {e}")

    if st.button("🔑 Authorize with GitHub", disabled=not user_token):
        # Prefer the elicitation URL captured from a gateway response,
        # fall back to the control plane for targets still pending auth
        fresh_url = st.session_state.get("pending_auth_url") or get_fresh_auth_url()
        if not fresh_url:
            st.error("No authorization URL found. Make a request in Search Repos or List Tools first to get one, or redeploy if the target is stuck.")
        else:
            _clear_result()
            t = threading.Thread(
                target=start_callback_listener,
                args=(user_token, fresh_url),
                daemon=True,
            )
            t.start()
            st.session_state["auth_in_progress"] = True
            st.rerun()

    if st.session_state.get("auth_in_progress"):
        _result = _read_result()
        if _result:
            st.session_state.pop("auth_in_progress", None)
            st.session_state["auth_result"] = _result
            if _result.get("success"):
                st.session_state.pop("pending_cognito_token", None)
                st.session_state.pop("target_pending_auth", None)
                get_fresh_auth_url.clear()
            st.rerun()
        else:
            st.rerun()

