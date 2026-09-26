"""Generate and store teacher blueprints for many topics up front (phase 1 of
teacher_student). Later `teacher_student` runs on these topics reuse the stored
blueprint and only pay for the student build.

    python make_blueprints.py topics.txt                 # skips topics already stored
    python make_blueprints.py topics.txt --teacher opus55 --force --no-rag

topics.txt: one topic per line, optional grade after a pipe:  Class 11 Physics: Rolling motion | 11
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from simgen import pipeline
from simgen.__main__ import load_env


def parse(lines):
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            topic, _, grade = line.partition("|")
            yield topic.strip(), int(grade) if grade.strip() else None


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("topics_file")
    ap.add_argument("--teacher", default=os.getenv("TEACHER", "opus5"))
    ap.add_argument("--no-rag", action="store_true")
    ap.add_argument("--force", action="store_true", help="regenerate even if stored")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    topics = list(parse(Path(a.topics_file).read_text(encoding="utf-8").splitlines()))

    def one(tg):
        topic, grade = tg
        try:
            rec, usages = pipeline.blueprint(topic, grade, a.teacher, not a.no_rag, a.force)
            return topic, rec, bool(usages), None
        except Exception as e:  # one bad topic shouldn't sink the batch
            return topic, None, False, e

    total = 0.0
    with ThreadPoolExecutor(a.workers) as ex:
        for topic, rec, fresh, err in ex.map(one, topics):
            if err:
                print(f"FAIL   {topic}: {err}", file=sys.stderr)
                continue
            total += rec["cost_usd"] if fresh else 0
            tag = "new   " if fresh else "stored"
            print(f"{tag} ${rec['cost_usd']:.4f}  {rec['in_tokens']:>6}->{rec['out_tokens']:<6} "
                  f"{rec['teacher']:<10} rag={'y' if rec['rag'] else 'n'}  {topic}")
    print(f"spent this run: ${total:.4f}  ->  {pipeline.BLUEPRINTS}/")


if __name__ == "__main__":
    main()
