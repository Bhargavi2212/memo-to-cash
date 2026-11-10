"""Demo data (P1 D18). Step 7 adds `build_csv`; step 9 adds `start_demo`.

`build_csv(D, buyer_email)` writes the §Seed dataset (IMPLEMENTATION_PLAN) as an import
file with every date relative to D (invariant 8), so a demo copy goes through the same
importer as a real ERP export.
"""

import csv
import io
from datetime import date

from backend.core.dates import offset

COLUMNS = (
    "record_type",
    "buyer_name",
    "buyer_email",
    "memo_no",
    "sent_on",
    "due_back_on",
    "sku",
    "description",
    "price",
    "returned_on",
    "return_ref",
    "invoice_no",
    "issued_on",
    "due_on",
)

BUYER_NAME = "Northside Jewelers"

# memo_no, sent (days from D), [(sku, description, price $, state)]
# state: ("KEPT", invoice_no) | ("RETURNED", days, ref) | ("OUT", due back days)
_MEMOS = [
    (
        "M-201",
        -75,
        [
            ("S-101", "Round 1.01ct", "2350", ("KEPT", "INV-1038")),
            ("S-102", "Oval 1.20ct", "1200", ("RETURNED", -38, "R-311")),
            ("S-103", "Pendant", "800", ("OUT", 11)),
            ("S-104", "Stud pair", "1150", ("KEPT", "INV-1038")),
        ],
    ),
    (
        "M-207",
        -45,
        [
            ("S-110", "Cushion 0.90ct", "1900", ("KEPT", "INV-1042")),
            ("S-111", "Tennis bracelet", "1600", ("KEPT", "INV-1042")),
            ("S-112", "Emerald 1.50ct", "2100", ("RETURNED", -21, "R-318")),
            ("S-113", "Halo ring", "950", ("RETURNED", -21, "R-318")),
        ],
    ),
    (
        "M-212",
        -20,
        [
            ("S-120", "Solitaire ring", "1420", ("KEPT", "INV-1051")),
            ("S-121", "Hoops", "640", ("RETURNED", -6, "R-325")),
            ("S-122", "Pear 0.70ct", "1300", ("RETURNED", -6, "R-325")),
            ("S-123", "Bangle", "780", ("OUT", 25)),
        ],
    ),
]
# invoice_no, memo_no, issued (days), due (days)
_INVOICES = [
    ("INV-1038", "M-201", -48, -18),
    ("INV-1042", "M-207", -20, 10),
    ("INV-1051", "M-212", -5, 25),
]


def build_csv(d: date, buyer_email: str) -> str:
    """The §Seed dataset as import CSV, dates relative to D."""
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerow({"record_type": "BUYER", "buyer_name": BUYER_NAME, "buyer_email": buyer_email})
    for memo_no, sent, lines in _MEMOS:
        due_back = [s[1] for *_, s in lines if s[0] == "OUT"]
        w.writerow(
            {
                "record_type": "MEMO",
                "memo_no": memo_no,
                "buyer_email": buyer_email,
                "sent_on": offset(d, sent).isoformat(),
                "due_back_on": offset(d, max(due_back)).isoformat() if due_back else "",
            }
        )
        for sku, desc, price, _ in lines:
            w.writerow(
                {
                    "record_type": "LINE",
                    "memo_no": memo_no,
                    "sku": sku,
                    "description": desc,
                    "price": price,
                }
            )
        for sku, _, _, state in lines:
            if state[0] == "RETURNED":
                w.writerow(
                    {
                        "record_type": "RETURN",
                        "memo_no": memo_no,
                        "sku": sku,
                        "returned_on": offset(d, state[1]).isoformat(),
                        "return_ref": state[2],
                    }
                )
    for inv_no, memo_no, issued, due in _INVOICES:
        w.writerow(
            {
                "record_type": "INVOICE",
                "invoice_no": inv_no,
                "memo_no": memo_no,
                "issued_on": offset(d, issued).isoformat(),
                "due_on": offset(d, due).isoformat(),
            }
        )
        for m_no, _, lines in _MEMOS:
            for sku, *_, state in lines:
                if m_no == memo_no and state == ("KEPT", inv_no):
                    w.writerow(
                        {
                            "record_type": "INVOICE_LINE",
                            "invoice_no": inv_no,
                            "memo_no": memo_no,
                            "sku": sku,
                        }
                    )
    return out.getvalue()
