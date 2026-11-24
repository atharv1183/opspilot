"""REST API endpoints (FastAPI TestClient against the seeded test DB)."""


def test_health_and_openapi(api):
    assert api.get("/health").json()["status"] == "ok"
    paths = api.get("/openapi.json").json()["paths"]
    assert "/agent/chat" in paths and "/purchase-orders" in paths and "/atp" in paths


def test_items_crud(api):
    body = {"sku": "ITM-9901", "name": "Test Widget", "category": "Tools", "uom": "EA",
            "standard_cost": "10.00", "list_price": "15.00", "lead_time_days": 5}  # fmt: skip
    assert api.post("/items", json=body).status_code == 201
    assert api.post("/items", json=body).status_code == 409
    assert (
        api.patch("/items/ITM-9901", json={"list_price": "16.50"}).json()["list_price"] == "16.50"
    )
    assert api.get("/items", params={"q": "Test Widget"}).json()[0]["sku"] == "ITM-9901"
    assert api.delete("/items/ITM-9901").status_code == 204
    assert api.get("/items/ITM-9901").json()["is_active"] is False
    assert api.get("/items/ITM-0000").status_code == 404


def test_inventory_endpoints(api):
    rows = api.get("/inventory", params={"warehouse": "B", "below_reorder": True}).json()
    assert len(rows) == 4 and all(r["below_reorder_level"] for r in rows)
    assert api.get("/inventory/ITM-1088").json()["item"]["sku"] == "ITM-1088"
    atp = api.get("/atp", params={"item": "ITM-1088", "qty": 50, "date": "2026-12-31"}).json()
    assert atp["can_promise"] is True
    adj = api.patch("/inventory/ITM-1088/WH-A", json={"reorder_level": 15}).json()
    assert adj["reorder_level"] == 15


def test_p2p_flow_requisition_po_receipt(api):
    pr = api.post("/purchase-requisitions",
                  json={"item": "ITM-1088", "quantity": 10, "warehouse": "WH-C"}).json()  # fmt: skip
    po = api.post("/purchase-orders",
                  json={"requisition_id": pr["pr_number"], "supplier_id": "SUP-0123"})  # fmt: skip
    assert po.status_code == 201 and po.json()["status"] == "APPROVED"
    po_no = po.json()["po_number"]
    grn = api.post("/goods-receipts", json={"po_number": po_no,
                                            "lines": [{"line_no": 1, "qty_received": 10}]})  # fmt: skip
    assert grn.status_code == 201 and grn.json()["po"] == "RECEIVED"
    over = api.post("/goods-receipts", json={"po_number": po_no,
                                             "lines": [{"line_no": 1, "qty_received": 1}]})  # fmt: skip
    assert over.status_code == 422


def test_invoices_and_three_way_match(api):
    held = api.get("/supplier-invoices", params={"status": "ON_HOLD"}).json()
    assert "INV-1042" in {i["invoice_number"] for i in held}
    m = api.get("/supplier-invoices/INV-1042/three-way-match").json()
    assert m["match_status"] == "PRICE_VARIANCE"
    assert api.get("/customer-invoices", params={"status": "OVERDUE"}).status_code == 200


def test_sales_orders(api):
    assert api.get("/sales-orders/SO-20003").json()["status"] == "INVOICED"
    so = api.post("/sales-orders", json={"customer_code": "CUS-00001", "warehouse": "WH-A",
                                         "lines": [{"item": "ITM-1088", "quantity": 2}]})  # fmt: skip
    assert so.status_code == 201 and so.json()["status"] in {"OPEN", "CREDIT_HOLD"}


def test_soap_bridge_endpoints(api):
    quotes = api.get("/supplier-portal/quotes", params={"item": "ITM-1088", "qty": 60}).json()
    assert quotes[0]["unit_price"] <= quotes[-1]["unit_price"]
    assert api.get("/supplier-portal/delivery-status/PO-10005").json()["status"] == "DELIVERED"


def test_agent_chat_with_human_approval(api):
    r = api.post("/agent/chat", json={"message": "Why is invoice INV-1042 on hold?"}).json()
    assert r["tool_calls"][0]["name"] == "three_way_match" and "PRICE_VARIANCE" in r["answer"]

    pr = api.post("/purchase-requisitions",
                  json={"item": "ITM-1277", "quantity": 80, "warehouse": "WH-B"}).json()  # fmt: skip
    msg = f"Convert {pr['pr_number']} into a purchase order with the cheapest approved supplier"
    r = api.post("/agent/chat", json={"message": msg}).json()
    assert (
        r["answer"] is None
        and r["pending_approval"]["actions"][0]["tool"] == "create_purchase_order"
    )
    tid = r["thread_id"]
    assert api.post("/agent/chat", json={"thread_id": tid, "message": "hi"}).status_code == 409
    done = api.post("/agent/chat", json={"thread_id": tid, "approve": True}).json()
    assert done["pending_approval"] is None and "Created PO-" in done["answer"]
    assert api.post("/agent/chat", json={"thread_id": tid, "approve": True}).status_code == 409
