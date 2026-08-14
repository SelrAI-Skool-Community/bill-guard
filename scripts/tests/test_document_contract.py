"""Compatibility contract for prepared JSON documents."""

from harness import eq, raises, test, true
from billguard.cli import (
    DOCUMENT_SCHEMA_VERSION, DOCUMENT_V1_FIELDS, document_from_dict,
)


EXPECTED_V1_FIELDS = {
    "schema_version", "doc_id", "channel", "received_at", "supplier_name",
    "supplier_abn", "supplier_domain", "supplier_country", "buyer_name",
    "buyer_abn", "invoice_number", "issue_date", "due_date",
    "po_reference", "currency", "subtotal", "subtotal_cents", "tax",
    "tax_cents", "total", "total_cents", "balance_due",
    "balance_due_cents", "line_items", "payment", "doc_type_words",
    "raw_text", "text_source", "artifacts", "jurisdiction",
}


@test
def version_one_shape_is_frozen():
    eq(DOCUMENT_SCHEMA_VERSION, 1)
    eq(set(DOCUMENT_V1_FIELDS), EXPECTED_V1_FIELDS)


@test
def additive_unknown_field_is_ignored_not_fatal():
    doc = document_from_dict({
        "schema_version": 1,
        "doc_id": "contract-1",
        "supplier_name": "Example Pty Ltd",
        "total_cents": 1234,
        "future_extension": {"safe_to_ignore": True},
    })
    eq(doc.doc_id, "contract-1")
    eq(doc.total_cents, 1234)
    eq(doc.artifacts["_unknown_keys"], ["future_extension"])


@test
def legacy_unversioned_document_is_version_one():
    doc = document_from_dict({"doc_id": "legacy", "total": "12.34"})
    eq(doc.total_cents, 1234)
    eq(doc.artifacts["_unknown_keys"], [])


@test
def unsupported_version_is_named_not_misread():
    def load():
        document_from_dict({"schema_version": 2, "doc_id": "future"})

    raises(ValueError, load)
    try:
        load()
    except ValueError as exc:
        true("schema_version 2" in str(exc))


@test
def boolean_is_not_accepted_as_numeric_version_one():
    raises(ValueError, document_from_dict,
           {"schema_version": True, "doc_id": "ambiguous"})


if __name__ == "__main__":
    from harness import main
    main(__name__)
