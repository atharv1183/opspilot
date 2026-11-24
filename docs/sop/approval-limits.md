# Approval Limits / Delegation of Authority (SOP-FIN-001)

**Owner:** CFO · **Applies to:** purchase orders, invoice exceptions, credit releases, write-offs

## 1. Purchase order approval matrix
Approval is based on the **total PO value** (all lines, excluding tax).

| PO total (USD)          | Approver                         | System status until approved |
|-------------------------|----------------------------------|------------------------------|
| up to $10,000           | Auto-approved by the ERP         | APPROVED immediately         |
| $10,000.01 – $50,000    | Procurement Manager              | PENDING_APPROVAL             |
| $50,000.01 – $250,000   | Director of Operations           | PENDING_APPROVAL             |
| above $250,000          | CFO                              | PENDING_APPROVAL             |

- POs created by the OpsPilot AI agent always require a **human confirmation in the chat**
  before the PO is created; after that, the matrix above still applies.
- Splitting a requirement into several smaller POs to stay under a limit is prohibited.

## 2. Invoice exception approvals
| Exception                                   | Approver                     |
|---------------------------------------------|------------------------------|
| Price variance above tolerance, up to $1,000 | Buyer who owns the PO        |
| Price variance above $1,000                 | Procurement Manager          |
| Quantity variance (billed > received)       | Cannot be approved – credit note or corrected invoice required |
| Invoice without goods receipt               | Cannot be paid until a GRN is posted |

## 3. Credit releases (Order-to-Cash)
Sales orders on credit hold may be released by the Credit Controller up to $50,000 of excess
exposure, and by the Finance Director above that.

## 4. Inventory adjustments
Cycle-count write-offs up to $1,000 per count are approved by the Warehouse Manager, above
$1,000 by the Finance Controller (see SOP-WH-002).
