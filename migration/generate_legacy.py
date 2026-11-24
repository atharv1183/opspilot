"""Generate deterministic, deliberately *dirty* legacy ERP export CSVs.

The files imitate what a 15-year-old on-prem ERP typically exports before a migration:

* duplicate supplier / customer master records (case, punctuation and suffix variants)
* mixed date formats (ISO, US, EU, compact, long-form)
* free-text units of measure ("each", "pcs", "Kilogram", "??")
* missing required fields and malformed numbers ("$1,234.50", "1,200")
* orphan references (inventory for unknown items, POs for unknown suppliers ...)

Usage::

    python migration/generate_legacy.py            # writes migration/legacy/*.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

SEED = 42
OUT_DIR = Path(__file__).parent / "legacy"

WAREHOUSE_VARIANTS = {
    "WH-A": ["WH-A", "A", "Whse A", "wh_a", "WAREHOUSE A"],
    "WH-B": ["WH-B", "B", "Whse B", "wh_b", "WAREHOUSE B"],
    "WH-C": ["WH-C", "C", "Whse C", "wh_c", "WAREHOUSE C"],
    "WH-D": ["WH-D", "D", "Whse D", "wh_d", "WAREHOUSE D"],
}
UOM_VARIANTS = {
    "EA": ["EA", "ea", "each", "Each", "PCS", "pcs", "pc", "unit"],
    "BOX": ["BOX", "bx", "Box", "CTN", "carton"],
    "KG": ["KG", "kg", "kgs", "Kilogram", "KILO"],
    "L": ["L", "ltr", "litre", "liter", "LTR"],
    "M": ["M", "m", "meter", "metre", "MTR"],
}
BAD_UOMS = ["??", "bundle", "N/A", "lot"]
CATEGORIES = {
    "Fasteners": (["Hex Bolt", "Lock Nut", "Flat Washer", "Wood Screw", "Rivet", "Anchor Bolt"],
                  ["M6", "M8", "M10", "M12", "M16"], ["EA", "BOX"]),
    "Electrical": (["Copper Cable", "Circuit Breaker", "Cable Tie", "Junction Box", "LED Panel"],
                   ["2.5mm", "4mm", "16A", "32A", "600mm"], ["EA", "M", "BOX"]),
    "Hydraulics": (["Hydraulic Hose", "Pressure Valve", "Hose Fitting", "Gear Pump", "Seal Kit"],
                   ["1/4in", "1/2in", "3/4in", "200bar", "350bar"], ["EA", "M"]),
    "Bearings": (["Ball Bearing", "Roller Bearing", "Pillow Block", "Thrust Bearing"],
                 ["6204", "6205", "6306", "22210", "UCP205"], ["EA"]),
    "Packaging": (["Stretch Film", "Corrugated Box", "Pallet Wrap", "Packing Tape", "Bubble Roll"],
                  ["small", "medium", "large", "48mm", "500mm"], ["EA", "BOX", "M"]),
    "Safety": (["Safety Gloves", "Hard Hat", "Safety Goggles", "Hi-Vis Vest", "Ear Plugs"],
               ["size M", "size L", "size XL", "class 2", "EN166"], ["EA", "BOX"]),
    "Raw Materials": (["Steel Sheet", "Aluminium Rod", "Copper Wire", "Resin Pellets", "Paint"],
                      ["1mm", "2mm", "10mm", "grade A", "RAL7035"], ["KG", "M", "L"]),
    "Lubricants": (["Hydraulic Oil", "Gear Oil", "Grease Cartridge", "Cutting Fluid"],
                   ["ISO32", "ISO46", "EP2", "5L", "20L"], ["L", "EA"]),
}  # fmt: skip
COUNTRY_VARIANTS = {
    "United States": ["USA", "US", "U.S.", "United States"],
    "Canada": ["Canada", "CA", "CAN"],
    "Mexico": ["Mexico", "MX"],
    "Germany": ["Germany", "DE", "Deutschland"],
    "India": ["India", "IN"],
    "China": ["China", "CN"],
    "United Kingdom": ["UK", "United Kingdom", "GB"],
}
LOW_STOCK_PER_WAREHOUSE = {"WH-A": 6, "WH-B": 4, "WH-C": 5, "WH-D": 3}


def fmt_date(d: date, rng: random.Random) -> str:
    """Render a date in one of several legacy formats."""
    style = rng.random()
    if style < 0.45:
        return d.isoformat()
    if style < 0.65:
        return d.strftime("%m/%d/%Y")
    if style < 0.78:
        return d.strftime("%d.%m.%Y")
    if style < 0.90:
        return d.strftime("%Y%m%d")
    return d.strftime("%b %d, %Y")


def money(v: float, rng: random.Random) -> str:
    """Render a number the way legacy exports do (sometimes with $ and thousands separators)."""
    r = rng.random()
    if r < 0.15:
        return f"${v:,.2f}"
    if r < 0.25:
        return f"{v:,.2f}"
    return f"{v:.2f}"


def qty_str(v: int, rng: random.Random) -> str:
    """Render a quantity with occasional formatting noise."""
    r = rng.random()
    if r < 0.08 and v >= 1000:
        return f"{v:,}"
    if r < 0.14:
        return f" {v} "
    if r < 0.20:
        return f"{v}.0"
    return str(v)


def rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def generate(out_dir: Path = OUT_DIR) -> dict[str, int]:
    """Write all legacy CSVs and return the row count per file."""
    rng = random.Random(SEED)
    fake = Faker("en_US")
    fake.seed_instance(SEED)
    counts: dict[str, int] = {}

    # ------------------------------------------------------------------ suppliers
    suppliers: list[dict] = []
    names: set[str] = set()
    while len(suppliers) < 280:
        name = fake.company()
        if name in names:
            continue
        names.add(name)
        i = len(suppliers)
        country = rng.choices(list(COUNTRY_VARIANTS), weights=[50, 10, 8, 10, 10, 8, 4])[0]
        status = rng.choices(["APPROVED", "PENDING", "BLOCKED"], weights=[76, 14, 10])[0]
        suppliers.append({
            "code": f"V{1001 + i}",
            "name": name,
            "tax_id": f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",
            "email": f"sales@{fake.domain_name()}",
            "phone": fake.phone_number(),
            "country": country,
            "terms": rng.choice([30, 30, 30, 45, 60]),
            "status": status,
            "rating": round(rng.uniform(2.5, 5.0), 1),
            "created": rand_date(rng, date(2009, 1, 1), date(2024, 12, 31)),
        })  # fmt: skip
    status_variants = {
        "APPROVED": ["A", "Active", "APPROVED", "approved"],
        "PENDING": ["P", "pending", "New"],
        "BLOCKED": ["B", "Blocked", "INACTIVE"],
    }
    terms_variants = ["NET{n}", "Net {n}", "{n} days", "N{n}"]
    rows = []
    for s in suppliers:
        rows.append([
            s["code"], s["name"], s["tax_id"], s["email"], s["phone"],
            rng.choice(COUNTRY_VARIANTS[s["country"]]),
            rng.choice(terms_variants).format(n=s["terms"]),
            rng.choice(status_variants[s["status"]]), str(s["rating"]),
            fmt_date(s["created"], rng),
        ])  # fmt: skip
    # 30 duplicates: same company re-keyed under a new vendor number with a name variant
    for j, s in enumerate(rng.sample(suppliers, 30)):
        variant = rng.choice([
            s["name"].upper(), s["name"].lower(), s["name"] + ".", s["name"] + "  ",
            s["name"].replace(",", ""), s["name"].replace(" and ", " & "),
        ])  # fmt: skip
        tax = s["tax_id"] if j % 5 else s["tax_id"].replace("-", "")
        if j % 7 == 0:
            tax = ""  # duplicate without tax id -> must match on normalised name
        rows.append([
            f"V{2001 + j}", variant, tax, "" if j % 3 == 0 else s["email"], s["phone"],
            rng.choice(COUNTRY_VARIANTS[s["country"]]), f"NET{s['terms']}",
            rng.choice(status_variants[s["status"]]), "",
            fmt_date(rand_date(rng, date(2015, 1, 1), date(2025, 6, 30)), rng),
        ])  # fmt: skip
    # 6 rows with missing name, 4 with an invalid e-mail
    for j in range(6):
        rows.append(
            [
                f"V{3001 + j}",
                "",
                f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",
                fake.email(),
                "",
                "USA",
                "NET30",
                "A",
                "",
                fmt_date(date(2020, 1, 1), rng),
            ]
        )
    for row in rng.sample(rows[:280], 4):
        row[3] = row[3].replace("@", " at ")
    rng.shuffle(rows)
    write_csv(out_dir / "suppliers.csv",
              ["SUPP_NO", "SUPP_NAME", "TAX_ID", "CONTACT_EMAIL", "PHONE", "COUNTRY", "TERMS",
               "STATUS", "RATING", "CREATED"], rows)  # fmt: skip
    counts["suppliers.csv"] = len(rows)
    approved_codes = [s["code"] for s in suppliers if s["status"] == "APPROVED"]
    dup_codes = [f"V{2001 + j}" for j in range(30)]

    # ------------------------------------------------------------------ customers
    customers: list[dict] = []
    rows = []
    for i in range(840):
        name = fake.company() if rng.random() < 0.8 else fake.name()
        c = {
            "code": f"C{10001 + i}",
            "name": name,
            "email": f"ap@{fake.domain_name()}" if rng.random() < 0.9 else "",
            "country": rng.choices(list(COUNTRY_VARIANTS), weights=[55, 12, 8, 8, 8, 5, 4])[0],
            "segment": rng.choice(["Distributor", "OEM", "Retail", "Contractor", "Government"]),
            "credit": rng.choice([5000, 10000, 25000, 50000, 100000, 250000]),
        }
        customers.append(c)
        credit = rng.choice([f"{c['credit']}", f"${c['credit']:,}", f"{c['credit']:,}.00"])
        rows.append(
            [
                c["code"],
                c["name"],
                c["email"],
                rng.choice(COUNTRY_VARIANTS[c["country"]]),
                c["segment"],
                credit,
                fmt_date(rand_date(rng, date(2010, 1, 1), date(2025, 12, 31)), rng),
            ]
        )
    for j, c in enumerate(rng.sample(customers, 15)):  # duplicates
        rows.append(
            [
                f"C{20001 + j}",
                c["name"].upper() if j % 2 else c["name"] + " ",
                c["email"].upper(),
                "USA",
                c["segment"],
                str(c["credit"]),
                fmt_date(date(2023, 3, 1), rng),
            ]
        )
    for j in range(8):  # missing names
        rows.append(
            [
                f"C{30001 + j}",
                "",
                fake.email(),
                "USA",
                "Retail",
                "5000",
                fmt_date(date(2021, 1, 1), rng),
            ]
        )
    for j in range(4):  # garbage credit limits
        rows.append(
            [
                f"C{31001 + j}",
                fake.company(),
                fake.email(),
                "Canada",
                "OEM",
                rng.choice(["TBD", "-500", "n/a", "unlimited"]),
                "2022-02-30",
            ]
        )
    rng.shuffle(rows)
    write_csv(
        out_dir / "customers.csv",
        ["CUST_NO", "CUST_NAME", "EMAIL", "COUNTRY", "SEGMENT", "CREDIT_LIMIT", "CREATED"],
        rows,
    )
    counts["customers.csv"] = len(rows)

    # ------------------------------------------------------------------ items
    items: list[dict] = []
    seen_names: set[str] = set()
    cats = list(CATEGORIES)
    while len(items) < 600:
        cat = rng.choice(cats)
        nouns, specs, uoms = CATEGORIES[cat]
        nm = f"{rng.choice(nouns)} {rng.choice(specs)}"
        if nm in seen_names:
            nm = f"{nm} {rng.choice(['Pro', 'HD', 'Std', 'Eco', 'XL', 'Mk2', 'Plus'])}"
        if nm in seen_names:
            continue
        seen_names.add(nm)
        cost = round(rng.uniform(0.4, 450.0) if cat != "Bearings" else rng.uniform(4, 180), 2)
        items.append({
            "no": 1001 + len(items), "name": nm, "category": cat, "uom": rng.choice(uoms),
            "cost": cost, "price": round(cost * rng.uniform(1.25, 1.8), 2),
            "lead": rng.choice([3, 5, 7, 7, 10, 14, 21, 30]),
            "pref": rng.choice(approved_codes) if rng.random() < 0.85 else rng.choice(dup_codes),
        })  # fmt: skip

    def item_no(n: int) -> str:
        return rng.choice([f"P-{n}", f"P-{n}", f"p-{n}", f" P-{n} ", f"P{n}"])

    rows = []
    for it in items:
        rows.append(
            [
                item_no(it["no"]),
                it["name"],
                it["category"],
                rng.choice(UOM_VARIANTS[it["uom"]]),
                money(it["cost"], rng),
                money(it["price"], rng) if rng.random() > 0.05 else "",
                rng.choice([str(it["lead"]), f"{it['lead']} days"]),
                it["pref"],
            ]
        )
    bad_uom_idx = rng.sample(range(600), 10)
    for k in bad_uom_idx:
        rows[k][3] = rng.choice(BAD_UOMS)
    missing_cost_idx = rng.sample([k for k in range(600) if k not in bad_uom_idx], 8)
    for k in missing_cost_idx:
        rows[k][4] = ""
    for k in rng.sample(range(600), 6):  # orphan preferred supplier -> nulled, not rejected
        rows[k][7] = "V9999"
    rows += [list(r) for r in rng.sample(rows, 5)]  # exact duplicate rows
    rejected_items = {items[k]["no"] for k in bad_uom_idx + missing_cost_idx}
    write_csv(out_dir / "items.csv",
              ["ITEM_NO", "DESCRIPTION", "CATEGORY", "UOM", "STD_COST", "LIST_PRICE",
               "LEAD_TIME", "PREF_SUPPLIER"], rows)  # fmt: skip
    counts["items.csv"] = len(rows)
    good_items = [it for it in items if it["no"] not in rejected_items]

    # ------------------------------------------------------------------ inventory
    rows = []
    inv_keys: list[tuple[int, str]] = []
    for it in items:
        for wh in rng.sample(list(WAREHOUSE_VARIANTS), 3):
            inv_keys.append((it["no"], wh))
    low_pool: dict[str, list[tuple[int, str]]] = {wh: [] for wh in WAREHOUSE_VARIANTS}
    for key in inv_keys:
        if key[0] not in rejected_items:
            low_pool[key[1]].append(key)
    low_keys: set[tuple[int, str]] = set()
    for wh, n in LOW_STOCK_PER_WAREHOUSE.items():
        low_keys.update(rng.sample(low_pool[wh], n))
    for no, wh in inv_keys:
        reorder = rng.choice([10, 20, 25, 40, 50, 75, 100, 150, 200])
        if (no, wh) in low_keys:
            on_hand = rng.randint(0, int(reorder * 0.8))
        else:
            on_hand = rng.randint(int(reorder * 1.6) + 5, reorder * 12 + 50)
        rows.append(
            [
                item_no(no),
                rng.choice(WAREHOUSE_VARIANTS[wh]),
                qty_str(on_hand, rng),
                str(reorder),
                str(reorder * rng.choice([2, 3, 4])),
                fmt_date(rand_date(rng, date(2025, 1, 1), date(2026, 10, 31)), rng),
            ]
        )
    for j in range(25):  # orphan items
        rows.append(
            [
                f"P-{9001 + j}",
                "WH-A",
                str(rng.randint(1, 500)),
                "20",
                "60",
                fmt_date(date(2026, 3, 1), rng),
            ]
        )
    for j in range(5):  # unknown warehouse
        rows.append([item_no(good_items[j]["no"]), "WH-Z", "15", "10", "30", "2026-01-15"])
    for j in range(6):  # negative quantities
        rows.append(
            [
                item_no(good_items[50 + j]["no"]),
                "WH-D",
                f"-{rng.randint(1, 40)}",
                "10",
                "30",
                "2026-02-01",
            ]
        )
    rng.shuffle(rows)
    write_csv(
        out_dir / "inventory.csv",
        ["ITEM_NO", "WHSE", "QTY_ON_HAND", "REORDER_PT", "REORDER_QTY", "LAST_COUNT"],
        rows,
    )
    counts["inventory.csv"] = len(rows)

    # ------------------------------------------------------------------ purchase orders (flat)
    po_status_variants = {
        "CLOSED": ["CLOSED", "Closed", "CLSD"], "RECEIVED": ["RECEIVED", "RECV"],
        "PARTIALLY_RECEIVED": ["PARTIAL", "Part Recv"], "SENT": ["SENT", "Open", "OPEN"],
        "APPROVED": ["APPROVED", "Released"], "CANCELLED": ["CANCELLED", "cancelled", "CNX"],
        "PENDING_APPROVAL": ["PENDING", "Awaiting Approval"],
    }  # fmt: skip
    rows = []
    for i in range(380):
        status = rng.choices(list(po_status_variants), weights=[35, 20, 8, 20, 9, 4, 4])[0]
        if status in ("SENT", "APPROVED", "PARTIALLY_RECEIVED", "PENDING_APPROVAL"):
            po_date = rand_date(rng, date(2026, 9, 15), date(2026, 11, 20))
        else:
            po_date = rand_date(rng, date(2025, 6, 1), date(2026, 10, 15))
        supplier = rng.choice(approved_codes) if rng.random() < 0.93 else rng.choice(dup_codes)
        if i % 60 == 7:
            supplier = f"V{8001 + i}"  # orphan supplier -> whole PO rejected
        wh = rng.choice(list(WAREHOUSE_VARIANTS))
        po_no = rng.choice(
            [f"PO{10001 + i}", f"PO-{10001 + i}", f"po-{10001 + i}", f"PO {10001 + i}"]
        )
        lines = rng.sample(good_items, rng.randint(1, 4))
        exp = po_date + timedelta(days=max(it["lead"] for it in lines) + rng.randint(0, 10))
        for ln, it in enumerate(lines, 1):
            q = rng.choice([10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000])
            price = round(it["cost"] * rng.uniform(0.9, 1.1), 2)
            rows.append(
                [
                    po_no,
                    fmt_date(po_date, rng),
                    supplier,
                    rng.choice(WAREHOUSE_VARIANTS[wh]),
                    str(ln),
                    item_no(it["no"]),
                    qty_str(q, rng),
                    money(price, rng),
                    rng.choice(po_status_variants[status]),
                    fmt_date(exp, rng),
                ]
            )
    for j in range(12):  # orphan item lines
        r = list(rng.choice(rows))
        r[4], r[5] = str(10 + j), f"P-{9500 + j}"
        rows.append(r)
    for j in range(6):  # missing quantities
        r = list(rng.choice(rows))
        r[4], r[6] = str(20 + j), ""
        rows.append(r)
    write_csv(out_dir / "purchase_orders.csv",
              ["PO_NO", "PO_DATE", "SUPP_NO", "WHSE", "LINE_NO", "ITEM_NO", "QTY", "UNIT_PRICE",
               "STATUS", "EXPECTED_DATE"], rows)  # fmt: skip
    counts["purchase_orders.csv"] = len(rows)

    # ------------------------------------------------------------------ sales orders (flat)
    so_status_variants = {
        "CLOSED": ["CLOSED", "Complete"], "INVOICED": ["INVOICED", "Billed"],
        "SHIPPED": ["SHIPPED", "Shipped"], "PARTIALLY_SHIPPED": ["PART SHIP", "Partial"],
        "ALLOCATED": ["ALLOCATED", "Alloc"], "OPEN": ["OPEN", "New", "Entered"],
        "CREDIT_HOLD": ["CREDIT HOLD", "HOLD-CR"], "CANCELLED": ["CANCELLED", "CNX"],
    }  # fmt: skip
    rows = []
    for i in range(500):
        status = rng.choices(list(so_status_variants), weights=[30, 15, 10, 5, 12, 20, 4, 4])[0]
        if status in ("OPEN", "ALLOCATED", "PARTIALLY_SHIPPED", "CREDIT_HOLD"):
            so_date = rand_date(rng, date(2026, 10, 1), date(2026, 11, 22))
        else:
            so_date = rand_date(rng, date(2025, 6, 1), date(2026, 10, 31))
        cust = rng.choice(customers)["code"]
        if i % 70 == 11:
            cust = f"C{88001 + i}"  # orphan customer
        wh = rng.choice(list(WAREHOUSE_VARIANTS))
        so_no = rng.choice([f"SO{20001 + i}", f"SO-{20001 + i}", f"so-{20001 + i}"])
        req = so_date + timedelta(days=rng.randint(3, 30))
        for ln, it in enumerate(rng.sample(good_items, rng.randint(1, 4)), 1):
            q = rng.choice([1, 2, 5, 10, 12, 20, 25, 40, 50, 100])
            price = round(it["price"] * rng.uniform(0.9, 1.05), 2)
            rows.append(
                [
                    so_no,
                    fmt_date(so_date, rng),
                    cust,
                    rng.choice(WAREHOUSE_VARIANTS[wh]),
                    str(ln),
                    item_no(it["no"]),
                    qty_str(q, rng),
                    money(price, rng),
                    rng.choice(so_status_variants[status]),
                    fmt_date(req, rng),
                ]
            )
    for j in range(10):  # orphan items
        r = list(rng.choice(rows))
        r[4], r[5] = str(10 + j), f"P-{9700 + j}"
        rows.append(r)
    for j in range(5):  # unparseable order dates
        r = list(rng.choice(rows))
        r[1], r[4] = "31/31/2026", str(30 + j)
        rows.append(r)
    write_csv(out_dir / "sales_orders.csv",
              ["SO_NO", "ORDER_DATE", "CUST_NO", "WHSE", "LINE_NO", "ITEM_NO", "QTY",
               "UNIT_PRICE", "STATUS", "REQ_DATE"], rows)  # fmt: skip
    counts["sales_orders.csv"] = len(rows)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    counts = generate(args.out)
    for name, n in counts.items():
        print(f"{name:<22} {n:>6} rows")
    print(f"{'TOTAL':<22} {sum(counts.values()):>6} rows")


if __name__ == "__main__":
    main()
