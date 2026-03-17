from typing import Any, Dict, List, Optional, Union
from pydantic import Field
import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
import stat

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
import logging

# MCP
from mcp.server.fastmcp import FastMCP

# Configure logging
_log_level = getattr(logging, os.environ.get("LOG_LEVEL", "WARNING").upper(), logging.WARNING)
logging.basicConfig(level=_log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('google_ads_server')

mcp = FastMCP(
    "google-ads-server",
    dependencies=[
        "google-auth-oauthlib",
        "google-auth",
        "requests",
        "python-dotenv"
    ]
)

# Constants and configuration
SCOPES = ['https://www.googleapis.com/auth/adwords']
API_VERSION = os.environ.get("GOOGLE_ADS_API_VERSION", "v22")  # Google Ads API version

# Load environment variables
try:
    from dotenv import load_dotenv
    # Load from .env file if it exists
    load_dotenv()
    logger.debug("Environment variables loaded from .env file")
except ImportError:
    logger.warning("python-dotenv not installed, skipping .env file loading")

# Get credentials from environment variables
GOOGLE_ADS_CREDENTIALS_PATH = os.environ.get("GOOGLE_ADS_CREDENTIALS_PATH")
GOOGLE_ADS_DEVELOPER_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
GOOGLE_ADS_LOGIN_CUSTOMER_ID = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
GOOGLE_ADS_AUTH_TYPE = os.environ.get("GOOGLE_ADS_AUTH_TYPE", "oauth")  # oauth or service_account

def format_customer_id(customer_id: str) -> str:
    """Format customer ID to ensure it's 10 digits without dashes."""
    # Convert to string if passed as integer or another type
    customer_id = str(customer_id)
    
    # Remove any quotes surrounding the customer_id (both escaped and unescaped)
    customer_id = customer_id.replace('\"', '').replace('"', '')
    
    # Remove any non-digit characters (including dashes, braces, etc.)
    customer_id = ''.join(char for char in customer_id if char.isdigit())
    
    # Ensure it's 10 digits with leading zeros if needed
    return customer_id.zfill(10)

def _sanitize_error(response_text: str) -> str:
    """Extract error message from API response without exposing raw response."""
    try:
        data = json.loads(response_text)
        if isinstance(data, list) and data:
            data = data[0]
        err = data.get("error", data)
        if isinstance(err, dict):
            msg = err.get("message", "Unknown error")
            code = err.get("code", "")
            return f"{code} {msg}".strip()
        return str(err)[:200]
    except (json.JSONDecodeError, AttributeError):
        return "API request failed (details hidden for security)"

def get_credentials():
    """
    Get and refresh OAuth credentials or service account credentials based on the auth type.
    
    This function supports two authentication methods:
    1. OAuth 2.0 (User Authentication) - For individual users or desktop applications
    2. Service Account (Server-to-Server Authentication) - For automated systems

    Returns:
        Valid credentials object to use with Google Ads API
    """
    if not GOOGLE_ADS_CREDENTIALS_PATH:
        raise ValueError("GOOGLE_ADS_CREDENTIALS_PATH environment variable not set")
    
    auth_type = GOOGLE_ADS_AUTH_TYPE.lower()
    logger.info(f"Using authentication type: {auth_type}")
    
    # Service Account authentication
    if auth_type == "service_account":
        try:
            return get_service_account_credentials()
        except Exception as e:
            logger.error(f"Error with service account authentication: {str(e)}")
            raise
    
    # OAuth 2.0 authentication (default)
    return get_oauth_credentials()

def get_service_account_credentials():
    """Get credentials using a service account key file."""
    logger.debug(f"Loading service account credentials from {GOOGLE_ADS_CREDENTIALS_PATH}")
    
    if not os.path.exists(GOOGLE_ADS_CREDENTIALS_PATH):
        raise FileNotFoundError(f"Service account key file not found at {GOOGLE_ADS_CREDENTIALS_PATH}")
    
    try:
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_ADS_CREDENTIALS_PATH, 
            scopes=SCOPES
        )
        
        # Check if impersonation is required
        impersonation_email = os.environ.get("GOOGLE_ADS_IMPERSONATION_EMAIL")
        if impersonation_email:
            logger.info(f"Impersonating user: {impersonation_email}")
            credentials = credentials.with_subject(impersonation_email)
            
        return credentials
        
    except Exception as e:
        logger.error(f"Error loading service account credentials: {str(e)}")
        raise

def get_oauth_credentials():
    """Get and refresh OAuth user credentials."""
    creds = None
    client_config = None
    
    # Path to store the refreshed token
    token_path = GOOGLE_ADS_CREDENTIALS_PATH
    if os.path.exists(token_path) and not os.path.basename(token_path).endswith('.json'):
        # If it's not explicitly a .json file, append a default name
        token_dir = os.path.dirname(token_path)
        token_path = os.path.join(token_dir, 'google_ads_token.json')
    
    # Check if token file exists and load credentials
    if os.path.exists(token_path):
        try:
            logger.debug(f"Loading OAuth credentials from {token_path}")
            with open(token_path, 'r') as f:
                creds_data = json.load(f)
                # Check if this is a client config or saved credentials
                if "installed" in creds_data or "web" in creds_data:
                    client_config = creds_data
                    logger.info("Found OAuth client configuration")
                else:
                    logger.info("Found existing OAuth token")
                    creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in token file: {token_path}")
            creds = None
        except Exception as e:
            logger.warning(f"Error loading credentials: {str(e)}")
            creds = None
    
    # If credentials don't exist or are invalid, get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing expired token")
                creds.refresh(Request())
                logger.info("Token successfully refreshed")
            except RefreshError as e:
                logger.warning(f"Error refreshing token: {str(e)}, will try to get new token")
                creds = None
            except Exception as e:
                logger.error(f"Unexpected error refreshing token: {str(e)}")
                raise
        
        # If we need new credentials
        if not creds:
            # If no client_config is defined yet, create one from environment variables
            if not client_config:
                logger.info("Creating OAuth client config from environment variables")
                client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID")
                client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET")
                
                if not client_id or not client_secret:
                    raise ValueError("GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET must be set if no client config file exists")
                
                client_config = {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
                    }
                }
            
            # Run the OAuth flow
            logger.info("Starting OAuth authentication flow")
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
            logger.info("OAuth flow completed successfully")
        
        # Save the refreshed/new credentials
        try:
            logger.debug(f"Saving credentials to {token_path}")
            # Ensure directory exists
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
            try:
                os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        except Exception as e:
            logger.warning(f"Could not save credentials: {str(e)}")
    
    return creds

def get_headers(creds):
    """Get headers for Google Ads API requests."""
    if not GOOGLE_ADS_DEVELOPER_TOKEN:
        raise ValueError("GOOGLE_ADS_DEVELOPER_TOKEN environment variable not set")
    
    # Handle different credential types
    if isinstance(creds, service_account.Credentials):
        # For service account, we need to get a new bearer token
        auth_req = Request()
        creds.refresh(auth_req)
        token = creds.token
    else:
        # For OAuth credentials, check if token needs refresh
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    logger.info("Refreshing expired OAuth token in get_headers")
                    creds.refresh(Request())
                    logger.info("Token successfully refreshed in get_headers")
                except RefreshError as e:
                    logger.error(f"Error refreshing token in get_headers: {str(e)}")
                    raise ValueError(f"Failed to refresh OAuth token: {str(e)}")
                except Exception as e:
                    logger.error(f"Unexpected error refreshing token in get_headers: {str(e)}")
                    raise
            else:
                raise ValueError("OAuth credentials are invalid and cannot be refreshed")
        
        token = creds.token
        
    headers = {
        'Authorization': f'Bearer {token}',
        'developer-token': GOOGLE_ADS_DEVELOPER_TOKEN,
        'content-type': 'application/json'
    }
    
    if GOOGLE_ADS_LOGIN_CUSTOMER_ID:
        headers['login-customer-id'] = format_customer_id(GOOGLE_ADS_LOGIN_CUSTOMER_ID)
    
    return headers

@mcp.tool()
async def list_accounts() -> str:
    """List all accessible Google Ads accounts."""
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers:listAccessibleCustomers"
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            return f"Error accessing accounts: {_sanitize_error(response.text)}"
        
        customers = response.json()
        if not customers.get('resourceNames'):
            return "No accessible accounts found."
        
        # Format the results
        result_lines = ["Accessible Google Ads Accounts:"]
        result_lines.append("-" * 50)
        
        for resource_name in customers['resourceNames']:
            customer_id = resource_name.split('/')[-1]
            formatted_id = format_customer_id(customer_id)
            result_lines.append(f"Account ID: {formatted_id}")
        
        return "\n".join(result_lines)
    
    except Exception as e:
        return f"Error listing accounts: {str(e)}"

@mcp.tool()
async def execute_gaql_query(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'"),
    query: str = Field(description="Valid GAQL query string following Google Ads Query Language syntax")
) -> str:
    """Execute a custom GAQL query and return formatted results."""
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error executing query: {_sanitize_error(response.text)}"
        
        results = response.json()
        if not results.get('results'):
            return "No results found for the query."
        
        # Format the results as a table
        result_lines = [f"Query Results for Account {formatted_customer_id}:"]
        result_lines.append("-" * 80)
        
        # Get field names from the first result
        fields = []
        first_result = results['results'][0]
        for key in first_result:
            if isinstance(first_result[key], dict):
                for subkey in first_result[key]:
                    fields.append(f"{key}.{subkey}")
            else:
                fields.append(key)
        
        # Add header
        result_lines.append(" | ".join(fields))
        result_lines.append("-" * 80)
        
        # Add data rows
        for result in results['results']:
            row_data = []
            for field in fields:
                if "." in field:
                    parent, child = field.split(".")
                    value = str(result.get(parent, {}).get(child, ""))
                else:
                    value = str(result.get(field, ""))
                row_data.append(value)
            result_lines.append(" | ".join(row_data))
        
        return "\n".join(result_lines)
    
    except Exception as e:
        return f"Error executing GAQL query: {str(e)}"

@mcp.tool()
async def get_campaign_performance(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'"),
    days: int = Field(default=30, description="Number of days to look back (7, 30, 90, etc.)")
) -> str:
    """Get campaign performance metrics (impressions, clicks, cost, conversions) for a time period."""
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.average_cpc
        FROM campaign
        WHERE segments.date DURING LAST_{days}_DAYS
        ORDER BY metrics.cost_micros DESC
        LIMIT 50
    """
    
    return await execute_gaql_query(customer_id, query)

@mcp.tool()
async def get_ad_performance(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'"),
    days: int = Field(default=30, description="Number of days to look back (7, 30, 90, etc.)")
) -> str:
    """Get ad-level performance metrics (impressions, clicks, cost, conversions) for a time period."""
    query = f"""
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.name,
            ad_group_ad.status,
            campaign.name,
            ad_group.name,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions
        FROM ad_group_ad
        WHERE segments.date DURING LAST_{days}_DAYS
        ORDER BY metrics.impressions DESC
        LIMIT 50
    """
    
    return await execute_gaql_query(customer_id, query)

@mcp.tool()
async def run_gaql(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'"),
    query: str = Field(description="Valid GAQL query string following Google Ads Query Language syntax"),
    format: str = Field(default="table", description="Output format: 'table', 'json', or 'csv'")
) -> str:
    """Run arbitrary GAQL with table/json/csv output."""
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error executing query: {_sanitize_error(response.text)}"
        
        results = response.json()
        if not results.get('results'):
            return "No results found for the query."
        
        if format.lower() == "json":
            return json.dumps(results, indent=2)
        
        elif format.lower() == "csv":
            # Get field names from the first result
            fields = []
            first_result = results['results'][0]
            for key, value in first_result.items():
                if isinstance(value, dict):
                    for subkey in value:
                        fields.append(f"{key}.{subkey}")
                else:
                    fields.append(key)
            
            # Create CSV string
            csv_lines = [",".join(fields)]
            for result in results['results']:
                row_data = []
                for field in fields:
                    if "." in field:
                        parent, child = field.split(".")
                        value = str(result.get(parent, {}).get(child, "")).replace(",", ";")
                    else:
                        value = str(result.get(field, "")).replace(",", ";")
                    row_data.append(value)
                csv_lines.append(",".join(row_data))
            
            return "\n".join(csv_lines)
        
        else:  # default table format
            result_lines = [f"Query Results for Account {formatted_customer_id}:"]
            result_lines.append("-" * 100)
            
            # Get field names and maximum widths
            fields = []
            field_widths = {}
            first_result = results['results'][0]
            
            for key, value in first_result.items():
                if isinstance(value, dict):
                    for subkey in value:
                        field = f"{key}.{subkey}"
                        fields.append(field)
                        field_widths[field] = len(field)
                else:
                    fields.append(key)
                    field_widths[key] = len(key)
            
            # Calculate maximum field widths
            for result in results['results']:
                for field in fields:
                    if "." in field:
                        parent, child = field.split(".")
                        value = str(result.get(parent, {}).get(child, ""))
                    else:
                        value = str(result.get(field, ""))
                    field_widths[field] = max(field_widths[field], len(value))
            
            # Create formatted header
            header = " | ".join(f"{field:{field_widths[field]}}" for field in fields)
            result_lines.append(header)
            result_lines.append("-" * len(header))
            
            # Add data rows
            for result in results['results']:
                row_data = []
                for field in fields:
                    if "." in field:
                        parent, child = field.split(".")
                        value = str(result.get(parent, {}).get(child, ""))
                    else:
                        value = str(result.get(field, ""))
                    row_data.append(f"{value:{field_widths[field]}}")
                result_lines.append(" | ".join(row_data))
            
            return "\n".join(result_lines)
    
    except Exception as e:
        return f"Error executing GAQL query: {str(e)}"

@mcp.tool()
async def get_ad_creatives(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'")
) -> str:
    """Get ad creative details (headlines, descriptions, URLs)."""
    query = """
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.name,
            ad_group_ad.ad.type,
            ad_group_ad.ad.final_urls,
            ad_group_ad.status,
            ad_group_ad.ad.responsive_search_ad.headlines,
            ad_group_ad.ad.responsive_search_ad.descriptions,
            ad_group.name,
            campaign.name
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
        ORDER BY campaign.name, ad_group.name
        LIMIT 50
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving ad creatives: {_sanitize_error(response.text)}"
        
        results = response.json()
        if not results.get('results'):
            return "No ad creatives found for this customer ID."
        
        # Format the results in a readable way
        output_lines = [f"Ad Creatives for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        for i, result in enumerate(results['results'], 1):
            ad = result.get('adGroupAd', {}).get('ad', {})
            ad_group = result.get('adGroup', {})
            campaign = result.get('campaign', {})
            
            output_lines.append(f"\n{i}. Campaign: {campaign.get('name', 'N/A')}")
            output_lines.append(f"   Ad Group: {ad_group.get('name', 'N/A')}")
            output_lines.append(f"   Ad ID: {ad.get('id', 'N/A')}")
            output_lines.append(f"   Ad Name: {ad.get('name', 'N/A')}")
            output_lines.append(f"   Status: {result.get('adGroupAd', {}).get('status', 'N/A')}")
            output_lines.append(f"   Type: {ad.get('type', 'N/A')}")
            
            # Handle Responsive Search Ads
            rsa = ad.get('responsiveSearchAd', {})
            if rsa:
                if 'headlines' in rsa:
                    output_lines.append("   Headlines:")
                    for headline in rsa['headlines']:
                        output_lines.append(f"     - {headline.get('text', 'N/A')}")
                
                if 'descriptions' in rsa:
                    output_lines.append("   Descriptions:")
                    for desc in rsa['descriptions']:
                        output_lines.append(f"     - {desc.get('text', 'N/A')}")
            
            # Handle Final URLs
            final_urls = ad.get('finalUrls', [])
            if final_urls:
                output_lines.append(f"   Final URLs: {', '.join(final_urls)}")
            
            output_lines.append("-" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving ad creatives: {str(e)}"

@mcp.tool()
async def get_account_currency(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'")
) -> str:
    """Get account default currency code."""
    query = """
        SELECT
            customer.id,
            customer.currency_code
        FROM customer
        LIMIT 1
    """
    
    try:
        creds = get_credentials()
        
        # Force refresh if needed
        if not creds.valid:
            logger.info("Credentials not valid, attempting refresh...")
            if hasattr(creds, 'refresh_token') and creds.refresh_token:
                creds.refresh(Request())
                logger.info("Credentials refreshed successfully")
            else:
                raise ValueError("Invalid credentials and no refresh token available")
        
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving account currency: {_sanitize_error(response.text)}"
        
        results = response.json()
        if not results.get('results'):
            return "No account information found for this customer ID."
        
        # Extract the currency code from the results
        customer = results['results'][0].get('customer', {})
        currency_code = customer.get('currencyCode', 'Not specified')
        
        return f"Account {formatted_customer_id} uses currency: {currency_code}"
    
    except Exception as e:
        logger.error(f"Error retrieving account currency: {str(e)}")
        return f"Error retrieving account currency: {str(e)}"

@mcp.resource("gaql://reference")
def gaql_reference() -> str:
    """Google Ads Query Language (GAQL) reference documentation."""
    return """
    # Google Ads Query Language (GAQL) Reference
    
    GAQL is similar to SQL but with specific syntax for Google Ads. Here's a quick reference:
    
    ## Basic Query Structure
    ```
    SELECT field1, field2, ... 
    FROM resource_type
    WHERE condition
    ORDER BY field [ASC|DESC]
    LIMIT n
    ```
    
    ## Common Field Types
    
    ### Resource Fields
    - campaign.id, campaign.name, campaign.status
    - ad_group.id, ad_group.name, ad_group.status
    - ad_group_ad.ad.id, ad_group_ad.ad.final_urls
    - keyword.text, keyword.match_type
    
    ### Metric Fields
    - metrics.impressions
    - metrics.clicks
    - metrics.cost_micros
    - metrics.conversions
    - metrics.ctr
    - metrics.average_cpc
    
    ### Segment Fields
    - segments.date
    - segments.device
    - segments.day_of_week
    
    ## Common WHERE Clauses
    
    ### Date Ranges
    - WHERE segments.date DURING LAST_7_DAYS
    - WHERE segments.date DURING LAST_30_DAYS
    - WHERE segments.date BETWEEN '2023-01-01' AND '2023-01-31'
    
    ### Filtering
    - WHERE campaign.status = 'ENABLED'
    - WHERE metrics.clicks > 100
    - WHERE campaign.name LIKE '%Brand%'
    
    ## Tips
    - Always check account currency before analyzing cost data
    - Cost values are in micros (millionths): 1000000 = 1 unit of currency
    - Use LIMIT to avoid large result sets
    """

@mcp.prompt("google_ads_workflow")
def google_ads_workflow() -> str:
    """Provides guidance on the recommended workflow for using Google Ads tools."""
    return """
    I'll help you analyze your Google Ads account data. Here's the recommended workflow:
    
    1. First, let's list all the accounts you have access to:
       - Run the `list_accounts()` tool to get available account IDs
    
    2. Before analyzing cost data, let's check which currency the account uses:
       - Run `get_account_currency(customer_id="ACCOUNT_ID")` with your selected account
    
    3. Now we can explore the account data:
       - For campaign performance: `get_campaign_performance(customer_id="ACCOUNT_ID", days=30)`
       - For ad performance: `get_ad_performance(customer_id="ACCOUNT_ID", days=30)`
       - For ad creative review: `get_ad_creatives(customer_id="ACCOUNT_ID")`
    
    4. For custom queries, use the GAQL query tool:
       - `run_gaql(customer_id="ACCOUNT_ID", query="YOUR_QUERY", format="table")`
    
    5. Let me know if you have specific questions about:
       - Campaign performance
       - Ad performance
       - Keywords
       - Budgets
       - Conversions
    
    Important: Always provide the customer_id as a string.
    For example: customer_id="1234567890"
    """

@mcp.prompt("gaql_help")
def gaql_help() -> str:
    """Provides assistance for writing GAQL queries."""
    return """
    I'll help you write a Google Ads Query Language (GAQL) query. Here are some examples to get you started:
    
    ## Get campaign performance last 30 days
    ```
    SELECT
      campaign.id,
      campaign.name,
      campaign.status,
      metrics.impressions,
      metrics.clicks,
      metrics.cost_micros,
      metrics.conversions
    FROM campaign
    WHERE segments.date DURING LAST_30_DAYS
    ORDER BY metrics.cost_micros DESC
    ```
    
    ## Get keyword performance
    ```
    SELECT
      keyword.text,
      keyword.match_type,
      metrics.impressions,
      metrics.clicks,
      metrics.cost_micros,
      metrics.conversions
    FROM keyword_view
    WHERE segments.date DURING LAST_30_DAYS
    ORDER BY metrics.clicks DESC
    ```
    
    ## Get ads with poor performance
    ```
    SELECT
      ad_group_ad.ad.id,
      ad_group_ad.ad.name,
      campaign.name,
      ad_group.name,
      metrics.impressions,
      metrics.clicks,
      metrics.conversions
    FROM ad_group_ad
    WHERE 
      segments.date DURING LAST_30_DAYS
      AND metrics.impressions > 1000
      AND metrics.ctr < 0.01
    ORDER BY metrics.impressions DESC
    ```
    
    Once you've chosen a query, use it with:
    ```
    run_gaql(customer_id="YOUR_ACCOUNT_ID", query="YOUR_QUERY_HERE")
    ```
    
    Remember:
    - Always provide the customer_id as a string
    - Cost values are in micros (1,000,000 = 1 unit of currency)
    - Use LIMIT to avoid large result sets
    - Check the account currency before analyzing cost data
    """

@mcp.tool()
async def get_image_assets(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'"),
    limit: int = Field(default=50, description="Maximum number of image assets to return")
) -> str:
    """List image assets with URLs, dimensions, and file sizes."""
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.type,
            asset.image_asset.full_size.url,
            asset.image_asset.full_size.height_pixels,
            asset.image_asset.full_size.width_pixels,
            asset.image_asset.file_size
        FROM
            asset
        WHERE
            asset.type = 'IMAGE'
        LIMIT {limit}
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving image assets: {_sanitize_error(response.text)}"
        
        results = response.json()
        if not results.get('results'):
            return "No image assets found for this customer ID."
        
        # Format the results in a readable way
        output_lines = [f"Image Assets for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        for i, result in enumerate(results['results'], 1):
            asset = result.get('asset', {})
            image_asset = asset.get('imageAsset', {})
            full_size = image_asset.get('fullSize', {})
            
            output_lines.append(f"\n{i}. Asset ID: {asset.get('id', 'N/A')}")
            output_lines.append(f"   Name: {asset.get('name', 'N/A')}")
            
            if full_size:
                output_lines.append(f"   Image URL: {full_size.get('url', 'N/A')}")
                output_lines.append(f"   Dimensions: {full_size.get('widthPixels', 'N/A')} x {full_size.get('heightPixels', 'N/A')} px")
            
            file_size = image_asset.get('fileSize', 'N/A')
            if file_size != 'N/A':
                # Convert to KB for readability
                file_size_kb = int(file_size) / 1024
                output_lines.append(f"   File Size: {file_size_kb:.2f} KB")
            
            output_lines.append("-" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving image assets: {str(e)}"

@mcp.tool()
async def download_image_asset(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'"),
    asset_id: str = Field(description="The ID of the image asset to download"),
    output_dir: str = Field(default="./ad_images", description="Directory to save the downloaded image")
) -> str:
    """Download a specific image asset by ID to a local directory."""
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.image_asset.full_size.url
        FROM
            asset
        WHERE
            asset.type = 'IMAGE'
            AND asset.id = {asset_id}
        LIMIT 1
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving image asset: {_sanitize_error(response.text)}"
        
        results = response.json()
        if not results.get('results'):
            return f"No image asset found with ID {asset_id}"
        
        # Extract the image URL
        asset = results['results'][0].get('asset', {})
        image_url = asset.get('imageAsset', {}).get('fullSize', {}).get('url')
        asset_name = asset.get('name', f"image_{asset_id}")
        
        if not image_url:
            return f"No download URL found for image asset ID {asset_id}"
        
        # Validate and sanitize the output directory to prevent path traversal
        try:
            # Get the base directory (current working directory)
            base_dir = Path.cwd()
            # Resolve the output directory to an absolute path
            resolved_output_dir = Path(output_dir).resolve()
            
            # Ensure the resolved path is within or under the current working directory
            # This prevents path traversal attacks like "../../../etc"
            try:
                resolved_output_dir.relative_to(base_dir)
            except ValueError:
                # If the path is not relative to base_dir, use the default safe directory
                resolved_output_dir = base_dir / "ad_images"
                logger.warning(f"Invalid output directory '{output_dir}' - using default './ad_images'")
            
            # Create output directory if it doesn't exist
            resolved_output_dir.mkdir(parents=True, exist_ok=True)
            
        except Exception as e:
            return f"Error creating output directory: {str(e)}"
        
        # Download the image
        image_response = requests.get(image_url)
        if image_response.status_code != 200:
            return f"Failed to download image: HTTP {image_response.status_code}"
        
        # Clean the filename to be safe for filesystem
        safe_name = ''.join(c for c in asset_name if c.isalnum() or c in ' ._-')
        filename = f"{asset_id}_{safe_name}.jpg"
        file_path = resolved_output_dir / filename
        
        # Save the image
        with open(file_path, 'wb') as f:
            f.write(image_response.content)
        
        return f"Successfully downloaded image asset {asset_id} to {file_path}"
    
    except Exception as e:
        return f"Error downloading image asset: {str(e)}"

@mcp.tool()
async def get_asset_usage(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'"),
    asset_id: str = Field(default=None, description="Optional: specific asset ID to look up (leave empty to get all image assets)"),
    asset_type: str = Field(default="IMAGE", description="Asset type to search for ('IMAGE', 'TEXT', 'VIDEO', etc.)")
) -> str:
    """Find where assets are used across campaigns and ad groups."""
    # Build the query based on whether a specific asset ID was provided
    where_clause = f"asset.type = '{asset_type}'"
    if asset_id:
        where_clause += f" AND asset.id = {asset_id}"
    
    # First get the assets themselves
    assets_query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.type
        FROM
            asset
        WHERE
            {where_clause}
        LIMIT 100
    """
    
    # Then get the associations between assets and campaigns/ad groups
    # Try using campaign_asset instead of asset_link
    associations_query = f"""
        SELECT
            campaign.id,
            campaign.name,
            asset.id,
            asset.name,
            asset.type
        FROM
            campaign_asset
        WHERE
            {where_clause}
        LIMIT 500
    """

    # Also try ad_group_asset for ad group level information
    ad_group_query = f"""
        SELECT
            ad_group.id,
            ad_group.name,
            asset.id,
            asset.name,
            asset.type
        FROM
            ad_group_asset
        WHERE
            {where_clause}
        LIMIT 500
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        
        # First get the assets
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        payload = {"query": assets_query}
        assets_response = requests.post(url, headers=headers, json=payload)
        
        if assets_response.status_code != 200:
            return f"Error retrieving assets: {_sanitize_error(assets_response.text)}"
        
        assets_results = assets_response.json()
        if not assets_results.get('results'):
            return f"No {asset_type} assets found for this customer ID."
        
        # Now get the associations
        payload = {"query": associations_query}
        assoc_response = requests.post(url, headers=headers, json=payload)
        
        if assoc_response.status_code != 200:
            return f"Error retrieving asset associations: {_sanitize_error(assoc_response.text)}"
        
        assoc_results = assoc_response.json()
        
        # Format the results in a readable way
        output_lines = [f"Asset Usage for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        # Create a dictionary to organize asset usage by asset ID
        asset_usage = {}
        
        # Initialize the asset usage dictionary with basic asset info
        for result in assets_results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            if asset_id:
                asset_usage[asset_id] = {
                    'name': asset.get('name', 'Unnamed asset'),
                    'type': asset.get('type', 'Unknown'),
                    'usage': []
                }
        
        # Add usage information from the associations
        for result in assoc_results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            
            if asset_id and asset_id in asset_usage:
                campaign = result.get('campaign', {})
                ad_group = result.get('adGroup', {})
                ad = result.get('adGroupAd', {}).get('ad', {}) if 'adGroupAd' in result else {}
                asset_link = result.get('assetLink', {})
                
                usage_info = {
                    'campaign_id': campaign.get('id', 'N/A'),
                    'campaign_name': campaign.get('name', 'N/A'),
                    'ad_group_id': ad_group.get('id', 'N/A'),
                    'ad_group_name': ad_group.get('name', 'N/A'),
                    'ad_id': ad.get('id', 'N/A') if ad else 'N/A',
                    'ad_name': ad.get('name', 'N/A') if ad else 'N/A'
                }
                
                asset_usage[asset_id]['usage'].append(usage_info)
        
        # Format the output
        for asset_id, info in asset_usage.items():
            output_lines.append(f"\nAsset ID: {asset_id}")
            output_lines.append(f"Name: {info['name']}")
            output_lines.append(f"Type: {info['type']}")
            
            if info['usage']:
                output_lines.append("\nUsed in:")
                output_lines.append("-" * 60)
                output_lines.append(f"{'Campaign':<30} | {'Ad Group':<30}")
                output_lines.append("-" * 60)
                
                for usage in info['usage']:
                    campaign_str = f"{usage['campaign_name']} ({usage['campaign_id']})"
                    ad_group_str = f"{usage['ad_group_name']} ({usage['ad_group_id']})"
                    
                    output_lines.append(f"{campaign_str[:30]:<30} | {ad_group_str[:30]:<30}")
            
            output_lines.append("=" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving asset usage: {str(e)}"

@mcp.tool()
async def analyze_image_assets(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'"),
    days: int = Field(default=30, description="Number of days to look back (7, 30, 90, etc.)")
) -> str:
    """Analyze image asset performance metrics (impressions, clicks, conversions) across campaigns."""
    # Make sure to use a valid date range format
    # Valid formats are: LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, etc. (with underscores)
    if days == 7:
        date_range = "LAST_7_DAYS"
    elif days == 14:
        date_range = "LAST_14_DAYS"
    elif days == 30:
        date_range = "LAST_30_DAYS"
    else:
        # Default to 30 days if not a standard range
        date_range = "LAST_30_DAYS"
        
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.image_asset.full_size.url,
            asset.image_asset.full_size.width_pixels,
            asset.image_asset.full_size.height_pixels,
            campaign.name,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.cost_micros
        FROM
            campaign_asset
        WHERE
            asset.type = 'IMAGE'
            AND segments.date DURING LAST_30_DAYS
        ORDER BY
            metrics.impressions DESC
        LIMIT 200
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error analyzing image assets: {_sanitize_error(response.text)}"
        
        results = response.json()
        if not results.get('results'):
            return "No image asset performance data found for this customer ID and time period."
        
        # Group results by asset ID
        assets_data = {}
        for result in results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            
            if asset_id not in assets_data:
                assets_data[asset_id] = {
                    'name': asset.get('name', f"Asset {asset_id}"),
                    'url': asset.get('imageAsset', {}).get('fullSize', {}).get('url', 'N/A'),
                    'dimensions': f"{asset.get('imageAsset', {}).get('fullSize', {}).get('widthPixels', 'N/A')} x {asset.get('imageAsset', {}).get('fullSize', {}).get('heightPixels', 'N/A')}",
                    'impressions': 0,
                    'clicks': 0,
                    'conversions': 0,
                    'cost_micros': 0,
                    'campaigns': set(),
                    'ad_groups': set()
                }
            
            # Aggregate metrics
            metrics = result.get('metrics', {})
            assets_data[asset_id]['impressions'] += int(metrics.get('impressions', 0))
            assets_data[asset_id]['clicks'] += int(metrics.get('clicks', 0))
            assets_data[asset_id]['conversions'] += float(metrics.get('conversions', 0))
            assets_data[asset_id]['cost_micros'] += int(metrics.get('costMicros', 0))
            
            # Add campaign and ad group info
            campaign = result.get('campaign', {})
            ad_group = result.get('adGroup', {})
            
            if campaign.get('name'):
                assets_data[asset_id]['campaigns'].add(campaign.get('name'))
            if ad_group.get('name'):
                assets_data[asset_id]['ad_groups'].add(ad_group.get('name'))
        
        # Format the results
        output_lines = [f"Image Asset Performance Analysis for Customer ID {formatted_customer_id} (Last {days} days):"]
        output_lines.append("=" * 100)
        
        # Sort assets by impressions (highest first)
        sorted_assets = sorted(assets_data.items(), key=lambda x: x[1]['impressions'], reverse=True)
        
        for asset_id, data in sorted_assets:
            output_lines.append(f"\nAsset ID: {asset_id}")
            output_lines.append(f"Name: {data['name']}")
            output_lines.append(f"Dimensions: {data['dimensions']}")
            
            # Calculate CTR if there are impressions
            ctr = (data['clicks'] / data['impressions'] * 100) if data['impressions'] > 0 else 0
            
            # Format metrics
            output_lines.append(f"\nPerformance Metrics:")
            output_lines.append(f"  Impressions: {data['impressions']:,}")
            output_lines.append(f"  Clicks: {data['clicks']:,}")
            output_lines.append(f"  CTR: {ctr:.2f}%")
            output_lines.append(f"  Conversions: {data['conversions']:.2f}")
            output_lines.append(f"  Cost (micros): {data['cost_micros']:,}")
            
            # Show where it's used
            output_lines.append(f"\nUsed in {len(data['campaigns'])} campaigns:")
            for campaign in list(data['campaigns'])[:5]:  # Show first 5 campaigns
                output_lines.append(f"  - {campaign}")
            if len(data['campaigns']) > 5:
                output_lines.append(f"  - ... and {len(data['campaigns']) - 5} more")
            
            # Add URL
            if data['url'] != 'N/A':
                output_lines.append(f"\nImage URL: {data['url']}")
            
            output_lines.append("-" * 100)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error analyzing image assets: {str(e)}"

@mcp.tool()
async def list_resources(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '1234567890'")
) -> str:
    """List valid GAQL resource types for FROM clauses."""
    # Example query that lists some common resources
    # This might need to be adjusted based on what's available in your API version
    query = """
        SELECT
            google_ads_field.name,
            google_ads_field.category,
            google_ads_field.data_type
        FROM
            google_ads_field
        WHERE
            google_ads_field.category = 'RESOURCE'
        ORDER BY
            google_ads_field.name
    """
    
    # Use your existing run_gaql function to execute this query
    return await run_gaql(customer_id, query)

# ============================================================
# MUTATE (WRITE) OPERATIONS
# ============================================================

def mutate_google_ads(customer_id: str, endpoint: str, operations: list) -> dict:
    """
    Common helper for Google Ads API mutate (write) operations.

    Args:
        customer_id: Google Ads customer ID
        endpoint: API endpoint path (e.g., 'sharedCriteria', 'adGroups')
        operations: List of operation dicts

    Returns:
        API response as dict
    """
    creds = get_credentials()
    headers = get_headers(creds)
    formatted_id = format_customer_id(customer_id)

    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_id}/{endpoint}:mutate"
    payload = {"operations": operations}

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        raise Exception(f"Mutate failed: {_sanitize_error(response.text)}")

    return response.json()


@mcp.tool()
async def add_negative_keywords(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    shared_set_id: str = Field(description="Shared set ID for the negative keyword list"),
    keywords: str = Field(description="Comma-separated keywords to add as negatives. Example: '寿命,年数,放置'"),
    match_type: str = Field(default="BROAD", description="Match type: BROAD, PHRASE, or EXACT"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Add negative keywords to a shared negative keyword list (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        keyword_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
        match_type_upper = match_type.upper()

        if match_type_upper not in ["BROAD", "PHRASE", "EXACT"]:
            return "Error: match_type must be BROAD, PHRASE, or EXACT"

        # Build operations
        operations = []
        for kw in keyword_list:
            operations.append({
                "create": {
                    "sharedSet": f"customers/{formatted_id}/sharedSets/{shared_set_id}",
                    "keyword": {
                        "text": kw,
                        "matchType": match_type_upper
                    }
                }
            })

        # Preview
        preview_lines = [f"=== Negative Keyword Addition Preview ==="]
        preview_lines.append(f"Shared Set ID: {shared_set_id}")
        preview_lines.append(f"Match Type: {match_type_upper}")
        preview_lines.append(f"Keywords to add ({len(keyword_list)}):")
        for kw in keyword_list:
            preview_lines.append(f"  - {kw}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        # Execute
        result = mutate_google_ads(formatted_id, "sharedCriteria", operations)

        added_count = len(result.get("results", []))
        preview_lines.append(f"\n✓ Successfully added {added_count} negative keywords.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error adding negative keywords: {str(e)}"


@mcp.tool()
async def remove_negative_keywords(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    shared_set_id: str = Field(description="Shared set ID for the negative keyword list"),
    criterion_ids: str = Field(description="Comma-separated criterion IDs to remove. Get these from querying shared_criterion."),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Remove negative keywords from a shared list by criterion IDs (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        id_list = [cid.strip() for cid in criterion_ids.split(",") if cid.strip()]

        # Build remove operations
        operations = []
        for cid in id_list:
            operations.append({
                "remove": f"customers/{formatted_id}/sharedCriteria/{shared_set_id}~{cid}"
            })

        # Preview
        preview_lines = [f"=== Negative Keyword Removal Preview ==="]
        preview_lines.append(f"Shared Set ID: {shared_set_id}")
        preview_lines.append(f"Criterion IDs to remove ({len(id_list)}):")
        for cid in id_list:
            preview_lines.append(f"  - {cid}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        # Execute
        result = mutate_google_ads(formatted_id, "sharedCriteria", operations)

        removed_count = len(result.get("results", []))
        preview_lines.append(f"\n✓ Successfully removed {removed_count} negative keywords.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error removing negative keywords: {str(e)}"


@mcp.tool()
async def add_keywords(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    ad_group_id: str = Field(description="Ad group ID to add keywords to"),
    keywords: str = Field(description="Comma-separated keywords to add. Example: '引越し 見積もり,引越し 格安'"),
    match_type: str = Field(default="PHRASE", description="Match type: BROAD, PHRASE, or EXACT"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Add keywords to an ad group (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        keyword_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
        match_type_upper = match_type.upper()

        if match_type_upper not in ["BROAD", "PHRASE", "EXACT"]:
            return "Error: match_type must be BROAD, PHRASE, or EXACT"

        operations = []
        for kw in keyword_list:
            operations.append({
                "create": {
                    "adGroup": f"customers/{formatted_id}/adGroups/{ad_group_id}",
                    "status": "ENABLED",
                    "keyword": {
                        "text": kw,
                        "matchType": match_type_upper
                    }
                }
            })

        preview_lines = [f"=== Keyword Addition Preview ==="]
        preview_lines.append(f"Ad Group ID: {ad_group_id}")
        preview_lines.append(f"Match Type: {match_type_upper}")
        preview_lines.append(f"Keywords to add ({len(keyword_list)}):")
        for kw in keyword_list:
            preview_lines.append(f"  - {kw}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "adGroupCriteria", operations)

        added_count = len(result.get("results", []))
        preview_lines.append(f"\n✓ Successfully added {added_count} keywords.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error adding keywords: {str(e)}"


@mcp.tool()
async def remove_keyword(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    ad_group_id: str = Field(description="Ad group ID containing the keyword"),
    criterion_id: str = Field(description="Criterion ID of the keyword to remove"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Remove a keyword from an ad group by criterion ID (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        resource_name = f"customers/{formatted_id}/adGroupCriteria/{ad_group_id}~{criterion_id}"

        operations = [{"remove": resource_name}]

        preview_lines = [f"=== Keyword Removal Preview ==="]
        preview_lines.append(f"Resource: {resource_name}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "adGroupCriteria", operations)
        preview_lines.append(f"\n✓ Successfully removed keyword.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error removing keyword: {str(e)}"


@mcp.tool()
async def update_keyword_bids(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    ad_group_id: str = Field(description="Ad group ID containing the keywords"),
    criterion_ids: str = Field(description="Comma-separated criterion IDs of keywords to update. Use 'ALL' to update all enabled keywords in the ad group."),
    cpc_bid: float = Field(description="CPC bid amount in account currency units (e.g. 350 for ¥350)"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Update CPC bids for keywords in an ad group; use 'ALL' for all enabled keywords (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        bid_micros = str(int(cpc_bid * 1_000_000))

        # If 'ALL', fetch all enabled keywords in the ad group
        if criterion_ids.strip().upper() == "ALL":
            creds = get_credentials()
            headers = get_headers(creds)
            query = (
                f"SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text "
                f"FROM ad_group_criterion "
                f"WHERE ad_group.id = {ad_group_id} "
                f"AND ad_group_criterion.type = 'KEYWORD' "
                f"AND ad_group_criterion.status = 'ENABLED'"
            )
            search_url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_id}/googleAds:searchStream"
            resp = requests.post(search_url, headers=headers, json={"query": query})
            if resp.status_code != 200:
                return f"Error fetching keywords: {resp.text}"
            data = resp.json()
            id_list = []
            kw_names = {}
            for batch in data:
                for row in batch.get("results", []):
                    cid = row.get("adGroupCriterion", {}).get("criterionId")
                    kw_text = row.get("adGroupCriterion", {}).get("keyword", {}).get("text", "")
                    if cid:
                        id_list.append(str(cid))
                        kw_names[str(cid)] = kw_text
            if not id_list:
                return f"No enabled keywords found in ad group {ad_group_id}."
        else:
            id_list = [cid.strip() for cid in criterion_ids.split(",") if cid.strip()]
            kw_names = {}

        operations = []
        for cid in id_list:
            resource_name = f"customers/{formatted_id}/adGroupCriteria/{ad_group_id}~{cid}"
            operations.append({
                "updateMask": "cpcBidMicros",
                "update": {
                    "resourceName": resource_name,
                    "cpcBidMicros": bid_micros
                }
            })

        preview_lines = [f"=== Keyword Bid Update Preview ==="]
        preview_lines.append(f"Ad Group ID: {ad_group_id}")
        preview_lines.append(f"CPC Bid: ¥{cpc_bid:,.0f}")
        preview_lines.append(f"Keywords to update ({len(id_list)}):")
        for cid in id_list:
            name = kw_names.get(cid, "")
            if name:
                preview_lines.append(f"  - {name} (criterion: {cid})")
            else:
                preview_lines.append(f"  - criterion: {cid}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "adGroupCriteria", operations)
        updated_count = len(result.get("results", []))
        preview_lines.append(f"\n✓ Successfully updated {updated_count} keyword bids to ¥{cpc_bid:,.0f}.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error updating keyword bids: {str(e)}"


@mcp.tool()
async def pause_enable_ad_group(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    ad_group_id: str = Field(description="Ad group ID to pause or enable"),
    action: str = Field(description="Action: 'PAUSE' or 'ENABLE'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Pause or enable an ad group (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        action_upper = action.upper()

        if action_upper == "PAUSE":
            status = "PAUSED"
        elif action_upper == "ENABLE":
            status = "ENABLED"
        else:
            return "Error: action must be 'PAUSE' or 'ENABLE'"

        resource_name = f"customers/{formatted_id}/adGroups/{ad_group_id}"

        operations = [{
            "updateMask": "status",
            "update": {
                "resourceName": resource_name,
                "status": status
            }
        }]

        preview_lines = [f"=== Ad Group Status Change Preview ==="]
        preview_lines.append(f"Ad Group ID: {ad_group_id}")
        preview_lines.append(f"New Status: {status}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "adGroups", operations)
        preview_lines.append(f"\n✓ Ad group status changed to {status}.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error changing ad group status: {str(e)}"


@mcp.tool()
async def update_ad_status(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    ad_group_id: str = Field(description="Ad group ID containing the ad"),
    ad_id: str = Field(description="Ad ID to pause or enable"),
    action: str = Field(description="Action: 'PAUSE' or 'ENABLE'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Pause or enable an individual ad (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        action_upper = action.upper()

        if action_upper == "PAUSE":
            status = "PAUSED"
        elif action_upper == "ENABLE":
            status = "ENABLED"
        else:
            return "Error: action must be 'PAUSE' or 'ENABLE'"

        resource_name = f"customers/{formatted_id}/adGroupAds/{ad_group_id}~{ad_id}"

        operations = [{
            "updateMask": "status",
            "update": {
                "resourceName": resource_name,
                "status": status
            }
        }]

        preview_lines = [f"=== Ad Status Change Preview ==="]
        preview_lines.append(f"Ad Group ID: {ad_group_id}")
        preview_lines.append(f"Ad ID: {ad_id}")
        preview_lines.append(f"New Status: {status}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "adGroupAds", operations)
        preview_lines.append(f"\n✓ Ad status changed to {status}.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error changing ad status: {str(e)}"


@mcp.tool()
async def edit_responsive_search_ad(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    ad_group_id: str = Field(description="Ad group ID containing the ad"),
    ad_id: str = Field(description="Ad ID of the existing RSA to edit"),
    headlines: str = Field(description="Pipe-separated headlines (min 3, max 15). Example: '格安引越しサービス|24時間365日対応|追加料金なし明朗会計'"),
    descriptions: str = Field(description="Pipe-separated descriptions (min 2, max 4). Example: '全国対応の引越しサービス。明朗会計で追加請求なし。|経験豊富なスタッフが丁寧に対応いたします。'"),
    final_url: str = Field(default=None, description="Optional: new landing page URL. If not provided, keeps the existing URL."),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Edit an existing RSA's headlines and descriptions in place (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)

        headline_list = [h.strip() for h in headlines.split("|") if h.strip()]
        description_list = [d.strip() for d in descriptions.split("|") if d.strip()]

        if len(headline_list) < 3:
            return "Error: At least 3 headlines required."
        if len(headline_list) > 15:
            return "Error: Maximum 15 headlines allowed."
        if len(description_list) < 2:
            return "Error: At least 2 descriptions required."
        if len(description_list) > 4:
            return "Error: Maximum 4 descriptions allowed."

        for i, h in enumerate(headline_list):
            if len(h) > 30:
                return f"Error: Headline {i+1} exceeds 30 characters ({len(h)} chars): '{h}'"
        for i, d in enumerate(description_list):
            if len(d) > 90:
                return f"Error: Description {i+1} exceeds 90 characters ({len(d)} chars): '{d}'"

        headline_assets = [{"text": h} for h in headline_list]
        description_assets = [{"text": d} for d in description_list]

        resource_name = f"customers/{formatted_id}/ads/{ad_id}"

        update_mask_fields = [
            "responsive_search_ad.headlines",
            "responsive_search_ad.descriptions"
        ]

        ad_object = {
            "resourceName": resource_name,
            "responsiveSearchAd": {
                "headlines": headline_assets,
                "descriptions": description_assets
            }
        }

        if final_url:
            update_mask_fields.append("final_urls")
            ad_object["finalUrls"] = [final_url]

        operations = [{
            "updateMask": ",".join(update_mask_fields),
            "update": ad_object
        }]

        # Preview
        preview_lines = [f"=== RSA Edit Preview ==="]
        preview_lines.append(f"Ad Group ID: {ad_group_id}")
        preview_lines.append(f"Ad ID: {ad_id} (editing in place)")
        if final_url:
            preview_lines.append(f"Final URL: {final_url}")
        preview_lines.append(f"\nHeadlines ({len(headline_list)}):")
        for i, h in enumerate(headline_list):
            preview_lines.append(f"  {i+1}. {h} ({len(h)} chars)")
        preview_lines.append(f"\nDescriptions ({len(description_list)}):")
        for i, d in enumerate(description_list):
            preview_lines.append(f"  {i+1}. {d} ({len(d)} chars)")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "ads", operations)
        preview_lines.append(f"\n✓ RSA updated successfully. Ad ID {ad_id} preserved.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error editing RSA: {str(e)}"


@mcp.tool()
async def create_responsive_search_ad(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    ad_group_id: str = Field(description="Ad group ID to create the ad in"),
    headlines: str = Field(description="Pipe-separated headlines (min 3, max 15). Example: '格安引越しサービス|24時間365日対応|追加料金なし明朗会計'"),
    descriptions: str = Field(description="Pipe-separated descriptions (min 2, max 4). Example: '全国対応の引越しサービス。明朗会計で追加請求なし。|経験豊富なスタッフが丁寧に対応いたします。'"),
    final_url: str = Field(description="Landing page URL. Example: 'https://example.com/'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Create a new Responsive Search Ad in an ad group (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)

        headline_list = [h.strip() for h in headlines.split("|") if h.strip()]
        description_list = [d.strip() for d in descriptions.split("|") if d.strip()]

        if len(headline_list) < 3:
            return "Error: At least 3 headlines required."
        if len(headline_list) > 15:
            return "Error: Maximum 15 headlines allowed."
        if len(description_list) < 2:
            return "Error: At least 2 descriptions required."
        if len(description_list) > 4:
            return "Error: Maximum 4 descriptions allowed."

        # Check character limits
        for i, h in enumerate(headline_list):
            if len(h) > 30:
                return f"Error: Headline {i+1} exceeds 30 characters ({len(h)} chars): '{h}'"
        for i, d in enumerate(description_list):
            if len(d) > 90:
                return f"Error: Description {i+1} exceeds 90 characters ({len(d)} chars): '{d}'"

        headline_assets = [{"text": h} for h in headline_list]
        description_assets = [{"text": d} for d in description_list]

        operations = [{
            "create": {
                "adGroup": f"customers/{formatted_id}/adGroups/{ad_group_id}",
                "status": "ENABLED",
                "ad": {
                    "responsiveSearchAd": {
                        "headlines": headline_assets,
                        "descriptions": description_assets
                    },
                    "finalUrls": [final_url]
                }
            }
        }]

        # Preview
        preview_lines = [f"=== RSA Creation Preview ==="]
        preview_lines.append(f"Ad Group ID: {ad_group_id}")
        preview_lines.append(f"Final URL: {final_url}")
        preview_lines.append(f"\nHeadlines ({len(headline_list)}):")
        for i, h in enumerate(headline_list):
            preview_lines.append(f"  {i+1}. {h} ({len(h)} chars)")
        preview_lines.append(f"\nDescriptions ({len(description_list)}):")
        for i, d in enumerate(description_list):
            preview_lines.append(f"  {i+1}. {d} ({len(d)} chars)")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "adGroupAds", operations)

        ad_resource = result.get("results", [{}])[0].get("resourceName", "")
        preview_lines.append(f"\n✓ RSA created successfully.")
        preview_lines.append(f"Resource: {ad_resource}")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error creating RSA: {str(e)}"


@mcp.tool()
async def add_callout_extensions(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(description="Campaign ID to attach callouts to"),
    callout_texts: str = Field(description="Pipe-separated callout texts. Example: '24時間365日対応|出張費無料|一律固定料金|カード決済対応'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Create and attach callout extensions to a campaign (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        callouts = [c.strip() for c in callout_texts.split("|") if c.strip()]

        for i, c in enumerate(callouts):
            if len(c) > 25:
                return f"Error: Callout {i+1} exceeds 25 characters ({len(c)} chars): '{c}'"

        preview_lines = [f"=== Callout Extension Preview ==="]
        preview_lines.append(f"Campaign ID: {campaign_id}")
        preview_lines.append(f"Callouts ({len(callouts)}):")
        for c in callouts:
            preview_lines.append(f"  - {c} ({len(c)} chars)")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        # Step 1: Create callout assets
        asset_operations = []
        for c in callouts:
            asset_operations.append({
                "create": {
                    "calloutAsset": {
                        "calloutText": c
                    }
                }
            })

        asset_result = mutate_google_ads(formatted_id, "assets", asset_operations)
        asset_resource_names = [r.get("resourceName", "") for r in asset_result.get("results", [])]

        # Step 2: Link assets to campaign
        link_operations = []
        for asset_rn in asset_resource_names:
            link_operations.append({
                "create": {
                    "campaign": f"customers/{formatted_id}/campaigns/{campaign_id}",
                    "asset": asset_rn,
                    "fieldType": "CALLOUT"
                }
            })

        mutate_google_ads(formatted_id, "campaignAssets", link_operations)

        preview_lines.append(f"\n✓ Successfully created and linked {len(callouts)} callout extensions.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error adding callout extensions: {str(e)}"


@mcp.tool()
async def add_structured_snippets(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(description="Campaign ID to attach structured snippets to"),
    header: str = Field(description="Snippet header. Options: サービス, ブランド, コース, 学位プログラム, 到着地, モデル, 地域, スタイル, タイプ, 設備, 番組, おすすめのホテル"),
    values: str = Field(description="Pipe-separated snippet values. Example: '単身引越し|家族引越し|オフィス移転|長距離引越し'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Create and attach a structured snippet extension to a campaign (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        value_list = [v.strip() for v in values.split("|") if v.strip()]

        if len(value_list) < 3:
            return "Error: At least 3 values required for structured snippets."

        preview_lines = [f"=== Structured Snippet Preview ==="]
        preview_lines.append(f"Campaign ID: {campaign_id}")
        preview_lines.append(f"Header: {header}")
        preview_lines.append(f"Values ({len(value_list)}):")
        for v in value_list:
            preview_lines.append(f"  - {v}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        # Step 1: Create structured snippet asset
        asset_operations = [{
            "create": {
                "structuredSnippetAsset": {
                    "header": header,
                    "values": value_list
                }
            }
        }]

        asset_result = mutate_google_ads(formatted_id, "assets", asset_operations)
        asset_rn = asset_result.get("results", [{}])[0].get("resourceName", "")

        # Step 2: Link to campaign
        link_operations = [{
            "create": {
                "campaign": f"customers/{formatted_id}/campaigns/{campaign_id}",
                "asset": asset_rn,
                "fieldType": "STRUCTURED_SNIPPET"
            }
        }]

        mutate_google_ads(formatted_id, "campaignAssets", link_operations)

        preview_lines.append(f"\n✓ Successfully created and linked structured snippet.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error adding structured snippets: {str(e)}"


# ============================================================
# INTERNAL HELPER: GAQL query (non-tool, for internal use)
# ============================================================

def _internal_gaql_search(customer_id: str, query: str) -> list:
    """
    Execute a GAQL query internally and return raw results list.
    Used by other tools that need to look up resource info before mutating.
    """
    creds = get_credentials()
    headers = get_headers(creds)
    formatted_id = format_customer_id(customer_id)

    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_id}/googleAds:search"
    payload = {"query": query}
    response = requests.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        raise Exception(f"GAQL query failed: {_sanitize_error(response.text)}")

    return response.json().get("results", [])


# ============================================================
# #1 CAMPAIGN PAUSE / ENABLE
# ============================================================

@mcp.tool()
async def pause_enable_campaign(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(description="Campaign ID to pause or enable"),
    action: str = Field(description="Action: 'PAUSE' or 'ENABLE'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Pause or enable a campaign (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        action_upper = action.upper()

        if action_upper == "PAUSE":
            status = "PAUSED"
        elif action_upper == "ENABLE":
            status = "ENABLED"
        else:
            return "Error: action must be 'PAUSE' or 'ENABLE'"

        resource_name = f"customers/{formatted_id}/campaigns/{campaign_id}"

        operations = [{
            "updateMask": "status",
            "update": {
                "resourceName": resource_name,
                "status": status
            }
        }]

        preview_lines = [f"=== Campaign Status Change Preview ==="]
        preview_lines.append(f"Campaign ID: {campaign_id}")
        preview_lines.append(f"New Status: {status}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "campaigns", operations)
        preview_lines.append(f"\n✓ Campaign status changed to {status}.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error changing campaign status: {str(e)}"


# ============================================================
# #2 UPDATE CAMPAIGN BUDGET
# ============================================================

@mcp.tool()
async def update_campaign_budget(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(description="Campaign ID whose budget to change"),
    new_budget: float = Field(description="New daily budget amount in account currency (e.g. 17000 for ¥17,000 or 50.00 for $50)"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Update a campaign's daily budget in currency units (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)

        # Look up the campaign's budget resource name
        query = f"""
            SELECT campaign.campaign_budget, campaign.name
            FROM campaign
            WHERE campaign.id = {campaign_id}
        """
        results = _internal_gaql_search(formatted_id, query)

        if not results:
            return f"Error: Campaign {campaign_id} not found."

        campaign_data = results[0].get("campaign", {})
        budget_resource = campaign_data.get("campaignBudget", "")
        campaign_name = campaign_data.get("name", "Unknown")

        if not budget_resource:
            return f"Error: Could not find budget resource for campaign {campaign_id}."

        # Convert to micros
        amount_micros = int(new_budget * 1_000_000)

        operations = [{
            "updateMask": "amountMicros",
            "update": {
                "resourceName": budget_resource,
                "amountMicros": str(amount_micros)
            }
        }]

        preview_lines = [f"=== Campaign Budget Change Preview ==="]
        preview_lines.append(f"Campaign: {campaign_name} (ID: {campaign_id})")
        preview_lines.append(f"Budget Resource: {budget_resource}")
        preview_lines.append(f"New Daily Budget: {new_budget:,.0f} ({amount_micros:,} micros)")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "campaignBudgets", operations)
        preview_lines.append(f"\n✓ Budget updated to {new_budget:,.0f}.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error updating campaign budget: {str(e)}"


# ============================================================
# #3 SITELINK EXTENSIONS
# ============================================================

@mcp.tool()
async def add_sitelink_extensions(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(description="Campaign ID to attach sitelinks to"),
    sitelinks: str = Field(description="Pipe-separated sitelinks. Each sitelink uses '::' to separate fields: 'linkText::finalUrl::description1::description2'. Example: '料金表::https://example.com/price::明朗会計::追加費用なし|会社概要::https://example.com/about::信頼の実績::年間1万件対応'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Create and attach sitelink extensions to a campaign (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        sitelink_list = [s.strip() for s in sitelinks.split("|") if s.strip()]

        parsed = []
        for i, sl in enumerate(sitelink_list):
            parts = [p.strip() for p in sl.split("::")]
            if len(parts) < 2:
                return f"Error: Sitelink {i+1} must have at least linkText::finalUrl"
            link_text = parts[0]
            final_url = parts[1]
            desc1 = parts[2] if len(parts) > 2 else ""
            desc2 = parts[3] if len(parts) > 3 else ""

            if len(link_text) > 25:
                return f"Error: Sitelink {i+1} link text exceeds 25 chars ({len(link_text)}): '{link_text}'"
            if desc1 and len(desc1) > 35:
                return f"Error: Sitelink {i+1} description1 exceeds 35 chars ({len(desc1)}): '{desc1}'"
            if desc2 and len(desc2) > 35:
                return f"Error: Sitelink {i+1} description2 exceeds 35 chars ({len(desc2)}): '{desc2}'"

            parsed.append({"linkText": link_text, "finalUrl": final_url, "desc1": desc1, "desc2": desc2})

        preview_lines = [f"=== Sitelink Extension Preview ==="]
        preview_lines.append(f"Campaign ID: {campaign_id}")
        preview_lines.append(f"Sitelinks ({len(parsed)}):")
        for i, sl in enumerate(parsed):
            preview_lines.append(f"  {i+1}. {sl['linkText']} → {sl['finalUrl']}")
            if sl['desc1']:
                preview_lines.append(f"     {sl['desc1']} / {sl['desc2']}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        # Step 1: Create sitelink assets
        asset_operations = []
        for sl in parsed:
            asset_data = {
                "sitelinkAsset": {
                    "linkText": sl["linkText"],
                    "finalUrls": [sl["finalUrl"]]
                }
            }
            if sl["desc1"]:
                asset_data["sitelinkAsset"]["description1"] = sl["desc1"]
            if sl["desc2"]:
                asset_data["sitelinkAsset"]["description2"] = sl["desc2"]

            asset_operations.append({"create": asset_data})

        asset_result = mutate_google_ads(formatted_id, "assets", asset_operations)
        asset_resource_names = [r.get("resourceName", "") for r in asset_result.get("results", [])]

        # Step 2: Link assets to campaign
        link_operations = []
        for asset_rn in asset_resource_names:
            link_operations.append({
                "create": {
                    "campaign": f"customers/{formatted_id}/campaigns/{campaign_id}",
                    "asset": asset_rn,
                    "fieldType": "SITELINK"
                }
            })

        mutate_google_ads(formatted_id, "campaignAssets", link_operations)

        preview_lines.append(f"\n✓ Successfully created and linked {len(parsed)} sitelink extensions.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error adding sitelink extensions: {str(e)}"


# ============================================================
# #4 PMAX ASSET GROUP: LIST & PAUSE/ENABLE
# ============================================================

@mcp.tool()
async def list_asset_groups(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(default=None, description="Optional: filter by campaign ID. If not provided, lists all asset groups.")
) -> str:
    """List Performance Max asset groups with status and details."""
    try:
        formatted_id = format_customer_id(customer_id)

        query = """
            SELECT
                asset_group.id,
                asset_group.name,
                asset_group.status,
                asset_group.campaign,
                asset_group.primary_status,
                campaign.name
            FROM asset_group
        """
        if campaign_id:
            query += f" WHERE campaign.id = {campaign_id}"
        query += " ORDER BY campaign.name, asset_group.name"

        results = _internal_gaql_search(formatted_id, query)

        if not results:
            return "No asset groups found."

        lines = [f"=== Asset Groups ({len(results)}) ==="]
        lines.append("-" * 70)

        for r in results:
            ag = r.get("assetGroup", {})
            camp = r.get("campaign", {})
            ag_id = ag.get("id", "")
            ag_name = ag.get("name", "")
            ag_status = ag.get("status", "")
            ag_primary = ag.get("primaryStatus", "")
            camp_name = camp.get("name", "")

            lines.append(f"Campaign: {camp_name}")
            lines.append(f"  Asset Group: {ag_name} (ID: {ag_id})")
            lines.append(f"  Status: {ag_status} | Primary: {ag_primary}")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return f"Error listing asset groups: {str(e)}"


@mcp.tool()
async def pause_enable_asset_group(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    asset_group_id: str = Field(description="Asset group ID to pause or enable"),
    action: str = Field(description="Action: 'PAUSE' or 'ENABLE'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Pause or enable a Performance Max asset group (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        action_upper = action.upper()

        if action_upper == "PAUSE":
            status = "PAUSED"
        elif action_upper == "ENABLE":
            status = "ENABLED"
        else:
            return "Error: action must be 'PAUSE' or 'ENABLE'"

        resource_name = f"customers/{formatted_id}/assetGroups/{asset_group_id}"

        operations = [{
            "updateMask": "status",
            "update": {
                "resourceName": resource_name,
                "status": status
            }
        }]

        preview_lines = [f"=== Asset Group Status Change Preview ==="]
        preview_lines.append(f"Asset Group ID: {asset_group_id}")
        preview_lines.append(f"New Status: {status}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "assetGroups", operations)
        preview_lines.append(f"\n✓ Asset group status changed to {status}.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error changing asset group status: {str(e)}"


# ============================================================
# #5 PMAX TEXT ASSETS: ADD TO ASSET GROUP
# ============================================================

@mcp.tool()
async def add_asset_group_text_assets(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    asset_group_id: str = Field(description="Asset group ID to add text assets to"),
    field_type: str = Field(description="Asset field type: HEADLINE (30 chars), LONG_HEADLINE (90 chars), DESCRIPTION (90 chars), or BUSINESS_NAME (25 chars)"),
    texts: str = Field(description="Pipe-separated text values. Example: '格安引越しサービス|24時間365日対応|無料見積もり受付中'"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Add text assets (headlines, descriptions, etc.) to a PMax asset group (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        field_upper = field_type.upper()

        limits = {
            "HEADLINE": 30,
            "LONG_HEADLINE": 90,
            "DESCRIPTION": 90,
            "BUSINESS_NAME": 25
        }

        if field_upper not in limits:
            return f"Error: field_type must be one of: {', '.join(limits.keys())}"

        char_limit = limits[field_upper]
        text_list = [t.strip() for t in texts.split("|") if t.strip()]

        # Validate lengths
        for i, t in enumerate(text_list):
            if len(t) > char_limit:
                return f"Error: Text {i+1} exceeds {char_limit} chars ({len(t)}): '{t}'"

        preview_lines = [f"=== Add Asset Group Text Assets Preview ==="]
        preview_lines.append(f"Asset Group ID: {asset_group_id}")
        preview_lines.append(f"Field Type: {field_upper} (max {char_limit} chars)")
        preview_lines.append(f"Texts to add ({len(text_list)}):")
        for i, t in enumerate(text_list):
            preview_lines.append(f"  {i+1}. {t} ({len(t)} chars)")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        # Step 1: Create text assets
        asset_operations = []
        for t in text_list:
            asset_operations.append({
                "create": {
                    "textAsset": {
                        "text": t
                    }
                }
            })

        asset_result = mutate_google_ads(formatted_id, "assets", asset_operations)
        asset_resource_names = [r.get("resourceName", "") for r in asset_result.get("results", [])]

        # Step 2: Link assets to asset group
        link_operations = []
        for asset_rn in asset_resource_names:
            link_operations.append({
                "create": {
                    "assetGroup": f"customers/{formatted_id}/assetGroups/{asset_group_id}",
                    "asset": asset_rn,
                    "fieldType": field_upper
                }
            })

        mutate_google_ads(formatted_id, "assetGroupAssets", link_operations)

        preview_lines.append(f"\n✓ Successfully created and linked {len(text_list)} {field_upper} assets.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error adding text assets to asset group: {str(e)}"


@mcp.tool()
async def remove_asset_group_asset(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    asset_group_id: str = Field(description="Asset group ID"),
    asset_id: str = Field(description="Asset ID to remove from the asset group"),
    field_type: str = Field(description="Asset field type: HEADLINE, LONG_HEADLINE, DESCRIPTION, BUSINESS_NAME, MARKETING_IMAGE, SQUARE_MARKETING_IMAGE, LOGO, LANDSCAPE_LOGO, YOUTUBE_VIDEO"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Remove (unlink) an asset from a PMax asset group (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        field_upper = field_type.upper()

        resource_name = f"customers/{formatted_id}/assetGroupAssets/{asset_group_id}~{asset_id}~{field_upper}"

        operations = [{"remove": resource_name}]

        preview_lines = [f"=== Remove Asset Group Asset Preview ==="]
        preview_lines.append(f"Asset Group ID: {asset_group_id}")
        preview_lines.append(f"Asset ID: {asset_id}")
        preview_lines.append(f"Field Type: {field_upper}")
        preview_lines.append(f"Resource: {resource_name}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "assetGroupAssets", operations)
        preview_lines.append(f"\n✓ Asset removed from asset group.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error removing asset from asset group: {str(e)}"


# ============================================================
# #6 LINK IMAGE ASSET TO PMAX ASSET GROUP
# ============================================================

@mcp.tool()
async def link_asset_to_asset_group(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    asset_group_id: str = Field(description="Asset group ID to link the asset to"),
    asset_id: str = Field(description="Asset ID to link (from get_image_assets or other asset queries)"),
    field_type: str = Field(description="Asset field type: MARKETING_IMAGE (1200x628), SQUARE_MARKETING_IMAGE (1200x1200), LOGO (1200x1200), LANDSCAPE_LOGO (1200x300), YOUTUBE_VIDEO"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Link an existing image/logo/video asset to a PMax asset group (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        field_upper = field_type.upper()

        valid_types = ["MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "LOGO", "LANDSCAPE_LOGO", "YOUTUBE_VIDEO"]
        if field_upper not in valid_types:
            return f"Error: field_type must be one of: {', '.join(valid_types)}"

        asset_resource = f"customers/{formatted_id}/assets/{asset_id}"

        operations = [{
            "create": {
                "assetGroup": f"customers/{formatted_id}/assetGroups/{asset_group_id}",
                "asset": asset_resource,
                "fieldType": field_upper
            }
        }]

        preview_lines = [f"=== Link Asset to Asset Group Preview ==="]
        preview_lines.append(f"Asset Group ID: {asset_group_id}")
        preview_lines.append(f"Asset ID: {asset_id}")
        preview_lines.append(f"Field Type: {field_upper}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "assetGroupAssets", operations)
        preview_lines.append(f"\n✓ Asset linked to asset group.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error linking asset to asset group: {str(e)}"


# ============================================================
# #7 UPDATE CAMPAIGN BIDDING STRATEGY
# ============================================================

@mcp.tool()
async def update_campaign_bidding(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(description="Campaign ID to update bidding for"),
    strategy: str = Field(description="Bidding strategy: 'TARGET_CPA', 'TARGET_ROAS', 'MAXIMIZE_CONVERSIONS', 'MAXIMIZE_CONVERSION_VALUE', 'MAXIMIZE_CLICKS', or 'MANUAL_CPC'"),
    target_value: float = Field(default=None, description="Target value: CPA in currency units (e.g. 3000 for ¥3,000), ROAS as ratio (e.g. 3.0 for 300%), max CPC bid limit for MAXIMIZE_CLICKS, or not used for MANUAL_CPC."),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Change a campaign's bidding strategy (TARGET_CPA, TARGET_ROAS, MAXIMIZE_CONVERSIONS, etc.) (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        strategy_upper = strategy.upper()

        resource_name = f"customers/{formatted_id}/campaigns/{campaign_id}"
        update = {"resourceName": resource_name}
        update_mask_parts = []

        if strategy_upper == "TARGET_CPA":
            bidding = {}
            if target_value is not None:
                bidding["targetCpaMicros"] = str(int(target_value * 1_000_000))
            update["targetCpa"] = bidding
            update_mask_parts.append("targetCpa.targetCpaMicros")

        elif strategy_upper == "TARGET_ROAS":
            bidding = {}
            if target_value is not None:
                bidding["targetRoas"] = target_value
            update["targetRoas"] = bidding
            update_mask_parts.append("targetRoas.targetRoas")

        elif strategy_upper == "MAXIMIZE_CONVERSIONS":
            bidding = {}
            if target_value is not None:
                bidding["targetCpaMicros"] = str(int(target_value * 1_000_000))
            update["maximizeConversions"] = bidding
            update_mask_parts.append("maximizeConversions.targetCpaMicros")

        elif strategy_upper == "MAXIMIZE_CONVERSION_VALUE":
            bidding = {}
            if target_value is not None:
                bidding["targetRoas"] = target_value
            update["maximizeConversionValue"] = bidding
            update_mask_parts.append("maximizeConversionValue.targetRoas")

        elif strategy_upper == "MAXIMIZE_CLICKS":
            bidding = {}
            if target_value is not None:
                bidding["cpcBidCeilingMicros"] = str(int(target_value * 1_000_000))
            update["targetSpend"] = bidding
            update_mask_parts.append("targetSpend.cpcBidCeilingMicros")

        elif strategy_upper == "MANUAL_CPC":
            update["manualCpc"] = {}
            update_mask_parts.append("manualCpc")

        else:
            return "Error: strategy must be TARGET_CPA, TARGET_ROAS, MAXIMIZE_CONVERSIONS, MAXIMIZE_CONVERSION_VALUE, MAXIMIZE_CLICKS, or MANUAL_CPC"

        operations = [{
            "updateMask": ",".join(update_mask_parts),
            "update": update
        }]

        preview_lines = [f"=== Campaign Bidding Update Preview ==="]
        preview_lines.append(f"Campaign ID: {campaign_id}")
        preview_lines.append(f"Strategy: {strategy_upper}")
        if strategy_upper == "MANUAL_CPC":
            preview_lines.append(f"Note: Use update_keyword_bids to set individual keyword bids after switching.")
        elif target_value is not None:
            if "CPA" in strategy_upper or strategy_upper == "MAXIMIZE_CONVERSIONS":
                preview_lines.append(f"Target CPA: {target_value:,.0f}")
            elif "ROAS" in strategy_upper or strategy_upper == "MAXIMIZE_CONVERSION_VALUE":
                preview_lines.append(f"Target ROAS: {target_value:.1f} ({target_value*100:.0f}%)")
            elif strategy_upper == "MAXIMIZE_CLICKS":
                preview_lines.append(f"Max CPC Bid Limit: {target_value:,.0f}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "campaigns", operations)
        preview_lines.append(f"\n✓ Bidding strategy updated.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error updating bidding strategy: {str(e)}"


# ============================================================
# #8 CAMPAIGN-LEVEL NEGATIVE KEYWORDS
# ============================================================

@mcp.tool()
async def add_campaign_negative_keywords(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(description="Campaign ID to add negative keywords to"),
    keywords: str = Field(description="Comma-separated keywords to add as negatives. Example: '寿命,年数,自分で'"),
    match_type: str = Field(default="BROAD", description="Match type: BROAD, PHRASE, or EXACT"),
    dry_run: bool = Field(default=True, description="If true, only shows what would be changed without executing")
) -> str:
    """Add negative keywords directly to a specific campaign (dry_run=true by default)."""
    try:
        formatted_id = format_customer_id(customer_id)
        keyword_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
        match_type_upper = match_type.upper()

        if match_type_upper not in ["BROAD", "PHRASE", "EXACT"]:
            return "Error: match_type must be BROAD, PHRASE, or EXACT"

        operations = []
        for kw in keyword_list:
            operations.append({
                "create": {
                    "campaign": f"customers/{formatted_id}/campaigns/{campaign_id}",
                    "negative": True,
                    "keyword": {
                        "text": kw,
                        "matchType": match_type_upper
                    }
                }
            })

        preview_lines = [f"=== Campaign Negative Keyword Preview ==="]
        preview_lines.append(f"Campaign ID: {campaign_id}")
        preview_lines.append(f"Match Type: {match_type_upper}")
        preview_lines.append(f"Keywords to add ({len(keyword_list)}):")
        for kw in keyword_list:
            preview_lines.append(f"  - {kw}")

        if dry_run:
            preview_lines.append(f"\n[DRY RUN] No changes made. Set dry_run=false to execute.")
            return "\n".join(preview_lines)

        result = mutate_google_ads(formatted_id, "campaignCriteria", operations)

        added_count = len(result.get("results", []))
        preview_lines.append(f"\n✓ Successfully added {added_count} negative keywords to campaign.")
        return "\n".join(preview_lines)

    except Exception as e:
        return f"Error adding campaign negative keywords: {str(e)}"


if __name__ == "__main__":
    # Start the MCP server on stdio transport
    mcp.run(transport="stdio")
