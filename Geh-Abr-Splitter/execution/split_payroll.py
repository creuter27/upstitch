#!/usr/bin/env python3
"""
split_payroll.py — DATEV-Lohnabrechnung nach Mitarbeiter aufteilen.

Sucht in allen Unterverzeichnissen des aktuellen Verzeichnisses nach
verschlüsselten DATEV-PDFs und extrahiert für jeden Mitarbeiter eine
eigene PDF-Datei.

Unterstützte DATEV-Formate:
  LNGN16  Abrechnung der Brutto/Netto-Bezüge (ab ca. 2025)
  LNGN14  Abrechnung der Brutto/Netto-Bezüge (bis ca. 2022)
  O01/C01 Komprimiertes Abrechnungsformat (2022-2024, kein Formular-Kürzel)

Ausgabe-Dateinamen:
  Lohn_Vorname_Nachname_25-11.pdf  (Monat = Name des Monatsverzeichnisses)

Passwort-Priorität:
    1. --password Argument (expliziter Override)
    2. PW.txt im aktuellen Verzeichnis
    3. Interaktive Eingabe (Fallback)

Verwendung:
    python split_payroll.py [Optionen]

Optionen:
    --password TEXT       PDF-Passwort (überschreibt PW.txt)
    --source-pattern GLOB Dateinamenmuster für Quell-PDFs (Default: *.pdf)
    --dry-run             Zeigt an, was passieren würde, ohne Dateien zu schreiben
    --overwrite           Vorhandene Ausgabedateien überschreiben
    -v, --verbose         Ausführlichere Ausgabe
"""

import argparse
import re
import sys
from pathlib import Path
from getpass import getpass

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF nicht installiert. Bitte ausführen:")
    print("  pip install pymupdf")
    sys.exit(1)


# Identifiziert Payslip-Seiten anhand des Berater/Mandant/Pers.-Nr.-Musters
# Deckt alle VKZ-Varianten ab: O01, C01, 001, L05 usw.
# Achtung: Meldebescheinigungen haben "396703 / 26568" (mit Leerzeichen) — kein Treffer.
_RE_BERATER_PERS = re.compile(r"\d{5,6}/\d{5}/(\d{5})")

# Erkennt alte Mitarbeiterdateien (z.B. christian_reuter.pdf) — alles Kleinbuchstaben + Unterstriche
_RE_OLD_STYLE = re.compile(r"^[a-z][a-zäöüß_]*\.pdf$")


def is_payslip_page(text: str) -> bool:
    """True, wenn die Seite eine individuelle Brutto/Netto-Abrechnung ist."""
    # LNGN16 (ab 2025) oder LNGN14 (bis 2022): explizites Formular-Kürzel
    if "LNGN16" in text or "LNGN14" in text:
        return True
    # Alle anderen DATEV-Formate (O01/C01/001 etc.): Berater/Mandant/Pers.Nr. ohne Leerzeichen
    if _RE_BERATER_PERS.search(text):
        return True
    # Agenda LOHN (ab 2025): kein Formular-Kürzel, aber eindeutige Kombination
    if "Agenda LOHN" in text and "Abrechnung der Brutto-Netto-Bezüge" in text:
        return True
    return False


def extract_pers_nr(text: str) -> str | None:
    """Extrahiert die Personalnummer aus dem Seitentext (alle Formate)."""
    # Format 1: *Pers.-Nr. 00001*  (LNGN14/LNGN16)
    m = re.search(r"\*Pers\.-Nr\.\s*(\d+)\*", text)
    if m:
        return m.group(1)
    # Format 2: 396703/26568/00001  (alle DATEV-VKZ-Stile)
    m = _RE_BERATER_PERS.search(text)
    if m:
        return m.group(1)
    # Format 3: Agenda LOHN — *P.-Nr.: 7* → zero-padded auf 5 Stellen (→ 00007)
    m = re.search(r"\*P\.-Nr\.: (\d+)\*", text)
    if m:
        return f"{int(m.group(1)):05d}"
    return None


def extract_name_from_payslip(text: str) -> str | None:
    """
    Extrahiert den Mitarbeiternamen aus einer Payslip-Seite.

    LNGN16:      Name → PLZ+Stadt  (direkt vor PLZ)
    LNGN14:      Name → Straße → PLZ+Stadt  (optionale Zeile zwischen Name und PLZ)
    Agenda LOHN: *P.-Nr.: N*\nFrau/Herrn\nVorname Nachname
    """
    # Agenda LOHN: Anrede direkt nach der internen Pers.-Nr.-Zeile
    m = re.search(r"\*P\.-Nr\.: \d+\*\n(?:Frau|Herrn?)\n([A-ZÄÖÜ][^\n]+)\n", text)
    if m:
        return m.group(1).strip()
    # DATEV LNGN14/LNGN16: Name direkt vor PLZ oder mit einer Straßenzeile dazwischen
    m = re.search(
        r"\n([A-ZÄÖÜ][a-zäöüß]+(?:[\-\. ][A-ZÄÖÜ][a-zäöüß]+)+)\n"
        r"(?:[^\n]+\n)?"   # optionale Straßenzeile (LNGN14)
        r"\d{5}\s",
        text,
    )
    return m.group(1).strip() if m else None


def build_name_lookup(doc) -> dict[str, str]:
    """
    Baut eine {pers_nr: name} Tabelle aus allen Seiten eines Dokuments.

    Quellen (in Priorität):
    1. LNGN14/LNGN16-Seiten: Name im Adressblock (PLZ-Muster)
    2. Lohnjournal-Seiten: "00001 6   Reuter, Margit ..." Einträge
    """
    lookup: dict[str, str] = {}

    for i in range(len(doc)):
        text = doc[i].get_text()

        if is_payslip_page(text):
            # LNGN14/16: Name direkt auf der Seite
            name = extract_name_from_payslip(text)
            pers_nr = extract_pers_nr(text)
            if name and pers_nr and pers_nr not in lookup:
                lookup[pers_nr] = name
        else:
            # Lohnjournal: "00001 6    Reuter, Margit ..."
            # Format: Pers.-Nr., dann beliebige Tokens, dann Nachname, Vorname
            for line in text.split("\n"):
                m = re.search(
                    r"^(\d{5})\s+\S.*?([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?"
                    r",\s*[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\b",
                    line,
                )
                if m:
                    pers_nr = m.group(1)
                    raw_name = m.group(2)
                    # Lohnarten wie "Aushilfslohn, Betr" herausfiltern:
                    # echte Nachnamen sind kurz (≤20 Zeichen), Lohnarten länger
                    nachname = raw_name.split(",")[0].strip()
                    if len(nachname) <= 20 and pers_nr not in lookup:
                        lookup[pers_nr] = normalize_nachname_vorname(raw_name)

    return lookup


def normalize_nachname_vorname(name: str) -> str:
    """Wandelt 'Nachname, Vorname' in 'Vorname Nachname' um."""
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            return f"{parts[1]} {parts[0]}"
    return name


def sanitize_name_part(name: str) -> str:
    """Wandelt einen Namen in einen sicheren Dateinamen-Teil um (Schreibweise beibehalten)."""
    name = name.strip()
    name = re.sub(r",\s*", "_", name)            # "Nachname, Vorname" → "Nachname_Vorname"
    name = re.sub(r"[^\w äöüÄÖÜß\-]", "", name)  # Sonderzeichen entfernen
    name = name.strip().replace(" ", "_")
    name = re.sub(r"_+", "_", name)
    return name  # Groß-/Kleinschreibung beibehalten


def rename_existing_files(subdirs: list[Path], base_dir: Path) -> int:
    """
    Benennt alte Mitarbeiterdateien (z.B. christian_reuter.pdf) einmalig
    ins neue Format (Lohn_Christian_Reuter_25-09.pdf) um.
    """
    renamed = 0

    for subdir in subdirs:
        month_name = subdir.name
        for old_path in sorted(subdir.rglob("*.pdf")):
            if not _RE_OLD_STYLE.match(old_path.name):
                continue
            # Name rekonstruieren: christian_reuter → Christian_Reuter
            stem = old_path.stem  # z.B. "christian_reuter"
            parts = [p.capitalize() for p in stem.split("_") if p]
            capitalized = "_".join(parts)
            new_name = f"Lohn_{capitalized}_{month_name}.pdf"
            new_path = old_path.parent / new_name
            if not new_path.exists():
                old_path.rename(new_path)
                print(f"  Umbenannt: {old_path.relative_to(base_dir)} → {new_name}")
                renamed += 1
            else:
                print(f"  WARNUNG: {new_name} existiert bereits, übersprungen: {old_path.name}")

    return renamed


def process_pdf(
    pdf_path: Path,
    password: str,
    dry_run: bool,
    overwrite: bool,
    verbose: bool,
    month_name: str,
) -> tuple[int, int]:
    """
    Verarbeitet eine Payroll-PDF: öffnet, entschlüsselt, extrahiert.

    Returns:
        (extracted, errors)
    """
    doc = fitz.open(str(pdf_path))

    if doc.is_encrypted:
        if not doc.authenticate(password):
            print(f"    FEHLER: Falsches Passwort.")
            doc.close()
            return 0, 1

    total_pages = len(doc)

    # Pass 1: Namen-Lookup aus dem gesamten Dokument aufbauen
    name_lookup = build_name_lookup(doc)

    # Pass 2: Payslip-Seiten identifizieren und nach Pers.-Nr. gruppieren
    # {pers_nr: {"name": str | None, "pages": [int]}}
    employees: dict[str, dict] = {}
    skipped = 0

    for page_num in range(total_pages):
        text = doc[page_num].get_text()

        if not is_payslip_page(text):
            skipped += 1
            if verbose:
                print(f"    Seite {page_num + 1:2d}: übersprungen")
            continue

        pers_nr = extract_pers_nr(text)
        if not pers_nr:
            print(f"    WARNUNG: Seite {page_num + 1} — Personalnummer nicht erkannt")
            pers_nr = f"unbekannt_{page_num + 1}"

        # Name: direkt von der Seite, sonst aus Lookup
        name = extract_name_from_payslip(text) or name_lookup.get(pers_nr)

        if pers_nr not in employees:
            employees[pers_nr] = {"name": name, "pages": []}
        employees[pers_nr]["pages"].append(page_num)
        if name and not employees[pers_nr]["name"]:
            employees[pers_nr]["name"] = name

    if verbose:
        print(f"    {total_pages} Seiten gesamt — {skipped} übersprungen, "
              f"{len(employees)} Mitarbeiter gefunden")

    output_dir = pdf_path.parent
    extracted = 0
    errors = 0

    for pers_nr, info in sorted(employees.items()):
        name = info["name"]
        pages = info["pages"]

        if name:
            filename = f"Lohn_{sanitize_name_part(name)}_{month_name}.pdf"
        else:
            filename = f"Lohn_pers_nr_{pers_nr}_{month_name}.pdf"
            print(f"    WARNUNG: Pers.-Nr. {pers_nr} — Name nicht erkannt → {filename}")

        out_path = output_dir / filename
        page_label = (f"Seite {pages[0] + 1}" if len(pages) == 1
                      else f"Seiten {', '.join(str(p + 1) for p in pages)}")

        if out_path.exists() and not overwrite:
            print(f"    Nr. {pers_nr}: {(name or 'UNBEKANNT'):30s} → {filename}  (vorhanden, übersprungen)")
            extracted += 1
            continue

        if dry_run:
            print(f"    Nr. {pers_nr}: {(name or 'UNBEKANNT'):30s} → {filename}  [{page_label}]  [DRY-RUN]")
            extracted += 1
            continue

        out_doc = fitz.open()
        for p in pages:
            out_doc.insert_pdf(doc, from_page=p, to_page=p)
        out_doc.save(str(out_path))
        out_doc.close()

        print(f"    Nr. {pers_nr}: {(name or 'UNBEKANNT'):30s} → {filename}  [{page_label}]")
        extracted += 1

    doc.close()
    return extracted, errors


def main():
    parser = argparse.ArgumentParser(
        description="DATEV-Lohnabrechnungs-PDFs nach Mitarbeiter aufteilen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--password",
        help="PDF-Passwort (wird interaktiv abgefragt, wenn nicht angegeben)",
    )
    parser.add_argument(
        "--source-pattern",
        default="*.pdf",
        metavar="GLOB",
        help="Dateinamenmuster für Quell-PDFs in Unterverzeichnissen (Default: *.pdf)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Zeigt Aktionen an, ohne Dateien zu schreiben",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Vorhandene Ausgabedateien überschreiben",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Ausführlichere Ausgabe",
    )
    args = parser.parse_args()

    if args.password is not None:
        password = args.password
    elif (pw_file := Path("PW.txt")).exists():
        password = pw_file.read_text(encoding="utf-8").strip()
        print(f"Passwort aus PW.txt gelesen.")
    else:
        password = getpass("PDF-Passwort: ")

    base_dir = Path(".")
    subdirs = sorted(p for p in base_dir.iterdir() if p.is_dir() and not p.name.startswith("."))

    if not subdirs:
        print("Keine Unterverzeichnisse im aktuellen Verzeichnis gefunden.")
        sys.exit(1)

    # Einmalig: alte Dateinamen (z.B. christian_reuter.pdf) ins neue Format umbenennen
    renamed = rename_existing_files(subdirs, base_dir)
    if renamed:
        print(f"\n{renamed} Datei(en) ins neue Format umbenannt.\n")

    # PDFs sammeln und dem jeweiligen Monatsverzeichnis zuordnen
    all_pdfs: list[tuple[Path, str]] = []
    for subdir in subdirs:
        month_name = subdir.name
        for pdf_path in sorted(subdir.rglob(args.source_pattern)):
            all_pdfs.append((pdf_path, month_name))

    if not all_pdfs:
        print(f"Keine PDFs mit Muster '{args.source_pattern}' in Unterverzeichnissen gefunden.")
        sys.exit(1)

    print(f"Gefunden: {len(all_pdfs)} PDF(s) in {len(subdirs)} Unterverzeichnis(sen).")
    if args.dry_run:
        print("Modus: DRY-RUN — es werden keine Dateien geschrieben.\n")

    total_extracted = total_errors = skipped_dirs = 0

    for pdf_path, month_name in all_pdfs:
        # Verzeichnis überspringen wenn bereits aufgeteilte Mitarbeiterdateien vorhanden
        if list(pdf_path.parent.glob("Lohn_*.pdf")):
            print(f"\n[{pdf_path.relative_to(base_dir)}] übersprungen (bereits aufgeteilt)")
            skipped_dirs += 1
            continue

        print(f"\n[{pdf_path.relative_to(base_dir)}]")
        extracted, errors = process_pdf(
            pdf_path, password, args.dry_run, args.overwrite, args.verbose, month_name
        )
        total_extracted += extracted
        total_errors += errors

    print(f"\n{'─' * 50}")
    parts = [f"Extrahiert: {total_extracted}"]
    if skipped_dirs:
        parts.append(f"Übersprungen: {skipped_dirs}")
    parts.append(f"Fehler: {total_errors}")
    print(f"Fertig.  {' | '.join(parts)}")
    if total_errors:
        print("Tipp: Passwort prüfen oder --verbose für mehr Details verwenden.")


if __name__ == "__main__":
    main()
