#!/usr/bin/env python3
"""Download PubMed abstracts for a query, excluding review articles.

Outputs:
- One txt file per article (named by PMID)
- One combined txt file containing all downloaded records
"""

from __future__ import annotations

import argparse
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests


BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def text_or_empty(elem: ET.Element, path: str) -> str:
    node = elem.find(path)
    return node.text.strip() if node is not None and node.text else ""


def extract_title(article: ET.Element) -> str:
    title_node = article.find("./MedlineCitation/Article/ArticleTitle")
    if title_node is None:
        return ""
    return "".join(title_node.itertext()).strip()


def extract_abstract(article: ET.Element) -> str:
    parts: list[str] = []
    for abstract_node in article.findall("./MedlineCitation/Article/Abstract/AbstractText"):
        label = abstract_node.attrib.get("Label")
        text = "".join(abstract_node.itertext()).strip()
        if not text:
            continue
        if label:
            parts.append(f"{label}: {text}")
        else:
            parts.append(text)
    return "\n".join(parts).strip()


def extract_authors(article: ET.Element) -> str:
    authors: list[str] = []
    for author in article.findall("./MedlineCitation/Article/AuthorList/Author"):
        collective = text_or_empty(author, "./CollectiveName")
        if collective:
            authors.append(collective)
            continue

        last = text_or_empty(author, "./LastName")
        fore = text_or_empty(author, "./ForeName")
        initials = text_or_empty(author, "./Initials")

        if last and fore:
            authors.append(f"{last}, {fore}")
        elif last and initials:
            authors.append(f"{last}, {initials}")
        elif last:
            authors.append(last)

    return "; ".join(authors)


def extract_publication_types(article: ET.Element) -> list[str]:
    publication_types: list[str] = []
    for pub_type in article.findall(
        "./MedlineCitation/Article/PublicationTypeList/PublicationType"
    ):
        text = "".join(pub_type.itertext()).strip()
        if text:
            publication_types.append(text)
    return publication_types


def search_pubmed(query: str, retmax: int = 10000) -> list[str]:
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": retmax,
        "retmode": "json",
        "sort": "relevance",
    }
    response = requests.get(f"{BASE_URL}/esearch.fcgi", params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    return payload.get("esearchresult", {}).get("idlist", [])


def fetch_non_review_records(id_list: list[str], chunk_size: int = 200) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for i in range(0, len(id_list), chunk_size):
        ids = id_list[i : i + chunk_size]
        params = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "xml",
        }
        response = requests.get(f"{BASE_URL}/efetch.fcgi", params=params, timeout=90)
        response.raise_for_status()

        root = ET.fromstring(response.text)
        for article in root.findall(".//PubmedArticle"):
            publication_types = extract_publication_types(article)
            if any("review" in pub_type.lower() for pub_type in publication_types):
                continue

            records.append(
                {
                    "pmid": text_or_empty(article, "./MedlineCitation/PMID"),
                    "title": extract_title(article),
                    "abstract": extract_abstract(article),
                    "authors": extract_authors(article),
                    "publication_types": "; ".join(publication_types),
                }
            )

        # Keep request rate friendly for NCBI E-utilities.
        time.sleep(0.35)

    return records


def write_outputs(records: list[dict[str, str]], query: str, id_count: int, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write one txt file per article
    for rec in records:
        pmid = rec.get("pmid", "unknown_pmid")
        out_file = out_dir / f"{pmid}.txt"
        content = [
            f"PMID: {rec['pmid']}",
            f"Title: {rec['title']}",
            f"Authors: {rec['authors']}",
            f"PublicationTypes: {rec['publication_types']}",
            "Abstract:",
            rec["abstract"],
            "",
        ]
        out_file.write_text("\n".join(content), encoding="utf-8")

    # Write combined file
    combined_path = out_dir / "search_results_non_review.txt"
    with combined_path.open("w", encoding="utf-8") as handle:
        handle.write(f"Query: {query}\n")
        handle.write(f"Total PubMed IDs returned: {id_count}\n")
        handle.write(f"Non-review articles downloaded: {len(records)}\n\n")

        for rec in records:
            handle.write(f"PMID: {rec['pmid']}\n")
            handle.write(f"Title: {rec['title']}\n")
            handle.write(f"Authors: {rec['authors']}\n")
            handle.write("Abstract:\n")
            handle.write((rec["abstract"] or "") + "\n")
            handle.write("\n" + ("-" * 80) + "\n\n")

    return combined_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search PubMed, exclude review articles, and save abstracts as txt files."
    )
    parser.add_argument(
        "--query",
        required=True,
        help="PubMed query. Example: '(Brucella OR Brucellosis) AND vaccine'",
    )
    parser.add_argument(
        "--out-dir",
        default="abstracts",
        help="Output folder for txt files (default: abstracts)",
    )
    parser.add_argument(
        "--retmax",
        type=int,
        default=10000,
        help="Maximum number of PubMed IDs to retrieve from search (default: 10000)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)

    id_list = search_pubmed(args.query, retmax=args.retmax)
    if not id_list:
        out_dir.mkdir(parents=True, exist_ok=True)
        no_results_path = out_dir / "search_results_non_review.txt"
        no_results_path.write_text(
            f"Query: {args.query}\n\nTotal PubMed IDs returned: 0\nNon-review articles downloaded: 0\n",
            encoding="utf-8",
        )
        print("No search results found.")
        print(f"Wrote summary file: {no_results_path.resolve()}")
        return

    records = fetch_non_review_records(id_list)
    combined_path = write_outputs(records, args.query, len(id_list), out_dir)

    print(f"Total PubMed IDs from search: {len(id_list)}")
    print(f"Non-review articles saved: {len(records)}")
    print(f"Output folder: {out_dir.resolve()}")
    print(f"Combined file: {combined_path.resolve()}")
    print(f"Individual article files: one per PMID in {out_dir.resolve()}")


if __name__ == "__main__":
    main()
