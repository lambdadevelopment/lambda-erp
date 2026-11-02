"""Fonts for PDF templates: what the server can print with, what a PDF uses,
and what an uploaded font file is.

WeasyPrint finds fonts through fontconfig, so the installed families and the
substitute for a missing one come from `fc-list` / `fc-match` (the Docker
image installs the font packages; see Dockerfile). Font names a PDF uses are
read from its font dictionaries, and from the embedded font program's name
table when it has one.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from functools import lru_cache
from struct import error as struct_error

FONT_TYPES = {".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2"}

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
# The standard 14 PDF fonts a viewer supplies itself, by base name.
_STANDARD_14 = {"Helvetica": "Helvetica", "Times": "Times", "Courier": "Courier",
                "Symbol": "Symbol", "ZapfDingbats": "ZapfDingbats"}
# Same letter widths, so a layout keeps its line breaks (what fontconfig's
# metric aliases map these names to).
_METRIC_TWINS = {
    "arial": "Liberation Sans", "helvetica": "Liberation Sans",
    "times new roman": "Liberation Serif", "times": "Liberation Serif",
    "courier new": "Liberation Mono", "courier": "Liberation Mono",
    "calibri": "Carlito", "cambria": "Caladea",
}


def _run(args: list[str]) -> str:
    if not shutil.which(args[0]):
        return ""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


@lru_cache(maxsize=1)
def installed_families() -> tuple[str, ...]:
    out = _run(["fc-list", "--format", "%{family[0]}\n"])
    return tuple(sorted({line.strip() for line in out.splitlines() if line.strip()}, key=str.lower))


@lru_cache(maxsize=256)
def renders_as(family: str) -> str | None:
    """The installed family fontconfig picks for `family` (None without fontconfig)."""
    return _run(["fc-match", "--format", "%{family[0]}", family]).strip() or None


def availability(family: str) -> dict:
    """installed / metric_compatible (same widths, other name) / missing (falls back)."""
    installed = {name.lower(): name for name in installed_families()}
    if family.lower() in installed:
        return {"available": "installed", "renders_as": installed[family.lower()]}
    substitute = renders_as(family)
    twin = _METRIC_TWINS.get(family.lower())
    if substitute and twin and substitute.lower() == twin.lower():
        return {"available": "metric_compatible", "renders_as": substitute,
                "note": f"{substitute} has the same letter widths as {family}, so line breaks match."}
    return {"available": "missing", "renders_as": substitute,
            "note": "Not installed; upload the font file (ttf, otf, woff, woff2) to use it."}


def fonts_line() -> str:
    """One paragraph for the template field reference."""
    families = installed_families()
    if not families:
        return "- Installed fonts: unknown on this server (no fontconfig). Upload font files to be sure of the face."
    twins = []
    for name in ("Helvetica", "Arial", "Times New Roman", "Courier New", "Calibri", "Cambria"):
        info = availability(name)
        if info["available"] == "metric_compatible":
            twins.append(f"{name} → {info['renders_as']}")
    fallback = renders_as("NoSuchFontFamily") or "a default font"
    line = f"- Installed fonts: {', '.join(families)}."
    if twins:
        line += f" Same letter widths under another name: {'; '.join(twins)}."
    return (line + f" Any other name falls back to {fallback}: for another face, upload its font file as an "
            "asset and declare it with @font-face.")


# ---------------------------------------------------------------------------
# Font files
# ---------------------------------------------------------------------------

def _names(font) -> tuple[str | None, str | None]:
    table = font["name"] if "name" in font else None
    if table is None:
        return None, None
    family = table.getDebugName(16) or table.getDebugName(1)
    style = table.getDebugName(17) or table.getDebugName(2)
    return family, style


def _embedding(font) -> str | None:
    """OS/2 fsType: what the font's licence says about embedding it in documents."""
    if "OS/2" not in font:
        return None
    fs_type = font["OS/2"].fsType
    if fs_type & 0x0002:
        return "restricted: the licence forbids embedding this font in documents"
    if fs_type & 0x0004:
        return "preview & print"
    if fs_type & 0x0008:
        return "editable"
    return "installable"


def font_file_info(data: bytes) -> dict:
    """Family, style and embedding permission of a ttf/otf/woff/woff2 file.

    Raises ValueError when the bytes aren't a font fontTools can read.
    """
    from fontTools.ttLib import TTFont, TTLibError
    try:
        font = TTFont(io.BytesIO(data), lazy=True)
        family, style = _names(font)
        info = {"family": family, "style": style, "format": font.flavor or ("otf" if "CFF " in font else "ttf"),
                "embedding": _embedding(font), "glyphs": len(font.getGlyphOrder())}
        font.close()
    except (TTLibError, AssertionError, KeyError, ValueError, struct_error) as exc:
        raise ValueError(f"Not a readable font file ({exc.__class__.__name__})") from None
    if not info["family"]:
        raise ValueError("The font file has no family name")
    return info


# ---------------------------------------------------------------------------
# Fonts a PDF uses
# ---------------------------------------------------------------------------

def _split_base_name(base: str) -> tuple[str, str]:
    """'LiberationSans-BoldItalic' -> ('Liberation Sans', 'Bold Italic'); 'ArialMT' -> ('Arial', '')."""
    name, _, style = base.partition("-")
    name = name.split(",")[0]
    for suffix in ("PSMT", "MT", "PS"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).strip()
    style = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", style.replace("MT", "")).strip()
    return family, style


def _embedded_program(descriptor) -> bytes | None:
    for key in ("/FontFile2", "/FontFile3", "/FontFile"):
        if key in descriptor:
            try:
                return descriptor[key].get_object().get_data()
            except Exception:  # noqa: BLE001 — a damaged stream just means no name table
                return None
    return None


def _describe(font) -> dict:
    font = font.get_object()
    base = str(font.get("/BaseFont") or "").lstrip("/")
    subset = bool(_SUBSET_PREFIX.match(base))
    base = _SUBSET_PREFIX.sub("", base)
    descriptor = font.get("/FontDescriptor")
    if descriptor is None and font.get("/DescendantFonts"):
        descriptor = font["/DescendantFonts"][0].get_object().get("/FontDescriptor")
    descriptor = descriptor.get_object() if descriptor is not None else {}
    program = _embedded_program(descriptor) if descriptor else None
    family = style = None
    if program:
        try:
            from fontTools.ttLib import TTFont
            family, style = _names(TTFont(io.BytesIO(program), lazy=True))
        except Exception:  # noqa: BLE001 — bare CFF/Type1 programs have no name table
            family = None
    if not family:
        raw = descriptor.get("/FontFamily") if descriptor else None
        family = (raw.decode("latin-1") if isinstance(raw, bytes) else str(raw)) if raw else None
        split_family, split_style = _split_base_name(base)
        family = family or split_family
        style = style or split_style
    return {"family": _STANDARD_14.get(family, family), "style": style or "Regular", "base_font": base,
            "embedded": program is not None, "subset": subset, "type": str(font.get("/Subtype") or "").lstrip("/")}


def fonts_in_pdf(data: bytes) -> list[dict]:
    """The font families a PDF's pages use, with their styles and our availability."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    seen: dict[str, dict] = {}

    def visit(resources, depth=0):
        if resources is None or depth > 4:
            return
        resources = resources.get_object()
        for font in (resources.get("/Font") or {}).values():
            info = _describe(font)
            entry = seen.setdefault(info["family"], {"family": info["family"], "styles": [],
                                                     "embedded": False, "base_fonts": []})
            if info["style"] not in entry["styles"]:
                entry["styles"].append(info["style"])
            if info["base_font"] not in entry["base_fonts"]:
                entry["base_fonts"].append(info["base_font"])
            entry["embedded"] = entry["embedded"] or info["embedded"]
        for xobject in (resources.get("/XObject") or {}).values():
            xobject = xobject.get_object()
            if xobject.get("/Subtype") == "/Form":
                visit(xobject.get("/Resources"), depth + 1)

    for page in reader.pages:
        visit(page.get("/Resources"))
    return [{**entry, **availability(entry["family"])} for entry in seen.values()]
