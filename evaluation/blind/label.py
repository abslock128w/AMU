"""Label the blind items without seeing the existing labels. Never reads key.json.

  python label.py list                      items and whether each is labelled yet
  python label.py show NN                   print the item's comment and the size of its patch (read patch.diff yourself)
  python label.py record NN --first-line right|wrong --adds something|nothing|noise --false-rows K --real-rows K
                         [--labeler NAME] [--note TEXT]
Answers go to answers-<labeler>.json beside this file (default labeler: blind).
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def items():
    return sorted(p.name for p in (HERE/'items').iterdir() if p.is_dir())


def answers_path(labeler):
    return HERE/f'answers-{labeler}.json'


def main():
    a = argparse.ArgumentParser()
    sub = a.add_subparsers(dest='cmd', required=True)
    lst = sub.add_parser('list')
    lst.add_argument('--labeler', default='blind')
    show = sub.add_parser('show')
    show.add_argument('item')
    rec = sub.add_parser('record')
    rec.add_argument('item')
    rec.add_argument('--first-line', required=True, choices=('right', 'wrong'))
    rec.add_argument('--adds', required=True, choices=('something', 'nothing', 'noise'))
    rec.add_argument('--false-rows', required=True, type=int)
    rec.add_argument('--real-rows', required=True, type=int)
    rec.add_argument('--labeler', default='blind')
    rec.add_argument('--note', default='')
    args = a.parse_args()
    if args.cmd == 'list':
        done = json.loads(answers_path(args.labeler).read_text()) if answers_path(args.labeler).exists() else {}
        for item in items():
            print(item, 'labelled' if item in done else '-')
    elif args.cmd == 'show':
        folder = HERE/'items'/args.item
        print((folder/'comment.md').read_text())
        print(f"--- patch.diff: {len((folder/'patch.diff').read_text().splitlines())} lines at {folder/'patch.diff'}")
    else:
        assert args.item in items(), args.item
        path = answers_path(args.labeler)
        done = json.loads(path.read_text()) if path.exists() else {}
        done[args.item] = dict(first_line_correct=args.first_line == 'right', adds=args.adds, false_rows=args.false_rows,
                               real_rows=args.real_rows, note=args.note)
        path.write_text(json.dumps(done, indent=1, sort_keys=True) + '\n')
        print(f'{args.item} recorded ({len(done)} of {len(items())})')


if __name__ == '__main__':
    main()
