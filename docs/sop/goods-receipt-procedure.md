# Goods Receipt Procedure (SOP-WH-004)

**Owner:** Warehouse Manager · **Applies to:** all inbound deliveries against purchase orders

## 1. At the dock
1. Match the delivery note to an open PO (status APPROVED, SENT or PARTIALLY_RECEIVED).
   Deliveries without a PO number are refused or held in the receiving area for max. 48 hours.
2. Count the goods and compare item and quantity with the PO line.
3. Inspect for damage; sample-test production materials per the quality plan.

## 2. Posting the GRN
- The goods receipt (GRN) must be posted in the ERP **within 24 hours** of physical arrival;
  late GRNs cause NO_RECEIPT invoice holds (SOP-AP-002).
- Record `qty_received` and any `qty_rejected`. Only the accepted quantity
  (received − rejected) increases stock on hand and counts for the 3-way match.
- Rejected goods move to quarantine and are returned to the vendor (SOP-OPS-005).

## 3. Over- and under-deliveries
- **Over-delivery:** receipts above the ordered quantity are not allowed (0% tolerance). The
  excess is refused or requires buyer approval and a PO change before it can be received.
- **Under-delivery:** the PO becomes PARTIALLY_RECEIVED and stays open for the remainder unless
  the buyer closes the line.

## 4. After posting
Once all lines are fully received the PO status becomes RECEIVED; Accounts Payable can then
match the supplier invoice.
