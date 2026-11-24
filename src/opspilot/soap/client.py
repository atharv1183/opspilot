"""zeep client for the legacy Supplier Portal SOAP service (used by the agent tools)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import zeep
from zeep.exceptions import Error as ZeepError
from zeep.exceptions import Fault
from zeep.helpers import serialize_object
from zeep.transports import Transport

from opspilot.config import get_settings
from opspilot.erp import ERPError


class PortalUnavailable(RuntimeError):
    """The SOAP endpoint could not be reached."""


class SupplierPortalClient:
    """Thin typed wrapper around the zeep client; WSDL is loaded lazily on first call."""

    def __init__(self, wsdl_url: str | None = None, timeout: float = 10.0) -> None:
        self.wsdl_url = wsdl_url or get_settings().soap_url
        self.timeout = timeout
        self._client: zeep.Client | None = None

    @property
    def client(self) -> zeep.Client:
        if self._client is None:
            try:
                transport = Transport(timeout=self.timeout, operation_timeout=self.timeout)
                self._client = zeep.Client(self.wsdl_url, transport=transport)
            except Exception as exc:  # network / WSDL errors
                raise PortalUnavailable(
                    f"Supplier portal unreachable at {self.wsdl_url}: {exc}"
                ) from exc
        return self._client

    def _call(self, op: str, *args: Any) -> Any:
        try:
            return serialize_object(getattr(self.client.service, op)(*args))
        except Fault as exc:
            raise ERPError(exc.message) from exc
        except (ZeepError, OSError) as exc:
            raise PortalUnavailable(f"Supplier portal call {op} failed: {exc}") from exc

    def get_supplier_quote(self, item_id: str, qty: float) -> list[dict[str, Any]]:
        """GetSupplierQuote -> list of quote dicts (cheapest first)."""
        result = self._call("GetSupplierQuote", str(item_id), float(qty))
        if result is None:
            return []
        if isinstance(result, dict) and "SupplierQuote" in result:
            result = result["SupplierQuote"]
        return [dict(q) for q in (result or [])]

    def get_delivery_status(self, po_id: str) -> dict[str, Any]:
        """GetDeliveryStatus -> delivery status dict."""
        return dict(self._call("GetDeliveryStatus", str(po_id)))


@lru_cache
def get_portal_client() -> SupplierPortalClient:
    """Process-wide client (WSDL parsed once)."""
    return SupplierPortalClient()
