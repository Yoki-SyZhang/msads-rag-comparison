"""
Stage 3 demo: interactive QA over the MSADS website corpus.

Run:
    conda activate adsp-nlp-backup
    python src/qa/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.qa.qa_pipeline import build_pipeline


def main() -> None:
    print("MSADS QA Demo  (type 'quit' or press Ctrl-C to exit)\n")
    pipeline = build_pipeline()

    while True:
        try:
            query = input("Question: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query or query.lower() in ("quit", "exit", "q"):
            break

        result = pipeline.run(query)

        print(f"\n[Attempts: {result['attempts']}/9 | "
              f"Completeness: {result['completeness']} | "
              f"Low-confidence: {result['low_confidence']}]")
        print("\nAnswer:")
        print(result["answer"])

        if result["sources"]:
            print("\nSources:")
            for src in result["sources"]:
                print(f"  - {src['page_title']}  {src['source_url']}")
        print("\n" + "-" * 60 + "\n")


if __name__ == "__main__":
    main()
