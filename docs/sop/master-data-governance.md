# Master Data Governance (SOP-MDM-001)

**Owner:** Master Data Management team · **Applies to:** supplier, customer, item and
warehouse master data, including data migrations

## 1. Ownership
| Domain     | Data owner          | Key fields                                  |
|------------|---------------------|---------------------------------------------|
| Suppliers  | Procurement         | supplier code, tax ID, status, payment terms |
| Customers  | Credit Control      | customer code, credit limit, segment        |
| Items      | Supply Planning     | SKU, unit of measure, standard cost, lead time |
| Warehouses | Logistics           | warehouse code, address                     |

## 2. Data standards
- **Codes:** suppliers `SUP-0001`, customers `CUS-00001`, items `ITM-1001`, warehouses `WH-A`.
  Legacy codes are kept for traceability (e.g. `suppliers.legacy_codes`).
- **Units of measure:** only EA, BOX, KG, L and M are allowed. Free text such as "pcs",
  "each" or "kgs" must be mapped; unknown units are not allowed.
- **Dates:** ISO 8601 (YYYY-MM-DD).
- **Uniqueness:** one supplier per tax ID; one customer per legal name + billing e-mail.

## 3. Data migration rules
Legacy data is migrated with an extract → validate → clean → de-duplicate → map → load
pipeline. Rows that break a mandatory rule (missing name, missing cost, unknown unit,
reference to a record that does not exist) are **rejected and reported**, never loaded with
broken references. Every migration produces a reconciliation report:
`rows read = rows loaded + duplicates merged + rows rejected`.

## 4. Change control
Changes to bank details, credit limits above $50,000 and supplier status require a second
person (four-eyes principle). All changes are audit-logged with user and timestamp.
