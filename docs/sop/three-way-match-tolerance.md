# 3-Way Match and Invoice Tolerances (SOP-AP-002)

**Owner:** Accounts Payable Lead · **Applies to:** all PO-backed supplier invoices

## 1. What is matched
Before a supplier invoice is paid, every invoiced line is compared against:
1. the **purchase order line** – agreed unit price and ordered quantity;
2. the **goods receipt (GRN)** – quantity accepted by the warehouse (received minus rejected);
3. the **invoice line** – unit price and quantity billed.

## 2. Tolerances
| Check                 | Tolerance                          | Result if exceeded |
|-----------------------|------------------------------------|--------------------|
| Unit price            | +2% above the PO price per line    | PRICE_VARIANCE hold |
| Quantity              | 0% – billed qty may not exceed accepted qty | QTY_VARIANCE hold |
| Goods receipt         | A GRN must exist for every billed line | NO_RECEIPT hold |

- Only **adverse** price variances (billed above PO price) block payment. Invoices billed
  below the PO price are paid at the invoiced price.
- A price difference within 2% is accepted automatically and booked as purchase price variance.
- If several checks fail, the hold reason follows the precedence
  NO_RECEIPT > QTY_VARIANCE > PRICE_VARIANCE.

## 3. Hold resolution
| Hold reason     | Action                                                                                   | Owner | SLA |
|-----------------|------------------------------------------------------------------------------------------|-------|-----|
| PRICE_VARIANCE  | Buyer confirms the new price (PO revision, approval per SOP-FIN-001) **or** requests a credit note for the difference | Buyer | 5 business days |
| QTY_VARIANCE    | Pay only for the received quantity; request a corrected invoice/credit note, or post the missing GRN if goods actually arrived | AP + Warehouse | 5 business days |
| NO_RECEIPT      | Warehouse posts the GRN when goods arrive; invoice stays on hold until then | Warehouse | 2 business days after arrival |

## 4. Re-matching
After a GRN is posted or a credit note is received, AP re-runs the match. Invoices that now
match are released for payment according to the supplier payment terms (e.g. NET30).

## 5. Early-payment discounts
Holds must be resolved quickly enough to capture early-payment discounts (e.g. 2/10 NET30).
