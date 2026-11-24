# Returns Policy – Customer Returns and Returns to Vendor (SOP-OPS-005)

**Owner:** Customer Service Manager & Procurement · **Version:** 2.0

## 1. Customer returns (RMA)
- Customers must request a **Return Merchandise Authorization (RMA)** within **30 days** of
  delivery. Returns without an RMA number are refused at the dock.
- **Defective or wrongly shipped goods:** full credit, no restocking fee; we pay return freight.
- **Non-defective returns** (customer changed their mind): accepted only for unopened,
  resaleable stock, with a **15% restocking fee**; the customer pays return freight.
- Custom-cut materials (cable, hose, sheet metal cut to length) are **non-returnable** unless
  defective.
- Returned goods are inspected within 3 business days. Resaleable goods go back to available
  stock; damaged goods go to quarantine for scrap or supplier claim.
- A **credit memo** is issued against the original customer invoice after inspection.

## 2. Returns to vendor (RTV)
- Goods rejected at goods receipt (damaged, wrong item, failed inspection) are recorded as
  `qty_rejected` on the GRN and moved to the quarantine location.
- Procurement raises an RTV with the supplier within **5 business days** and requests either a
  replacement shipment or a **debit note / credit note**.
- Rejected quantities are never paid: they are excluded from the accepted quantity used in the
  3-way match (SOP-AP-002).

## 3. Warranty claims
Warranty claims follow the supplier's warranty terms; the buyer coordinates claims with
quality assurance and keeps evidence (photos, batch numbers).
