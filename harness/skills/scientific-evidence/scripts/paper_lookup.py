import argparse
import json
import pathlib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def summarize(item):
    dates = item.get("published", {}).get("date-parts", [[]])
    return {
        "title": next(iter(item.get("title", [])), ""),
        "authors": [" ".join(filter(None, [a.get("given"), a.get("family")]))
                    for a in item.get("author", [])[:8]],
        "year": dates[0][0] if dates and dates[0] else None,
        "doi": item.get("DOI"),
        "url": item.get("URL"),
        "type": item.get("type"),
        "venue": next(iter(item.get("container-title", [])), ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Compact Crossref metadata lookup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query")
    group.add_argument("--doi")
    parser.add_argument("--limit", type=int, choices=range(1, 11), default=5)
    args = parser.parse_args()
    base = "https://api.crossref.org/works"
    if args.doi:
        doi = args.doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
        if not doi.startswith("10.") or "/" not in doi:
            parser.error("DOI must start with 10. and contain /")
        url = base + "/" + urllib.parse.quote(doi, safe="")
    else:
        if not args.query.strip():
            parser.error("query must not be empty")
        url = base + "?" + urllib.parse.urlencode({
            "query.bibliographic": args.query, "rows": args.limit,
            "select": "title,author,published,DOI,URL,type,container-title",
        })
    request = urllib.request.Request(url, headers={
        "User-Agent": "HarnessAcademicLookup/1.0", "Accept": "application/json",
    })
    try:
        ca = pathlib.Path('/etc/ssl/cert.pem')
        context = ssl.create_default_context(cafile=str(ca) if ca.is_file() else None)
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("response exceeds 2 MB")
        message = json.loads(data)["message"]
        items = [message] if args.doi else message["items"][:args.limit]
        print(json.dumps({"source": "Crossref", "results": list(map(summarize, items))},
                         ensure_ascii=False, separators=(",", ":")))
    except (urllib.error.URLError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
