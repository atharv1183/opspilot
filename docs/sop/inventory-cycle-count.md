# Inventory Cycle Count Procedure (SOP-WH-002)

**Owner:** Warehouse Manager · **Applies to:** all warehouses (WH-A Chicago, WH-B Dallas,
WH-C Atlanta, WH-D Reno)

## 1. Purpose
Cycle counting replaces the annual wall-to-wall stock take. Small sets of locations are counted
continuously so that system quantities (`qty_on_hand`) stay accurate for planning and
available-to-promise.

## 2. ABC classification and count frequency
| Class | Definition (annual consumption value) | Count frequency | Count tolerance |
|-------|----------------------------------------|-----------------|-----------------|
| A     | top 80% of value                       | monthly         | ±2%             |
| B     | next 15% of value                      | quarterly       | ±5%             |
| C     | remaining 5% of value                  | every 6 months  | ±5%             |

Items at or below their reorder level are additionally counted before a replenishment PO
is placed if the last count is older than 90 days.

## 3. Counting rules
- **Blind counts:** counters do not see the system quantity.
- Movements in the counted location are frozen during the count.
- A variance outside tolerance triggers a **recount by a second counter**.
- Confirmed variances are posted as an inventory adjustment; the item's `last_counted_at`
  date is updated.

## 4. Approval of adjustments
Adjustments up to **$1,000** of value per count are approved by the Warehouse Manager; above
$1,000 by the Finance Controller (SOP-FIN-001). Root causes (mis-picks, unrecorded receipts,
damage) are logged.

## 5. KPIs
Inventory record accuracy target: **≥ 97%** of counted lines within tolerance.
