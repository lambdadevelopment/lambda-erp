# Tool result limits

The built-in chat and the assistants connected through MCP read what a tool
returns as text. A model can read some tens of thousands of words at once;
a list of every customer cannot be read at all. Since 1.4.25 every list tool
pages its rows, and a general limit sits below all of them.

The REST API and the pages of the app are not affected.

## Per tool

| Tool | Returns | To get more |
|---|---|---|
| `search_masters` | Up to 50 matches: an array of full records. More: a page `{rows, total, returned, offset, has_more, note}` with 50 records and their identifying columns. With `limit` set: a plain array of up to that many full records (at most 500), as before | `offset`; `result_fields` for chosen columns; better: a narrower `query` or `filters` |
| `search_masters` with `include_meta` or `time_filter` | A page of 50 with the exact total (as before) | `limit` up to 500, `offset` |
| `list_documents` | 20 documents (as before) | `limit` up to 500 |
| `get_report` | Up to 200 rows of a list report, with `total_rows` and `has_more` | The report's filters; `limit` up to 2000, `offset` |
| `get_document` | Up to 200 lines per line table, with `_lines` giving the totals | `limit` up to 2000, `offset` |
| `query_dataset` | 200 groups | `limit` |

Totals and summaries of a report or document always cover all rows, whatever
part of the rows is returned.

The identifying columns of a master record are its id, the columns a
free-text search looks at, and a few that tell records apart (for an
account: root type, account type, group flag, parent).

## The general limit

Whatever a tool returns is brought under **100,000 characters** of JSON
(about 25,000 tokens) before it reaches the model. This also covers tools a
deployment registers itself.

- A result that is a list, or contains one, has its largest list shortened to
  what fits. The answer says so: `truncated` / `_truncated` with how many
  entries there are and how many are included.
- A result that is too large without a list to shorten becomes an error that
  names its size.
- Pictures and file content handed to the model are not counted.

`LAMBDA_ERP_TOOL_RESULT_MAX_CHARS` changes the limit (minimum 2000).

A plugin tool should still page its own rows and return a total; the general
limit is the net, not the design.
