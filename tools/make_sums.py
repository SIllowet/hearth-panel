#!/usr/bin/env python3
"""Write SHA256SUMS for the files an update replaces.

Run this before publishing a new version, and commit the result:

    python tools/make_sums.py

Hearth's updater downloads SHA256SUMS separately from the code and refuses to
install anything that does not match it, so a release without a current
SHA256SUMS cannot be installed by the panel at all.
"""
import hashlib
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import hearth_setup                                        # noqa: E402

OUT = os.path.join(BASE, 'SHA256SUMS')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def wanted_files():
    """Exactly what the updater is willing to replace, and nothing else."""
    for name in hearth_setup.APP_FILES:
        p = os.path.join(BASE, name)
        if os.path.isfile(p):
            yield name, p
    for d in hearth_setup.APP_DIRS:
        root = os.path.join(BASE, d)
        for r, _dirs, files in os.walk(root):
            for fn in sorted(files):
                p = os.path.join(r, fn)
                yield os.path.relpath(p, BASE).replace(os.sep, '/'), p


def crlf_files(rows):
    """Files whose bytes on disk are not what the repository holds.

    A checkout that converted line endings gives every text file a different
    checksum from the one in the release zip, so digests generated from it
    would match nothing and updates would fail for everyone who tried. Git is
    told not to do that in .gitattributes; this is here in case the checkout
    predates it.
    """
    bad = []
    for rel, path in rows:
        with open(path, 'rb') as f:
            head = f.read(1024 * 64)
        if b'\r\n' in head:
            bad.append(rel)
    return bad


def render():
    rows = sorted(set(wanted_files()))
    bad = crlf_files(rows)
    if bad:
        raise SystemExit(
            "These files have Windows line endings, which would make every\n"
            "published checksum wrong: %s\n"
            "Your checkout converted them. Fix it with:\n"
            "    git rm --cached -r . && git reset --hard\n"
            "(.gitattributes stops it happening again.)" % ", ".join(bad))
    out = ["# sha256 of every file Hearth's updater replaces.",
           "# Regenerate with: python tools/make_sums.py"]
    out += ["%s  %s" % (digest(path), rel) for rel, path in rows]
    return "\n".join(out) + "\n", len(rows)


def main():
    text, n = render()
    if '--check' in sys.argv:
        # For CI: a release whose SHA256SUMS is stale cannot be installed by
        # the panel at all, so it is worth failing the build over.
        have = open(OUT, encoding='utf-8').read() if os.path.exists(OUT) else ''
        if have.replace('\r\n', '\n') != text:
            print("SHA256SUMS is out of date - run: python tools/make_sums.py")
            return 1
        print("SHA256SUMS is current (%d files)" % n)
        return 0
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print("wrote %s (%d files)" % (OUT, n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
