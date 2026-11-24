"""RAG chunking/retrieval and the zeep <-> spyne SOAP integration."""

import pytest

from opspilot.erp import ERPError
from opspilot.rag.chunking import chunk_markdown
from opspilot.rag.embeddings import HashEmbedder
from opspilot.rag.ingest import DEFAULT_SOP_DIR, load_chunks
from opspilot.rag.retriever import search, unique_docs


def test_chunking_keeps_title_and_section():
    md = (
        "# My Policy\n\nIntro text.\n\n## 1. Scope\n\nApplies to all.\n\n## 2. Rules\n\n"
        + ("Long paragraph. " * 80 + "\n\n") * 3
    )
    chunks = chunk_markdown("my.md", md, max_chars=600)
    assert chunks[0].content.startswith("My Policy > 1. Scope")
    assert "Intro text." in chunks[0].content  # preamble merged into the first section
    assert any(c.section == "2. Rules" for c in chunks)
    assert all(c.content.startswith("My Policy > ") for c in chunks if c.section)
    assert len([c for c in chunks if c.section == "2. Rules"]) >= 3


def test_sop_corpus_size():
    docs = {c.doc_name for c in load_chunks(DEFAULT_SOP_DIR)}
    assert 8 <= len(docs) <= 12


@pytest.mark.parametrize(
    "query,doc",
    [("3-way match price tolerance invoice hold", "three-way-match-tolerance.md"),
     ("cycle count frequency for A class items", "inventory-cycle-count.md"),
     ("supplier onboarding bank details verification", "supplier-onboarding.md")],
)  # fmt: skip
def test_retrieval_top3_contains_expected_doc(db, query, doc):
    hits = search(db, query, k=3, embedder=HashEmbedder(384))
    assert doc in unique_docs(hits)
    assert all(-1.0 <= h.score <= 1.0 for h in hits)


def test_soap_quote_and_delivery_status(soap_url):
    from opspilot.soap.client import SupplierPortalClient

    client = SupplierPortalClient(soap_url)
    quotes = client.get_supplier_quote("ITM-1088", 60)
    assert len(quotes) == 4 and quotes[0]["unit_price"] <= quotes[1]["unit_price"]
    assert {"supplier_code", "supplier_status", "lead_time_days", "total"} <= quotes[0].keys()
    status = client.get_delivery_status("PO-10005")
    assert status["status"] == "DELIVERED" and status["po_number"] == "PO-10005"


def test_soap_fault_becomes_business_error(soap_url):
    from opspilot.soap.client import SupplierPortalClient

    with pytest.raises(ERPError, match="not found"):
        SupplierPortalClient(soap_url).get_delivery_status("PO-99999")


def test_soap_unreachable_is_reported():
    from opspilot.soap.client import PortalUnavailable, SupplierPortalClient

    with pytest.raises(PortalUnavailable):
        SupplierPortalClient("http://127.0.0.1:9/?wsdl", timeout=1).get_supplier_quote(
            "ITM-1088", 1
        )
