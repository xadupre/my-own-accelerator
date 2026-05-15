"""Pull request activity report command line."""

from __future__ import annotations

import argparse
import csv
import os
import pathlib
import re
import sys
import zipfile
from collections import Counter
from html import escape
from typing import Any
from urllib import parse
from urllib.error import HTTPError, URLError

from .review_pr import _fetch_json

PAGE_SIZE = 100
COPILOT_COMMAND_RE = re.compile(r"(?:^|\s)(?:@copilot|/copilot)\b", re.IGNORECASE)


def _fetch_paginated(url: str, token: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        page_url = f"{url}{sep}{parse.urlencode({'per_page': PAGE_SIZE, 'page': page})}"
        data = _fetch_json(page_url, token)
        if not isinstance(data, list) or not data:
            break
        if any(not isinstance(item, dict) for item in data):
            raise ValueError("Unexpected paginated payload returned by API.")
        rows.extend(data)
        if len(data) < PAGE_SIZE:
            break
        page += 1
    return rows


def _count_comments(comment_bodies: list[str]) -> tuple[int, int]:
    manual = 0
    copilot = 0
    for body in comment_bodies:
        if COPILOT_COMMAND_RE.search(body or ""):
            copilot += 1
        else:
            manual += 1
    return manual, copilot


def _collect_pr_comment_stats(
    owner: str,
    repo: str,
    pull_number: int,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> tuple[int, int]:
    base = f"{api_url.rstrip('/')}/repos/{owner}/{repo}"
    issue_comments = _fetch_paginated(f"{base}/issues/{pull_number}/comments", token)
    review_comments = _fetch_paginated(f"{base}/pulls/{pull_number}/comments", token)
    reviews = _fetch_paginated(f"{base}/pulls/{pull_number}/reviews", token)
    bodies = [
        str(comment.get("body", ""))
        for comment in [*issue_comments, *review_comments, *reviews]
        if isinstance(comment, dict)
    ]
    return _count_comments(bodies)


def build_pr_activity_rows(
    owner: str,
    repo: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> list[dict[str, Any]]:
    pulls_url = (
        f"{api_url.rstrip('/')}/repos/{owner}/{repo}/pulls"
        "?state=closed&sort=created&direction=desc"
    )
    pulls = _fetch_paginated(pulls_url, token)
    rows: list[dict[str, Any]] = []
    for pr in pulls:
        if pr.get("state") != "closed":
            continue
        number = int(pr.get("number", 0))
        manual_comments, copilot_commands = _collect_pr_comment_stats(
            owner=owner,
            repo=repo,
            pull_number=number,
            token=token,
            api_url=api_url,
        )
        merged_at = pr.get("merged_at")
        rows.append(
            {
                "number": number,
                "author": (pr.get("user") or {}).get("login", ""),
                "title": pr.get("title", ""),
                "created_at": pr.get("created_at", ""),
                "merged_at": merged_at or "",
                "closed_at": pr.get("closed_at", ""),
                "status": "merged" if merged_at else "cancelled",
                "manual_comments": manual_comments,
                "copilot_commands": copilot_commands,
                "html_url": pr.get("html_url", ""),
            }
        )
    return rows


def _save_csv(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "number",
        "author",
        "title",
        "created_at",
        "merged_at",
        "closed_at",
        "status",
        "manual_comments",
        "copilot_commands",
        "html_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _xlsx_cell(col: int, row: int, value: Any) -> str:
    col_name = ""
    c = col
    while c > 0:
        c, rem = divmod(c - 1, 26)
        col_name = chr(65 + rem) + col_name
    ref = f"{col_name}{row}"
    if isinstance(value, int):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _save_xlsx(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "number",
        "author",
        "title",
        "created_at",
        "merged_at",
        "closed_at",
        "status",
        "manual_comments",
        "copilot_commands",
        "html_url",
    ]
    values = [headers] + [[row.get(key, "") for key in headers] for row in rows]
    xml_rows = []
    for ridx, row_values in enumerate(values, start=1):
        cells = "".join(
            _xlsx_cell(cidx, ridx, value) for cidx, value in enumerate(row_values, start=1)
        )
        xml_rows.append(f'<row r="{ridx}">{cells}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(xml_rows)}</sheetData>"
        "</worksheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        "Type="
        '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="PR activity" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet)


def _save_bar_graph(path: pathlib.Path, values: dict[str, int], title: str) -> None:
    if not values:
        values = {"none": 0}
    max_value = max(values.values()) if values else 0
    width = 600
    bar_width = 80
    gap = 40
    left = 60
    baseline = 300
    scale = 200 / max_value if max_value else 0
    bars = []
    labels = []
    for i, (label, value) in enumerate(values.items()):
        x = left + i * (bar_width + gap)
        height = int(value * scale) if max_value else 0
        y = baseline - height
        bars.append(
            f'<rect x="{x}" y="{y}" width="{bar_width}" height="{height}" fill="#4e79a7"/>'
        )
        labels.append(
            f'<text x="{x + bar_width / 2}" y="{baseline + 20}" text-anchor="middle">'
            f"{escape(label)}</text>"
        )
        labels.append(
            f'<text x="{x + bar_width / 2}" y="{y - 6}" text-anchor="middle">{value}</text>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="360">'
        f'<text x="{width/2}" y="28" text-anchor="middle" font-size="18">{escape(title)}</text>'
        f'<line x1="{left - 20}" y1="{baseline}" '
        f'x2="{width - 20}" y2="{baseline}" stroke="#000"/>'
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )
    path.write_text(svg, encoding="utf-8")


def save_pr_activity_report(
    owner: str,
    repo: str,
    output_dir: str,
    prefix: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> dict[str, pathlib.Path]:
    rows = build_pr_activity_rows(owner=owner, repo=repo, token=token, api_url=api_url)
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{prefix}.csv"
    xlsx_path = out / f"{prefix}.xlsx"
    status_svg_path = out / f"{prefix}_status.svg"
    comments_svg_path = out / f"{prefix}_comments.svg"
    _save_csv(csv_path, rows)
    _save_xlsx(xlsx_path, rows)
    status_counts = Counter(row["status"] for row in rows)
    _save_bar_graph(status_svg_path, dict(status_counts), "Pull requests by status")
    total_manual = sum(int(row["manual_comments"]) for row in rows)
    total_copilot = sum(int(row["copilot_commands"]) for row in rows)
    _save_bar_graph(
        comments_svg_path,
        {"manual_comments": total_manual, "copilot_commands": total_copilot},
        "Manual comments vs Copilot commands",
    )
    return {
        "csv": csv_path,
        "xlsx": xlsx_path,
        "status_svg": status_svg_path,
        "comments_svg": comments_svg_path,
    }


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    token_default = os.environ.get("GITHUB_TOKEN") or None
    api_url_default = os.environ.get("GITHUB_API_URL") or "https://api.github.com"
    parser = argparse.ArgumentParser(
        description=(
            "Builds a report of completed pull requests with author, "
            "manual comments, Copilot commands, dates, and output files."
        )
    )
    parser.add_argument("owner", help="GitHub repository owner")
    parser.add_argument("repo", help="GitHub repository name")
    parser.add_argument("--token", default=token_default, help="GitHub personal access token")
    parser.add_argument("--api-url", default=api_url_default, help="GitHub API base URL")
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory where output files are written (default: current directory).",
    )
    parser.add_argument(
        "--prefix",
        default="pr_activity",
        help="Filename prefix for generated files.",
    )
    args = parser.parse_args(argv)
    try:
        outputs = save_pr_activity_report(
            owner=args.owner,
            repo=args.repo,
            output_dir=args.output_dir,
            prefix=args.prefix,
            token=args.token,
            api_url=args.api_url,
        )
    except (HTTPError, URLError, OSError, ValueError) as e:
        print(
            f"Unable to build pull request activity report ({type(e).__name__}).",
            file=sys.stderr,
        )
        return 1
    for _, path in outputs.items():
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
