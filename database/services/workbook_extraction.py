"""Read OOXML workbook evidence once without Excel or formula execution."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zipfile import ZipFile

from .audit import canonical_json
from .decimals import ExactDecimal
from .ingestion import FieldEvidence, WorkbookInput, WorksheetInput


ADAPTER_VERSION = "OOXML Evidence v1"
MARKER_GROUPS = {
    "part": ("Part Number", "Part No", "Part No."),
    "supplier": ("Supplier", "Supplier Name"),
    "plant": ("Plant", "Plant Location", "Manufacturing Location"),
    "operations": ("Operations", "Operation", "Manufacturing Operations"),
    "materials": ("Materials", "Material", "Raw Materials"),
    "price": ("Total Selling Price", "Selling Price", "Final Piece Price"),
}
CELL_REFERENCE = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}\Z")


class ExtractionCancelled(Exception):
    pass


def _label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).rstrip(":").strip().casefold()


def default_detection_profile() -> dict:
    return {"profile_version": "Detailed PBD Markers v1", "markers": {k: list(v) for k, v in MARKER_GROUPS.items()},
            "ignored_sheet_names": ["PBD Summary", "Instructions", "Cover"]}


def validate_detection_profile(profile: dict) -> dict:
    profile = json.loads(canonical_json(profile))
    if not isinstance(profile, dict):
        raise ValueError("Detection profile must be a JSON object")
    if not isinstance(profile.get("profile_version"), str) or not profile["profile_version"].strip():
        raise ValueError("Detection profile requires a version")
    if not isinstance(profile.get("markers"), dict) or set(profile["markers"]) != set(MARKER_GROUPS):
        raise ValueError("Detection profile requires all six structural marker groups")
    for labels in profile["markers"].values():
        if not isinstance(labels, list) or not labels or any(not isinstance(x, str) or not _label(x) for x in labels):
            raise ValueError("Every marker group requires nonempty exact labels")
    ignored = profile.get("ignored_sheet_names", [])
    if not isinstance(ignored, list) or any(not isinstance(x, str) or not _label(x) for x in ignored):
        raise ValueError("Ignored worksheet names must be a list of nonempty exact names")
    profile["ignored_sheet_names"] = ignored
    return profile


def _elements(root: ET.Element, local_name: str):
    return root.iterfind(f".//{{*}}{local_name}")


def _text(root: ET.Element | None) -> str:
    if root is None:
        return ""
    # Phonetic annotations are retained in raw XML, not appended to cell values.
    return "".join(node.text or "" for node in root.findall("{*}t")) + "".join(
        node.text or "" for node in root.findall("{*}r/{*}t"))


def shared_strings(raw: bytes | None) -> tuple[str, ...]:
    return () if raw is None else tuple(_text(item) for item in ET.fromstring(raw).findall("{*}si"))


def _digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExtractedWorksheet:
    metadata: WorksheetInput
    payload: dict


@dataclass(frozen=True)
class ExtractedWorkbook:
    workbook: WorkbookInput
    profile: dict
    context: dict
    worksheets: tuple[ExtractedWorksheet, ...]

    def summary(self) -> dict:
        return {
            "filename": self.workbook.filename, "file_hash_sha256": self.workbook.file_hash_sha256,
            "adapter_version": ADAPTER_VERSION, "profile_version": self.profile["profile_version"],
            "scanned_tabs": len(self.worksheets),
            "pbd_tabs": sum(s.metadata.detection_result == "PBD" for s in self.worksheets),
            "review_tabs": sum(s.metadata.detection_result == "Review Required" for s in self.worksheets),
            "ignored_tabs": sum(s.metadata.detection_result == "Non-PBD" for s in self.worksheets),
            "failed_tabs": sum(s.metadata.detection_result == "Failed" for s in self.worksheets),
            "worksheets": [{"name": s.metadata.name, "visibility": s.metadata.visibility,
                            "result": s.metadata.detection_result, "cell_count": len(s.payload["cells"]),
                            "diagnostics": s.payload["diagnostics"]} for s in self.worksheets],
        }


def extract_worksheet(
    raw_xml: bytes, *, ordinal: int, name: str, visibility: str,
    strings: tuple[str, ...], profile: dict, kind: str = "worksheet",
    comments_xml: bytes | None = None,
) -> ExtractedWorksheet:
    cells, merges, diagnostics, notes = [], [], [], []
    matched, used_range = [], None
    reason = "No PBD structural markers"
    result = "Non-PBD"
    try:
        root = ET.fromstring(raw_xml)
        if kind != "worksheet":
            reason = "Non-worksheet workbook tab"
        else:
            if root.tag.split("}")[-1] != "worksheet":
                raise ValueError("Worksheet relationship does not contain a worksheet")
            dimension = root.find("{*}dimension")
            used_range = dimension.get("ref") if dimension is not None else None
            seen = set()
            for cell in root.findall("{*}sheetData/{*}row/{*}c"):
                address = cell.get("r", "")
                if not CELL_REFERENCE.fullmatch(address) or address in seen:
                    raise ValueError("Missing, invalid, or duplicate cell reference")
                seen.add(address)
                value_node, formula = cell.find("{*}v"), cell.find("{*}f")
                raw_value = value_node.text if value_node is not None else None
                cell_type = cell.get("t", "n")
                value = raw_value
                if cell_type == "s":
                    if raw_value is None or not raw_value.isdigit() or int(raw_value) >= len(strings):
                        raise ValueError("Invalid shared-string reference")
                    value = strings[int(raw_value)]
                elif cell_type == "inlineStr":
                    value = _text(cell.find("{*}is"))
                formula_text = None if formula is None else "=" + (formula.text or "")
                attributes = {} if formula is None else dict(formula.attrib)
                if formula is not None and raw_value is None:
                    diagnostics.append({"code": "FORMULA_CACHE_MISSING", "cell": address})
                if attributes.get("t") in {"shared", "array", "dataTable"}:
                    diagnostics.append({"code": "FORMULA_EXPANSION_REVIEW", "cell": address})
                if cell_type == "e":
                    diagnostics.append({"code": "EXCEL_ERROR_VALUE", "cell": address})
                cells.append({"cell": address, "cell_type": cell_type,
                              "submitted_lexeme": value, "raw_value_lexeme": raw_value,
                              "formula_text": formula_text, "formula_attributes": attributes,
                              "cached_value_lexeme": raw_value if formula is not None else None,
                              "style_index": cell.get("s")})
            merges = [node.get("ref") for node in root.findall("{*}mergeCells/{*}mergeCell")]
            labels = {_label(c["submitted_lexeme"]) for c in cells
                      if c["cell_type"] in {"s", "inlineStr", "str"}
                      and c["submitted_lexeme"] is not None and c["formula_text"] is None}
            matched = sorted(group for group, aliases in profile["markers"].items()
                             if any(_label(alias) in labels for alias in aliases))
            if _label(name) in {_label(n) for n in ["PBD Summary", *profile["ignored_sheet_names"]]}:
                reason = "Explicit instruction, cover, or summary tab"
            elif len(matched) == len(MARKER_GROUPS):
                result, reason = "PBD", "All detailed PBD structural markers present"
            elif matched:
                result, reason = "Review Required", "Incomplete PBD-like structural markers"
            if comments_xml is not None:
                for comment in _elements(ET.fromstring(comments_xml), "comment"):
                    notes.append({"cell": comment.get("ref"), "text": _text(comment.find("{*}text"))})
    except (ET.ParseError, ValueError, IndexError) as error:
        result, reason = "Failed", str(error)
        diagnostics.append({"code": "WORKSHEET_PARSE_FAILED", "detail": str(error)})
    logical_cells = [{k: v for k, v in cell.items()
                      if k != "style_index" and not (k == "raw_value_lexeme" and cell["cell_type"] == "s")}
                     for cell in cells]
    logical = _digest({"adapter": ADAPTER_VERSION, "profile": profile,
                       "cells": logical_cells, "notes": notes}) if result in {"PBD", "Review Required"} else None
    metadata = WorksheetInput(ordinal, name, visibility, used_range,
                              hashlib.sha256(raw_xml).hexdigest(), used_range, logical, result)
    payload = {"raw_xml_base64": base64.b64encode(raw_xml).decode("ascii"),
               "comments_xml_base64": None if comments_xml is None else base64.b64encode(comments_xml).decode("ascii"),
               "kind": kind, "cells": cells, "merged_ranges": merges, "notes": notes,
               "matched_markers": matched, "diagnostics": diagnostics, "reason": reason}
    return ExtractedWorksheet(metadata, payload)


def _target(base: str, target: str) -> str:
    if not isinstance(target, str) or not target or "\\" in target or ":" in target:
        raise ValueError("Invalid internal workbook relationship")
    member = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                               else posixpath.join(posixpath.dirname(base), target))
    if member.startswith("../") or member in {"..", "."}:
        raise ValueError("Workbook relationship escapes its package")
    return member


def _relationships(archive: ZipFile, part: str) -> dict:
    rel_path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if rel_path not in archive.namelist():
        return {}
    nodes = list(ET.fromstring(archive.read(rel_path)))
    ids = [node.get("Id") for node in nodes]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError("Workbook relationship identifiers must be present and unique")
    return {node.get("Id"): {"target": node.get("Target"), "type": node.get("Type", "").rsplit("/", 1)[-1],
                            "external": node.get("TargetMode") == "External"}
            for node in nodes}


def extract_workbook(
    path: Path, *, profile: dict | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ExtractedWorkbook:
    path = Path(path)
    if path.suffix.lower() not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        raise ValueError("Workbook extraction supports OOXML .xlsx/.xlsm/.xltx/.xltm files")
    profile = validate_detection_profile(default_detection_profile() if profile is None else profile)
    with path.open("rb") as stream:
        original = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if cancelled and cancelled():
                raise ExtractionCancelled("Workbook extraction cancelled before registration")
            digest.update(chunk)
        stream.seek(0)
        with ZipFile(stream) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Workbook package contains duplicate member names")
            roots = ET.fromstring(archive.read("_rels/.rels"))
            office = [node for node in roots if node.get("Type", "").endswith("/officeDocument")]
            if len(office) != 1 or office[0].get("TargetMode") == "External":
                raise ValueError("Workbook requires one internal office-document relationship")
            workbook_part = _target("", office[0].get("Target", ""))
            workbook_xml = archive.read(workbook_part)
            workbook_root = ET.fromstring(workbook_xml)
            relations = _relationships(archive, workbook_part)
            def related(kind):
                matches = [rel for rel in relations.values() if rel["type"] == kind]
                if len(matches) > 1 or any(rel["external"] for rel in matches):
                    raise ValueError(f"Invalid {kind} workbook relationship")
                return None if not matches else archive.read(_target(workbook_part, matches[0]["target"]))
            strings_xml, styles_xml = related("sharedStrings"), related("styles")
            strings = shared_strings(strings_xml)
            tabs = workbook_root.findall("{*}sheets/{*}sheet")
            if not tabs:
                raise ValueError("Workbook contains no tabs")
            tab_names = [tab.get("name") for tab in tabs]
            if not all(tab_names) or len(tab_names) != len(set(tab_names)):
                raise ValueError("Workbook tabs require distinct nonempty names")
            sheets = []
            for ordinal, tab in enumerate(tabs, 1):
                if cancelled and cancelled():
                    raise ExtractionCancelled("Workbook extraction cancelled before registration")
                relation_id = next((v for k, v in tab.attrib.items() if k.endswith("}id")), None)
                relation = relations.get(relation_id)
                if relation is None or relation["external"]:
                    raise ValueError("Workbook tab has a missing or external relationship")
                member = _target(workbook_part, relation["target"])
                comments = [rel for rel in _relationships(archive, member).values()
                            if rel["type"] == "comments" and not rel["external"]]
                if len(comments) > 1:
                    raise ValueError("Worksheet has ambiguous comment relationships")
                comments_xml = None if not comments else archive.read(_target(member, comments[0]["target"]))
                visibility = {"visible": "Visible", "hidden": "Hidden", "veryHidden": "Very Hidden"}.get(tab.get("state", "visible"))
                if visibility is None:
                    raise ValueError("Unsupported workbook tab visibility")
                sheet = extract_worksheet(
                    archive.read(member), ordinal=ordinal, name=tab.get("name", ""),
                    visibility=visibility, strings=strings, profile=profile,
                    kind=relation["type"], comments_xml=comments_xml,
                )
                sheets.append(sheet)
                if progress:
                    progress(ordinal, len(tabs), sheet.metadata.name)
        final = os.fstat(stream.fileno())
        if (original.st_size, original.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            raise ValueError("Source workbook changed during extraction")
    workbook = WorkbookInput(path.name, str(path.resolve()), original.st_size, digest.hexdigest(),
                             datetime.fromtimestamp(original.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                             None, tuple(sheet.metadata for sheet in sheets))
    context = {"workbook_xml_base64": base64.b64encode(workbook_xml).decode("ascii"),
               "workbook_relationships": relations,
               "shared_strings_xml_base64": None if strings_xml is None else base64.b64encode(strings_xml).decode("ascii"),
               "styles_xml_base64": None if styles_xml is None else base64.b64encode(styles_xml).decode("ascii")}
    return ExtractedWorkbook(workbook, profile, context, tuple(sheets))


def mapped_field(sheet: ExtractedWorksheet, *, cell_reference: str, field_code: str,
                 value_kind: str, normalized_unit_id: str | None = None,
                 currency_id: str | None = None) -> FieldEvidence:
    if sheet.metadata.detection_result not in {"PBD", "Review Required"}:
        raise ValueError("Only PBD candidates can provide provisional field mappings")
    cell = next((item for item in sheet.payload["cells"] if item["cell"] == cell_reference), None)
    if cell is None or cell["submitted_lexeme"] is None or cell["cell_type"] == "e":
        raise ValueError("Mapped field requires valid source-cell evidence")
    if cell["formula_attributes"].get("t") in {"shared", "array", "dataTable"}:
        raise ValueError("Mapped formula requires supported expansion and review")
    value = cell["submitted_lexeme"]
    exact, text, boolean = None, None, None
    if value_kind == "decimal":
        if cell["cell_type"] not in {"n", "str"}:
            raise ValueError("Numeric mapping requires a numeric source cell")
        exact = ExactDecimal.parse(value)
        exact.governing_1e4()
    elif value_kind == "text":
        text = value
    elif value_kind == "boolean" and cell["cell_type"] == "b" and value in {"0", "1"}:
        boolean = value == "1"
    else:
        raise ValueError("Unsupported mapped value kind or source-cell type")
    return FieldEvidence(field_code, cell_reference, value, cell["formula_text"],
                         cell["cached_value_lexeme"], exact_decimal=exact, text_value=text,
                         boolean_value=boolean, normalized_unit_id=normalized_unit_id,
                         currency_id=currency_id, precision_status="Eligible" if exact else "Not Applicable")
