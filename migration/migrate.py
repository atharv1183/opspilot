"""Legacy ERP -> OpsPilot data migration (extract, validate, clean, de-duplicate, map, load).

Pipeline per entity::

    CSV rows --extract--> dict --pydantic validate/clean--> record  (or rejected + reason)
            --dedupe/merge--> survivors --map FKs (legacy code -> new id)--> ORM rows
            --load (one DB transaction for everything)--> reconciliation report

Usage::

    python migration/migrate.py                      # load migration/legacy/*.csv
    python migration/migrate.py --dry-run            # validate + report, no DB writes
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # allow `python migration/migrate.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from migration.cleaning import name_key
from migration.schemas import (
    CustomerRecord,
    InventoryRecord,
    ItemRecord,
    POLineRecord,
    SOLineRecord,
    SupplierRecord,
    rejection_reason,
)

LEGACY_DIR = Path(__file__).parent / "legacy"
REPORT_PATH = Path(__file__).parent / "report.md"

WAREHOUSES = [
    ("WH-A", "Warehouse A", "Chicago, IL"),
    ("WH-B", "Warehouse B", "Dallas, TX"),
    ("WH-C", "Warehouse C", "Atlanta, GA"),
    ("WH-D", "Warehouse D", "Reno, NV"),
]

# --------------------------------------------------------------------------- reporting


@dataclass
class EntityStats:
    """Reconciliation counters for one legacy file."""

    source: str
    target: str
    read: int = 0
    loaded: int = 0
    merged: int = 0
    rejected: Counter = field(default_factory=Counter)
    warnings: Counter = field(default_factory=Counter)
    examples: dict[str, str] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        """Count a rejection, grouping value-specific reasons (keeps one example)."""
        base = re.sub(r"\s*(\(.*\)|'.*')$", "", reason)
        self.rejected[base] += 1
        self.examples.setdefault(base, reason)

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())

    def reconciles(self) -> bool:
        """read == loaded + merged duplicates + rejected."""
        return self.read == self.loaded + self.merged + self.rejected_total


@dataclass
class MigrationResult:
    stats: dict[str, EntityStats]
    entity_counts: dict[str, int]
    duration_s: float
    dry_run: bool
    merge_examples: list[str]

    @property
    def total_read(self) -> int:
        return sum(s.read for s in self.stats.values())

    @property
    def total_loaded(self) -> int:
        return sum(s.loaded for s in self.stats.values())


def extract(path: Path) -> list[dict[str, str]]:
    """Read a legacy CSV into a list of raw string dicts."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- transform steps


def dedupe_suppliers(
    records: list[SupplierRecord], stats: EntityStats, examples: list[str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Merge duplicate suppliers.

    Two records are the same supplier if their normalised tax ids match, or if their normalised
    names match and at least one of them has no tax id. The earliest-created record survives;
    blank fields are back-filled from the duplicates and all legacy codes are kept.
    Returns (survivor dicts, legacy_code -> survivor index).
    """
    records = sorted(records, key=lambda r: (r.created or datetime.max.date(), r.legacy_code))
    groups: list[dict[str, Any]] = []
    by_tax: dict[str, int] = {}
    by_name: dict[str, int] = {}
    code_to_group: dict[str, int] = {}
    for r in records:
        key = name_key(r.name)
        gi = by_tax.get(r.tax_id) if r.tax_id else None
        if gi is None and key in by_name:
            cand = by_name[key]
            if r.tax_id is None or groups[cand]["tax_id"] is None:
                gi = cand
        if gi is None:
            gi = len(groups)
            groups.append({**r.model_dump(), "legacy_codes": [r.legacy_code]})
            if r.tax_id:
                by_tax[r.tax_id] = gi
            by_name.setdefault(key, gi)
        else:
            g = groups[gi]
            g["legacy_codes"].append(r.legacy_code)
            for f in ("tax_id", "email", "phone", "country", "rating"):
                if g[f] is None and getattr(r, f) is not None:
                    g[f] = getattr(r, f)
            stats.merged += 1
            if len(examples) < 5:
                examples.append(
                    f'`{r.legacy_code}` "{r.name}" -> merged into '
                    f'`{g["legacy_codes"][0]}` "{g["name"]}"'
                )
        code_to_group[r.legacy_code] = gi
    return groups, code_to_group


def dedupe_customers(
    records: list[CustomerRecord], stats: EntityStats
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Merge customers with the same normalised name and matching (or missing) e-mail."""
    records = sorted(records, key=lambda r: (r.created or datetime.max.date(), r.legacy_code))
    groups: list[dict[str, Any]] = []
    by_name: dict[str, list[int]] = defaultdict(list)
    code_to_group: dict[str, int] = {}
    for r in records:
        key = name_key(r.name)
        gi = None
        for cand in by_name.get(key, []):
            ce = groups[cand]["email"]
            if ce is None or r.email is None or ce == r.email:
                gi = cand
                break
        if gi is None:
            gi = len(groups)
            groups.append(r.model_dump())
            by_name[key].append(gi)
        else:
            stats.merged += 1
            if groups[gi]["email"] is None:
                groups[gi]["email"] = r.email
        code_to_group[r.legacy_code] = gi
    return groups, code_to_group


def _validate(rows: list[dict[str, str]], model: Any, stats: EntityStats) -> list[Any]:
    out = []
    for row in rows:
        try:
            out.append(model.from_row(row))
        except ValidationError as err:
            stats.reject(rejection_reason(err))
    return out


# --------------------------------------------------------------------------- main pipeline


def run_migration(
    session: Session | None,
    legacy_dir: Path = LEGACY_DIR,
    *,
    dry_run: bool = False,
    truncate: bool = True,
) -> MigrationResult:
    """Run the full migration. With ``dry_run`` (or no session) nothing is written."""
    from opspilot.db import models as m
    from opspilot.db.enums import (
        POStatus,
        SOStatus,
        SupplierStatus,
        UnitOfMeasure,
    )

    t0 = time.perf_counter()
    stats = {
        "suppliers": EntityStats("suppliers.csv", "suppliers"),
        "customers": EntityStats("customers.csv", "customers"),
        "items": EntityStats("items.csv", "items"),
        "inventory": EntityStats("inventory.csv", "inventory"),
        "purchase_orders": EntityStats("purchase_orders.csv", "purchase_orders + po_lines"),
        "sales_orders": EntityStats("sales_orders.csv", "sales_orders + so_lines"),
    }
    merge_examples: list[str] = []

    # ---- suppliers
    raw = extract(legacy_dir / "suppliers.csv")
    st = stats["suppliers"]
    st.read = len(raw)
    for row in raw:
        if row["CONTACT_EMAIL"].strip() and "@" not in row["CONTACT_EMAIL"]:
            st.warnings["invalid e-mail nulled"] += 1
    sup_records = _validate(raw, SupplierRecord, st)
    groups, sup_code_map = dedupe_suppliers(sup_records, st, merge_examples)
    # New supplier codes are assigned in legacy-code order of the surviving record.
    order = sorted(range(len(groups)), key=lambda gi: groups[gi]["legacy_codes"][0])
    pos_of_group = {gi: pos for pos, gi in enumerate(order)}
    suppliers = [groups[gi] for gi in order]
    sup_code_to_pos = {code: pos_of_group[gi] for code, gi in sup_code_map.items()}
    survivor_codes = {g["legacy_codes"][0] for g in suppliers}
    st.loaded = len(suppliers)

    # ---- customers
    raw = extract(legacy_dir / "customers.csv")
    st = stats["customers"]
    st.read = len(raw)
    cus_records = _validate(raw, CustomerRecord, st)
    for r in cus_records:
        if r.credit_limit is None:
            st.warnings["invalid credit limit defaulted to 0 (flagged for credit review)"] += 1
        if r.created is None:
            st.warnings["invalid created date ignored"] += 1
    customers, cus_code_map = dedupe_customers(cus_records, st)
    st.loaded = len(customers)

    # ---- items
    raw = extract(legacy_dir / "items.csv")
    st = stats["items"]
    st.read = len(raw)
    item_records = _validate(raw, ItemRecord, st)
    items: dict[str, ItemRecord] = {}
    for r in item_records:
        if r.sku in items:
            st.merged += 1  # exact duplicate export row
            continue
        items[r.sku] = r
    for r in items.values():
        if r.list_price is None:
            st.warnings["missing list price derived as cost x 1.35"] += 1
        if r.preferred_supplier_code and r.preferred_supplier_code not in sup_code_to_pos:
            st.warnings["orphan preferred supplier nulled"] += 1
        elif r.preferred_supplier_code and r.preferred_supplier_code not in survivor_codes:
            st.warnings["preferred supplier remapped to surviving duplicate"] += 1
    st.loaded = len(items)

    # ---- inventory
    raw = extract(legacy_dir / "inventory.csv")
    st = stats["inventory"]
    st.read = len(raw)
    inv_records = _validate(raw, InventoryRecord, st)
    wh_codes = {w[0] for w in WAREHOUSES}
    inventory: dict[tuple[str, str], InventoryRecord] = {}
    for r in inv_records:
        if r.sku not in items:
            st.reject("orphan item reference (unknown or rejected item)")
        elif r.warehouse not in wh_codes:
            st.reject("unknown warehouse")
        elif (r.sku, r.warehouse) in inventory:
            st.merged += 1
        else:
            inventory[(r.sku, r.warehouse)] = r
    st.loaded = len(inventory)

    # ---- purchase orders (flat line export -> header + lines)
    def build_orders(
        file: str, model: Any, st: EntityStats, party_map: dict[str, int], party: str
    ) -> dict[str, dict[str, Any]]:
        raw = extract(legacy_dir / file)
        st.read = len(raw)
        lines = _validate(raw, model, st)
        orders: dict[str, dict[str, Any]] = {}
        for ln in lines:
            if ln.party_code not in party_map:
                st.reject(f"orphan {party} reference")
                continue
            if ln.sku not in items:
                st.reject("orphan item reference")
                continue
            o = orders.setdefault(ln.doc_number, {"header": ln, "lines": {}})
            if ln.line_no in o["lines"]:
                st.merged += 1
                continue
            o["lines"][ln.line_no] = ln
        st.loaded = sum(len(o["lines"]) for o in orders.values())
        return orders

    pos = build_orders(
        "purchase_orders.csv", POLineRecord, stats["purchase_orders"], sup_code_to_pos, "supplier"
    )
    sos = build_orders(
        "sales_orders.csv", SOLineRecord, stats["sales_orders"], cus_code_map, "customer"
    )

    entity_counts = {
        "warehouses": len(WAREHOUSES), "suppliers": len(suppliers), "customers": len(customers),
        "items": len(items), "inventory": len(inventory), "purchase_orders": len(pos),
        "po_lines": sum(len(o["lines"]) for o in pos.values()), "sales_orders": len(sos),
        "so_lines": sum(len(o["lines"]) for o in sos.values()),
    }  # fmt: skip

    # ---- load (single transaction)
    if session is not None and not dry_run:
        with session.begin():
            if truncate:
                session.execute(text(
                    "TRUNCATE " + ", ".join(m.ERP_TABLES) + " RESTART IDENTITY CASCADE"
                ))  # fmt: skip
            whs = {code: m.Warehouse(code=code, name=n, city=c) for code, n, c in WAREHOUSES}
            session.add_all(whs.values())

            sup_rows = []
            for i, g in enumerate(suppliers, 1):
                sup_rows.append(m.Supplier(
                    supplier_code=f"SUP-{i:04d}", name=g["name"], tax_id=g["tax_id"],
                    email=g["email"], phone=g["phone"], country=g["country"],
                    payment_terms=g["payment_terms"], status=SupplierStatus(g["status"]),
                    rating=g["rating"], legacy_codes=sorted(g["legacy_codes"]),
                ))  # fmt: skip
            session.add_all(sup_rows)

            cus_rows = []
            for i, g in enumerate(customers, 1):
                cus = m.Customer(
                    customer_code=f"CUS-{i:05d}", name=g["name"], email=g["email"],
                    country=g["country"], segment=g["segment"],
                    credit_limit=g["credit_limit"] if g["credit_limit"] is not None else 0,
                )  # fmt: skip
                if g["created"]:
                    cus.created_at = datetime.combine(g["created"], datetime.min.time(), UTC)
                cus_rows.append(cus)
            session.add_all(cus_rows)
            session.flush()

            item_rows: dict[str, Any] = {}
            for sku, r in sorted(items.items(), key=lambda kv: int(kv[0].split("-")[1])):
                pref = sup_code_to_pos.get(r.preferred_supplier_code or "")
                item_rows[sku] = m.Item(
                    sku=sku, name=r.name, category=r.category, uom=UnitOfMeasure(r.uom),
                    standard_cost=r.standard_cost,
                    list_price=r.list_price or (r.standard_cost * Decimal("1.35")).quantize(
                        Decimal("0.01")),
                    lead_time_days=r.lead_time_days,
                    preferred_supplier_id=sup_rows[pref].id if pref is not None else None,
                )  # fmt: skip
            session.add_all(item_rows.values())
            session.flush()

            session.add_all(
                m.Inventory(
                    item_id=item_rows[sku].id, warehouse_id=whs[wh].id, qty_on_hand=r.qty_on_hand,
                    reorder_level=r.reorder_level, reorder_qty=r.reorder_qty,
                    last_counted_at=r.last_counted,
                )
                for (sku, wh), r in inventory.items()
            )  # fmt: skip

            for po_no, o in sorted(pos.items(), key=lambda kv: int(kv[0].split("-")[1])):
                h: POLineRecord = o["header"]
                lines = [
                    m.POLine(line_no=n, item_id=item_rows[ln.sku].id, quantity=ln.quantity,
                             unit_price=ln.unit_price)
                    for n, ln in sorted(o["lines"].items())
                ]  # fmt: skip
                session.add(m.PurchaseOrder(
                    po_number=po_no, supplier_id=sup_rows[sup_code_to_pos[h.party_code]].id,
                    warehouse_id=whs[h.warehouse].id, status=POStatus(h.status),
                    order_date=h.order_date, expected_date=h.due_date,
                    total_amount=sum(ln.quantity * ln.unit_price for ln in lines),
                    approved_by="legacy" if h.status not in ("PENDING_APPROVAL",) else None,
                    lines=lines,
                ))  # fmt: skip

            for so_no, o in sorted(sos.items(), key=lambda kv: int(kv[0].split("-")[1])):
                h2: SOLineRecord = o["header"]
                lines2 = [
                    m.SOLine(line_no=n, item_id=item_rows[ln.sku].id, quantity=ln.quantity,
                             unit_price=ln.unit_price)
                    for n, ln in sorted(o["lines"].items())
                ]  # fmt: skip
                session.add(m.SalesOrder(
                    so_number=so_no, customer_id=cus_rows[cus_code_map[h2.party_code]].id,
                    warehouse_id=whs[h2.warehouse].id, order_date=h2.order_date,
                    requested_date=h2.due_date, status=SOStatus(h2.status),
                    total_amount=sum(ln.quantity * ln.unit_price for ln in lines2), lines=lines2,
                ))  # fmt: skip
            session.flush()

    return MigrationResult(
        stats=stats,
        entity_counts=entity_counts,
        duration_s=time.perf_counter() - t0,
        dry_run=dry_run or session is None,
        merge_examples=merge_examples,
    )


# --------------------------------------------------------------------------- report


def render_report(result: MigrationResult) -> str:
    """Render the reconciliation report as Markdown."""
    s = result.stats
    out = [
        "# Legacy data migration – reconciliation report",
        "",
        f"_Generated by `migration/migrate.py` on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC "
        f"({'DRY RUN, nothing written' if result.dry_run else 'loaded in one transaction'}, "
        f"{result.duration_s:.1f}s)._",
        "",
        "## Summary",
        "",
        "| Source file | Target table(s) | Rows read | Loaded | Duplicates merged | Rejected |"
        " Reconciles |",
        "|---|---|---:|---:|---:|---:|:---:|",
    ]
    for st in s.values():
        out.append(
            f"| `{st.source}` | {st.target} | {st.read:,} | {st.loaded:,} | {st.merged:,} | "
            f"{st.rejected_total:,} | {'yes' if st.reconciles() else '**NO**'} |"
        )
    tot_merged = sum(x.merged for x in s.values())
    tot_rej = sum(x.rejected_total for x in s.values())
    out += [
        f"| **Total** | | **{result.total_read:,}** | **{result.total_loaded:,}** | "
        f"**{tot_merged:,}** | **{tot_rej:,}** | |",
        "",
        "`read = loaded + duplicates merged + rejected` is checked for every file.",
        "",
        "## Records created in the new schema",
        "",
        "| Table | Rows |",
        "|---|---:|",
    ]
    out += [f"| {k} | {v:,} |" for k, v in result.entity_counts.items()]
    out += ["", "## Rejected rows by reason", "", "| Source | Reason | Rows | Example |",
            "|---|---|---:|---|"]  # fmt: skip
    for st in s.values():
        for reason, n in st.rejected.most_common():
            ex = st.examples.get(reason, reason)
            out.append(f"| `{st.source}` | {reason} | {n} | {ex if ex != reason else ''} |")
    out += ["", "## Auto-fixes and warnings (rows loaded)", "", "| Source | Fix | Rows |",
            "|---|---|---:|"]  # fmt: skip
    for st in s.values():
        for reason, n in st.warnings.most_common():
            out.append(f"| `{st.source}` | {reason} | {n} |")
    out += [
        "",
        "## Cleaning rules applied",
        "",
        "- **Dates**: ISO, `MM/DD/YYYY`, `DD.MM.YYYY`, `YYYYMMDD` and `Mon DD, YYYY` normalised "
        "to ISO dates; unparseable required dates reject the row.",
        "- **Units of measure**: free text (`each`, `pcs`, `kgs`, `carton`, `litre` ...) mapped "
        "to `EA/BOX/KG/L/M`; unknown units (`??`, `bundle`) reject the item.",
        "- **Numbers**: `$1,234.50`, `1,200`, ` 35 `, `12.0` parsed as decimals; negative "
        "quantities rejected.",
        "- **Codes**: `p-1001`/`P1001` -> `ITM-1001`, `po 10001` -> `PO-10001`, "
        "`Whse B` -> `WH-B`.",
        "- **De-duplication**: suppliers by normalised tax id, else normalised name; customers by "
        "normalised name + e-mail. Earliest record survives, blanks back-filled, legacy codes kept "
        "on `suppliers.legacy_codes`, and POs that referenced a duplicate vendor number are "
        "re-pointed to the survivor.",
        "- **Referential integrity**: rows pointing to unknown items, suppliers, customers or "
        "warehouses are rejected (orphans) instead of loaded with broken FKs.",
        "",
    ]
    if result.merge_examples:
        out += ["## Example supplier merges", ""]
        out += [f"- {e}" for e in result.merge_examples]
        out.append("")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy ERP CSV exports into OpsPilot.")
    parser.add_argument("--legacy-dir", type=Path, default=LEGACY_DIR)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--dry-run", action="store_true", help="validate only, no DB writes")
    args = parser.parse_args()

    if not (args.legacy_dir / "suppliers.csv").exists():
        from migration.generate_legacy import generate

        generate(args.legacy_dir)

    session = None
    if not args.dry_run:
        from opspilot.db.session import get_sessionmaker

        session = get_sessionmaker()()
    try:
        result = run_migration(session, args.legacy_dir, dry_run=args.dry_run)
    finally:
        if session is not None:
            session.close()
    args.report.write_text(render_report(result), encoding="utf-8")
    print(
        f"read {result.total_read:,} legacy rows, loaded {result.total_loaded:,} "
        f"in {result.duration_s:.1f}s -> report: {args.report}"
    )


if __name__ == "__main__":
    main()
