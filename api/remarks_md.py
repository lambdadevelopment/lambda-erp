"""Lightweight markup for the Notes / Terms (`remarks`) block on documents.

`remarks` is free text the user types on a document (quotation, sales order,
invoice, …), but on the PDF it often wants to read like a real offer: italic
asides, bold service headings, and a right-aligned recurring-price line beside a
description. Rather than pulling in a full Markdown dependency we support a tiny,
predictable subset and render it to **safe** HTML. `generate_pdf()` exposes the
result as `remarks_html`; templates render it with `| safe` and style the emitted
class names (`.rm-block`, `.rm-h`, `.rm-p`, `.rm-amt`) however they like — so a
branded template can restyle the same markup without changing this converter.

Syntax (authored in the same textarea, also what the chat assistant emits):
  blank line              -> separates blocks (a float stays beside its block)
  # Heading               -> bold heading line (leading #'s stripped)
  *italic*   _italic_     -> italic
  **bold**                -> bold
  >> Monatlich | CHF 380.—  -> right-aligned price box (period over amount),
                               floated beside the block's heading/description
Everything is HTML-escaped first, so user text can't inject markup.
"""
import html
import re


def _inline(text):
    """Escape, then apply inline **bold** / *italic* / _italic_."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\*(.+?)\*", r"<em>\1</em>", out)
    out = re.sub(r"_(.+?)_", r"<em>\1</em>", out)
    return out


def _price(line):
    """`>> period | amount` -> a right-floated box with period over amount."""
    body = line.lstrip()[2:].strip()  # drop the leading '>>'
    if "|" in body:
        period, amount = (p.strip() for p in body.split("|", 1))
    else:
        period, amount = "", body
    parts = []
    if period:
        parts.append(f'<div class="rm-amt-period">{_inline(period)}</div>')
    if amount:
        parts.append(f'<div class="rm-amt-val">{_inline(amount)}</div>')
    return f'<div class="rm-amt">{"".join(parts)}</div>'


def _block(lines):
    """Render one block (lines between blank lines) to HTML.

    Price line(s) are emitted first and floated right so they sit beside the
    heading/description that follow, matching a typical offer layout.
    """
    price_html = ""
    body = ""
    para = []

    def flush_para():
        nonlocal body, para
        if para:
            body += '<div class="rm-p">' + "<br>".join(_inline(p) for p in para) + "</div>"
            para = []

    for raw in lines:
        stripped = raw.lstrip()
        if stripped.startswith(">>"):
            flush_para()
            price_html += _price(raw)
        elif stripped.startswith("#"):
            flush_para()
            heading = stripped.lstrip("#").strip()
            body += f'<div class="rm-h">{_inline(heading)}</div>'
        else:
            para.append(raw)
    flush_para()
    return f'<div class="rm-block">{price_html}{body}</div>'


def render_remarks(text):
    """Convert the remarks free text to safe styled HTML (or '' if empty)."""
    if not text or not text.strip():
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    for raw_block in re.split(r"\n[ \t]*\n", text):
        lines = raw_block.split("\n")
        if not any(l.strip() for l in lines):
            continue
        blocks.append(_block(lines))
    return "".join(blocks)
