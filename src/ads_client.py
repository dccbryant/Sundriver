"""Google Ads API wrapper for enabling/pausing campaigns.

Uses the official google-ads Python client. Authentication is configured via
google-ads.yaml (developer token, OAuth2 client credentials + refresh token,
and the login_customer_id of the manager account if applicable).

The customer ID we operate on is passed in separately so the same yaml can
be reused across child accounts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass
class CampaignAction:
    customer_id: str
    campaign_id: str
    desired_status: str  # "ENABLED" or "PAUSED"
    reason: str


class AdsClient:
    """Lazy-initialised wrapper so the module imports cleanly even without
    the google-ads SDK installed (handy for unit-testing the Sheet logic)."""

    def __init__(self, yaml_path: str, customer_id: str, dry_run: bool = False):
        self.yaml_path = yaml_path
        self.customer_id = customer_id.replace("-", "")
        self.dry_run = dry_run
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient  # type: ignore
            self._client = GoogleAdsClient.load_from_storage(self.yaml_path)
        return self._client

    def get_campaign_statuses(self, campaign_ids: Iterable[str]) -> dict[str, str]:
        """Return {campaign_id: status_name} for the requested campaigns."""
        ids = [str(c) for c in campaign_ids]
        if not ids:
            return {}
        client = self._client_lazy()
        ga_service = client.get_service("GoogleAdsService")
        id_list = ", ".join(ids)
        query = (
            "SELECT campaign.id, campaign.status, campaign.name "
            "FROM campaign "
            f"WHERE campaign.id IN ({id_list})"
        )
        response = ga_service.search(customer_id=self.customer_id, query=query)
        statuses: dict[str, str] = {}
        for row in response:
            statuses[str(row.campaign.id)] = row.campaign.status.name
        return statuses

    def apply(self, action: CampaignAction) -> str:
        """Push a status change. Returns a short outcome string for logging."""
        target = action.desired_status.upper()
        if target not in ("ENABLED", "PAUSED"):
            return f"skip: invalid target {target}"

        if self.dry_run:
            return f"dry-run: would set campaign {action.campaign_id} -> {target}"

        client = self._client_lazy()
        campaign_service = client.get_service("CampaignService")
        op = client.get_type("CampaignOperation")
        campaign = op.update
        campaign.resource_name = campaign_service.campaign_path(
            self.customer_id, action.campaign_id
        )
        status_enum = client.enums.CampaignStatusEnum
        campaign.status = status_enum.ENABLED if target == "ENABLED" else status_enum.PAUSED

        field_mask = client.get_type("FieldMask")
        field_mask.paths.append("status")
        op.update_mask.CopyFrom(field_mask)

        campaign_service.mutate_campaigns(
            customer_id=self.customer_id, operations=[op]
        )
        return f"applied: campaign {action.campaign_id} -> {target}"
