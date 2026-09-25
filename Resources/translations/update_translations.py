#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Translation extraction and compilation script for Nesting Workbench.

Extracts all strings marked with QT_TRANSLATE_NOOP from the Python source files
into a Qt Linguist .ts XML file, preserving any existing translations.
Optionally compiles .ts files into binary .qm catalogs.

Usage:
    python3 update_translations.py           # Extract strings to NestingWorkbench.ts
    python3 update_translations.py --compile # Also compile .ts files to .qm
"""

import argparse
import ast
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from xml.dom import minidom


def find_repo_root():
    """Finds the root of the repository."""
    current = os.path.dirname(os.path.abspath(__file__))
    while current and current != os.path.dirname(current):
        if os.path.exists(os.path.join(current, "package.xml")):
            return current
        current = os.path.dirname(current)
    # Fallback: two levels up from Resources/translations
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class TranslationExtractor(ast.NodeVisitor):
    def __init__(self, rel_filepath):
        self.rel_filepath = rel_filepath
        # entries: list of (context, source_text, line_number)
        self.entries = []

    def visit_Call(self, node):
        # Match QT_TRANSLATE_NOOP(context, text)
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name == "QT_TRANSLATE_NOOP" and len(node.args) >= 2:
            ctx_node = node.args[0]
            txt_node = node.args[1]

            context = self._extract_str(ctx_node)
            text = self._extract_str(txt_node)

            if context is not None and text is not None:
                self.entries.append((context, text, node.lineno))

        self.generic_visit(node)

    def _extract_str(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None


def extract_strings_from_dir(source_dir, repo_root):
    """Walks source_dir and extracts all QT_TRANSLATE_NOOP occurrences."""
    results = {}  # {context: {source_text: [(filepath, lineno), ...]}}

    for root, _, files in os.walk(source_dir):
        for f in sorted(files):
            if f.endswith(".py"):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, repo_root)

                with open(full_path, "r", encoding="utf-8") as fp:
                    try:
                        tree = ast.parse(fp.read(), filename=full_path)
                    except SyntaxError as e:
                        print(f"Warning: could not parse {full_path}: {e}", file=sys.stderr)
                        continue

                extractor = TranslationExtractor(rel_path)
                extractor.visit(tree)

                for context, text, lineno in extractor.entries:
                    if context not in results:
                        results[context] = {}
                    if text not in results[context]:
                        results[context][text] = []
                    results[context][text].append((rel_path, lineno))

    return results


def load_existing_ts(ts_path):
    """Loads existing translations from a .ts file if present."""
    existing = {}  # {(context, source_text): (translation_type, translation_text)}
    if not os.path.exists(ts_path):
        return existing

    try:
        tree = ET.parse(ts_path)
        root = tree.getroot()
        for context_elem in root.findall("context"):
            name_elem = context_elem.find("name")
            if name_elem is None or not name_elem.text:
                continue
            context_name = name_elem.text

            for msg_elem in context_elem.findall("message"):
                src_elem = msg_elem.find("source")
                if src_elem is None or not src_elem.text:
                    continue
                source_text = src_elem.text

                trans_elem = msg_elem.find("translation")
                if trans_elem is not None:
                    trans_type = trans_elem.attrib.get("type", "")
                    trans_text = trans_elem.text or ""
                    existing[(context_name, source_text)] = (trans_type, trans_text)
    except Exception as e:
        print(f"Warning: could not read existing TS file {ts_path}: {e}", file=sys.stderr)

    return existing


def generate_ts_xml(extracted_data, existing_translations, language=None):
    """Builds the Qt TS XML ElementTree."""
    ts_elem = ET.Element("TS", version="2.1")
    if language:
        ts_elem.set("language", language)

    for context_name in sorted(extracted_data.keys()):
        context_elem = ET.SubElement(ts_elem, "context")
        name_elem = ET.SubElement(context_elem, "name")
        name_elem.text = context_name

        messages = extracted_data[context_name]
        for source_text in sorted(messages.keys()):
            msg_elem = ET.SubElement(context_elem, "message")

            # Add locations
            for rel_path, lineno in messages[source_text]:
                loc_elem = ET.SubElement(msg_elem, "location")
                loc_elem.set("filename", rel_path)
                loc_elem.set("line", str(lineno))

            src_elem = ET.SubElement(msg_elem, "source")
            src_elem.text = source_text

            trans_elem = ET.SubElement(msg_elem, "translation")
            existing = existing_translations.get((context_name, source_text))
            if existing:
                trans_type, trans_text = existing
                if trans_type:
                    trans_elem.set("type", trans_type)
                if trans_text:
                    trans_elem.text = trans_text
            else:
                trans_elem.set("type", "unfinished")

    # Pretty format XML
    raw_str = ET.tostring(ts_elem, encoding="utf-8")
    parsed = minidom.parseString(raw_str)
    return parsed.toprettyxml(indent="    ", encoding="utf-8").decode("utf-8")


def compile_ts_to_qm(ts_path, qm_path):
    """Compiles a .ts file to .qm using lrelease if available."""
    lrelease_bin = (
        shutil.which("lrelease")
        or shutil.which("lrelease-qt5")
        or shutil.which("lrelease-qt6")
        or shutil.which("pyside6-lrelease")
        or shutil.which("pyside2-lrelease")
    )
    if not lrelease_bin:
        print(f"Notice: lrelease not found in PATH; cannot compile {os.path.basename(ts_path)} to .qm.")
        return False

    cmd = [lrelease_bin, ts_path, "-qm", qm_path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"Compiled {os.path.basename(ts_path)} -> {os.path.basename(qm_path)}")
        return True
    else:
        print(f"Error compiling {ts_path}: {res.stderr}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Update and compile Nesting Workbench translations.")
    parser.add_argument("--compile", action="store_true", help="Compile .ts files to binary .qm catalogs")
    args = parser.parse_args()

    repo_root = find_repo_root()
    source_dir = os.path.join(repo_root, "freecad", "nestingworkbench")
    translations_dir = os.path.join(repo_root, "Resources", "translations")
    os.makedirs(translations_dir, exist_ok=True)

    print(f"Extracting translation strings from {source_dir}...")
    extracted = extract_strings_from_dir(source_dir, repo_root)

    total_strings = sum(len(msgs) for msgs in extracted.values())
    print(f"Found {total_strings} translatable strings across {len(extracted)} contexts.")

    ts_filename = "NestingWorkbench.ts"
    ts_path = os.path.join(translations_dir, ts_filename)
    existing_trans = load_existing_ts(ts_path)

    xml_content = generate_ts_xml(extracted, existing_trans)
    with open(ts_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    print(f"Wrote master template: {ts_path}")

    # Process all .ts files in translations dir
    for fname in os.listdir(translations_dir):
        if fname.endswith(".ts") and fname != ts_filename:
            path = os.path.join(translations_dir, fname)
            lang_existing = load_existing_ts(path)
            lang_code = fname.replace("NestingWorkbench_", "").replace(".ts", "")
            lang_xml = generate_ts_xml(extracted, lang_existing, language=lang_code)
            with open(path, "w", encoding="utf-8") as f:
                f.write(lang_xml)
            print(f"Updated language file: {path}")

    if args.compile:
        for fname in os.listdir(translations_dir):
            if fname.endswith(".ts"):
                qm_fname = fname[:-3] + ".qm"
                ts_file = os.path.join(translations_dir, fname)
                qm_file = os.path.join(translations_dir, qm_fname)
                compile_ts_to_qm(ts_file, qm_file)


if __name__ == "__main__":
    main()
