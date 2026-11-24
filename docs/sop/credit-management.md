# Customer Credit Management (SOP-FIN-004)

**Owner:** Credit Controller · **Applies to:** all customer sales orders (Order-to-Cash)

## 1. Credit limits
Every customer has a credit limit in the customer master. Default limits by segment for new
customers: Retail $5,000; Contractor $10,000; Distributor $25,000; OEM $50,000; Government
$100,000. Higher limits require a credit report and Finance Director approval.

## 2. Automatic credit check
When a sales order is entered, the ERP calculates
`exposure = open accounts receivable (OPEN + OVERDUE invoices) + value of the new order`.
If exposure exceeds the credit limit, the order is placed on **CREDIT_HOLD** and cannot be
allocated, picked or shipped.

Orders are also held if the customer has invoices **more than 30 days overdue**.

## 3. Releasing a credit hold
- The Credit Controller reviews payment history and may release the order up to $50,000 of
  excess exposure; above that the Finance Director decides (SOP-FIN-001).
- Alternatives: request prepayment, a partial shipment within the limit, or a temporary limit
  increase.
- Released orders return to OPEN and are processed normally.

## 4. Collections
- Standard payment terms are **NET30**.
- Dunning: reminder at 7 days overdue, phone call at 15 days, account stop at 30 days,
  escalation to collections agency at 90 days.
