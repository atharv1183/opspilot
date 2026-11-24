# Order Fulfillment and Available-to-Promise (SOP-OPS-002)

**Owner:** Customer Service & Logistics · **Applies to:** sales orders (Order-to-Cash)

## 1. Order-to-Cash flow
Sales order (OPEN) → credit check → allocation (ALLOCATED, stock reserved) → pick/pack →
shipment (SHIPPED, carrier + tracking number) → customer invoice (INVOICED) → payment (CLOSED).

## 2. Available-to-Promise (ATP)
Customer service must check ATP before confirming a delivery date:

`ATP = quantity on hand − quantity reserved for other orders + open purchase order receipts
expected on or before the requested date`

- If ATP ≥ requested quantity, the requested date can be promised.
- If not, quote the **earliest date** at which cumulative supply (stock + scheduled PO
  receipts) covers the quantity.
- If open POs cannot cover it, the promise date is today + item lead time + 2 days handling,
  and a requisition must be raised.
- Never promise stock that is reserved for another customer.

## 3. Allocation priority
When stock is short: (1) orders with confirmed promise dates, (2) key accounts / contracts,
(3) first-come first-served by order date.

## 4. Partial shipments and backorders
Partial shipments are allowed when the customer agrees; the remaining quantity stays on
backorder and the order status is PARTIALLY_SHIPPED. Freight for the second shipment is on us
if the split was caused by our stock-out.

## 5. Shipping cut-off
Orders allocated before 2 pm local warehouse time ship the same day.
