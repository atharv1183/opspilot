# Inventory Replenishment and Reorder Policy (SOP-INV-001)

**Owner:** Supply Planning · **Applies to:** stocked items in all warehouses

## 1. Reorder point model
Each item/warehouse combination has two planning parameters in the `inventory` table:
- **Reorder level (reorder point):**
  `average daily demand × supplier lead time (days) + safety stock`.
- **Reorder quantity:** the standard replenishment lot, normally 2–4× the reorder level,
  rounded to the supplier's pack size.

Safety stock targets a 95% service level for A items and 90% for B/C items.

## 2. Replenishment trigger
When `qty_on_hand` falls **at or below the reorder level**, the item is a reorder exception.
The planner (or the OpsPilot agent) must:
1. Check the quantity already **on order** (open PO lines not yet received). If on-order
   quantity covers the shortfall, no new requisition is raised.
2. Otherwise raise a **purchase requisition** for the reorder quantity for that warehouse.
3. Convert the requisition into a PO with the **cheapest approved supplier** that can meet
   the need-by date (SOP-PROC-001), subject to approval limits (SOP-FIN-001).

## 3. Expediting
If projected stock will hit zero before the PO's expected date, the buyer expedites with the
supplier (check delivery status in the Supplier Portal) or transfers stock from another
warehouse that is above its reorder level.

## 4. Parameter review
Reorder levels and quantities are reviewed quarterly using the last 12 months of demand and
current supplier lead times. Items with no movement for 12 months are flagged as slow-moving.
