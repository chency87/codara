To get the token usage for an account, UAG should use the access token from the vault-backed credential for that account. The provider auth file can be used as the materialized runtime copy for the CLI-primary account, but it is not the inventory source of truth.

If quota values have not been observed yet, the management API should return `null` for limits and reset timestamps rather than inventing placeholder defaults.

In the 2026 ecosystem, the Codex CLI primarily uses "Subscription-based" usage metrics (Plus/Pro quotas) rather than just a raw dollar balance.

1. Extracting the Token
For Codex OAuth accounts, the materialized `auth.json` file (typically `~/.codex/auth.json`) contains the OAuth session data used by the local CLI runtime. You’ll need the access token from the `tokens` payload.

Python
import json
import os

def get_codex_token():
    auth_path = os.path.expanduser("~/.codex/auth.json")
    with open(auth_path, "r") as f:
        data = json.load(f)
        return data.get("tokens", {}).get("access_token")
2. Querying the Usage Endpoints
Depending on whether you want "Standard API Usage" (tokens) or "Subscription Quota" (message limits), you hit different endpoints. In the current runtime, Codex OAuth quota tracking primarily relies on the WHAM endpoint below.

A. Subscription Quota (Message/Rate Limits)
Since the Codex CLI is a "First-Party" tool, it often draws from your ChatGPT Plus/Pro message limits. This endpoint returns your 5-hour and weekly windows.

Endpoint: GET https://chatgpt.com/backend-api/wham/usage

Headers: * Authorization: Bearer <access_token>

User-Agent: OpenAI-Codex-CLI/0.116.0 (Must match the CLI's agent)

B. Detailed Token Usage (Optional Developer View)
If you need a raw token breakdown from the provider, use the standard usage endpoint. The current UAG runtime does not implement an automatic token-optimization pipeline around this data.

Endpoint: GET https://api.openai.com/v1/usage?start_date={YYYY-MM-DD}&end_date={YYYY-MM-DD}

3. Implementation in the UAG AccountPool
For your Account Management Module, you should implement a "Usage Scraper" that runs as a background heartbeat.

Python
import requests
from datetime import datetime

def fetch_account_stats(token):
    # Standard Usage Endpoint
    today = datetime.now().strftime('%Y-%m-%d')
    url = f"https://api.openai.com/v1/usage?start_date={today}&end_date={today}"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        usage_data = response.json()
        return {
            "total_tokens": usage_data.get("total_usage", 0),
            "cached_tokens": usage_data.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            "timestamp": datetime.now().isoformat()
        }
    return None
4. Critical 2026 Limitations
Session Expiry: The access_token in auth.json is short-lived (usually 1 hour). To maintain a persistent proxy, your AccountPool must use the refresh_token from the same file to request a new access_token when you hit a 401 Unauthorized.

Rate Limit Headers: Always check the response headers of any request. The headers `x-ratelimit-remaining-requests` and `x-ratelimit-reset-requests` are the most accurate live indicators for leaky-bucket rotation logic when they are available.
