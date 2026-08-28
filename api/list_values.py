"""Shared value suggestions for the generic master/document list filters."""


def distinct_list_values(db, table: str, field: str, query: str = "", limit: int = 200) -> list:
    """Return distinct, non-empty values for one validated table column.

    ``field`` is checked against the live schema before it is interpolated.
    Prefix matching keeps autocomplete useful without loading a whole
    high-cardinality column into the browser.
    """
    if field not in db._get_table_columns(table):
        raise KeyError(field)

    where = [f'"{field}" IS NOT NULL', f'CAST("{field}" AS TEXT) <> \'\'']
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
