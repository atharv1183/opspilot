"""spyne SOAP 1.1 service simulating a legacy "Supplier Portal".

Operations
----------
* ``GetSupplierQuote(item_id, qty)`` -> array of SupplierQuote
* ``GetDeliveryStatus(po_id)``       -> DeliveryStatus

WSDL: ``http://<host>:8001/?wsdl``. Run: ``python -m opspilot.soap.service --port 8001``
"""

from __future__ import annotations

import argparse
import logging
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from spyne import (
    Application,
    Array,
    ComplexModel,
    Double,
    Fault,
    Integer,
    ServiceBase,
    Unicode,
    rpc,
)
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication

from opspilot.erp import ERPError
from opspilot.soap import portal

TNS = "urn:opspilot:supplier-portal"
log = logging.getLogger("opspilot.soap")


class SupplierQuote(ComplexModel):
    __namespace__ = TNS
    supplier_code = Unicode
    supplier_name = Unicode
    supplier_status = Unicode
    item_sku = Unicode
    quantity = Double
    unit_price = Double
    currency = Unicode
    lead_time_days = Integer
    total = Double


class DeliveryStatus(ComplexModel):
    __namespace__ = TNS
    po_number = Unicode
    status = Unicode
    carrier = Unicode
    tracking_number = Unicode
    eta = Unicode
    delay_days = Integer
    last_event = Unicode


def _session():  # noqa: ANN202 - indirection so tests can swap the session factory
    from opspilot.db.session import session_scope

    return session_scope()


class SupplierPortalService(ServiceBase):
    """Legacy supplier portal operations."""

    @rpc(Unicode(min_occurs=1), Double(min_occurs=1), _returns=Array(SupplierQuote))
    def GetSupplierQuote(ctx, item_id, qty):  # noqa: N802, N805, ANN001, ANN201
        """Return quotes from all suppliers that list the item, cheapest first."""
        try:
            with _session() as s:
                return [SupplierQuote(**q) for q in portal.portal_quotes(s, item_id, qty)]
        except ERPError as exc:
            raise Fault(faultcode="Client.NotFound", faultstring=str(exc)) from exc

    @rpc(Unicode(min_occurs=1), _returns=DeliveryStatus)
    def GetDeliveryStatus(ctx, po_id):  # noqa: N802, N805, ANN001, ANN201
        """Return the supplier/carrier delivery status for a purchase order."""
        try:
            with _session() as s:
                return DeliveryStatus(**portal.delivery_status(s, po_id))
        except ERPError as exc:
            raise Fault(faultcode="Client.NotFound", faultstring=str(exc)) from exc


def build_wsgi_app() -> WsgiApplication:
    """Create the spyne WSGI application."""
    app = Application(
        [SupplierPortalService],
        tns=TNS,
        name="SupplierPortal",
        in_protocol=Soap11(validator="lxml"),
        out_protocol=Soap11(),
    )
    return WsgiApplication(app)


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        log.info("%s - %s", self.address_string(), format % args)


def make_soap_server(host: str = "0.0.0.0", port: int = 8001) -> WSGIServer:
    """Build a threaded WSGI server hosting the SOAP app (port 0 = random free port)."""
    return make_server(host, port, build_wsgi_app(), server_class=_ThreadingWSGIServer,
                       handler_class=_QuietHandler)  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the legacy Supplier Portal SOAP service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("spyne").setLevel(logging.WARNING)
    server = make_soap_server(args.host, args.port)
    log.info("Supplier Portal SOAP service on http://%s:%s/?wsdl", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
