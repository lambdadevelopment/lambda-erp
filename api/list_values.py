"""Shared value suggestions for the generic master/document list filters."""


def distinct_list_values(db, table: str, field: str, query: str = "", limit: int = 200) -> list:
    """Return distinct, non-empty values for one validated table column.

    ``field`` is checked against the live schema before it is interpolated.
    Prefix matching keeps autocomplete useful without loading a whole
    high-cardinality column into the browser.
    """
    if field not in db._get_table_columns(table):
        raise KeyError(field)
    # An access profile can hide the type, the field, or some of the rows.
    from lambda_erp import permissions
    permissions.require(table, "read")
    permissions.require_fields_visible(table, [field], "list values of")

    where = [f'"{field}" IS NOT NULL', f'CAST("{field}" AS TEXT) <> \'\'', *permissions.record_conditions(table)]
    params: list = []
    if query:
        where.append(f'LOWER(CAST("{field}" AS TEXT)) LIKE LOWER(?)')
        params.append(f"{query}%")

    rows = db.sql(
        f'SELECT DISTINCT "{field}" AS v FROM "{table}" '
        f'WHERE {" AND ".join(where)} ORDER BY "{field}" LIMIT {int(limit)}',
        params,
    )
    return [row["v"] for row in rows]
