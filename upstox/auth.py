"""
Upstox Authentication Module.
Handles token retrieval, validation, and ApiClient initialization.
"""

import logging
from typing import Optional
import upstox_client
from upstox_client.rest import ApiException

import config

logger = logging.getLogger(__name__)


class UpstoxAuth:
    """Manages Upstox API credentials and Client configuration."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        analytics_token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self.access_token = access_token or config.UPSTOX_ACCESS_TOKEN
        self.analytics_token = analytics_token or config.UPSTOX_ANALYTICS_TOKEN
        self.client_id = client_id or config.UPSTOX_CLIENT_ID
        self.client_secret = client_secret or config.UPSTOX_CLIENT_SECRET
        self._api_client: Optional[upstox_client.ApiClient] = None

    @property
    def has_access_token(self) -> bool:
        return bool(self.access_token and self.access_token.strip())

    @property
    def has_analytics_token(self) -> bool:
        return bool(self.analytics_token and self.analytics_token.strip())

    def get_configuration(self) -> upstox_client.Configuration:
        """Returns upstox_client Configuration instance with access token."""
        configuration = upstox_client.Configuration()
        if self.has_access_token:
            configuration.access_token = self.access_token.strip()
        return configuration

    def get_api_client(self) -> upstox_client.ApiClient:
        """Returns or creates a cached upstox_client ApiClient instance."""
        if self._api_client is None:
            config_instance = self.get_configuration()
            self._api_client = upstox_client.ApiClient(config_instance)
        return self._api_client

    def validate_token(self) -> bool:
        """
        Validates token against Upstox User API.
        Returns True if authorized, False otherwise.
        """
        if not self.has_access_token:
            logger.warning("No access token provided.")
            return False

        try:
            client = self.get_api_client()
            user_api = upstox_client.UserApi(client)
            profile = user_api.get_profile("2.0")
            if profile and profile.data:
                logger.info(f"Authenticated successfully as: {profile.data.user_name} ({profile.data.user_id})")
                return True
            return False
        except ApiException as e:
            logger.warning(f"Upstox token validation failed (HTTP {e.status}): {e.reason}")
            return False
        except Exception as e:
            logger.warning(f"Upstox token validation error: {e}")
            return False

    def get_auth_summary(self) -> dict:
        """Returns authorization status dictionary."""
        return {
            "access_token_present": self.has_access_token,
            "analytics_token_present": self.has_analytics_token,
            "client_id_present": bool(self.client_id),
            "client_secret_present": bool(self.client_secret),
        }
