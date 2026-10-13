#!/usr/bin/env python3
"""
Every way of cutting an episode, rendered as audio, side by side.

    ./tools/variants.py ep0
    ./tools/variants.py ep0 --only tight tightest
    ./tools/variants.py ep0 --list

The cut has a handful of settings — what counts as a pause, how short it
becomes, whether fillers and stammers go, and whether a cut is allowed to
land on sound — and the right ones are a matter of listening. So this runs
the cut at each named setting, renders each as an mp3 with the real music
and chapters, and writes next to it the list of what that variant removed,
so a listener can hear a cut and read what it was.

Output: build/episodes/<slug>/out/variants/<name>.mp3, .txt (the cuts in
output time), .edl.json (the edit, ready for assemble.py --edl), .json
(numbers), and README.txt with the table. The main edl.json is untouched.
Pick one with:

    ./tools/assemble.py ep0 --edl build/episodes/ep0/out/variants/tight.edl.json

or copy it over edl.json and run the normal render.
"""

import argparse
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import ROOT, die, ep_dir, load_json, save_json  # noqa: E402

TOOLS = pathlib.Path(__file__).resolve().parent

# name -> cut.py arguments. Ordered loose to tight.
VARIANTS = {
    "loose":    dict(fillers="off", stammers="off", max_pause=1.5, pause=0.6, extra=["--no-erm"],
                     note="pauses only, and only long ones"),
    "fillers":  dict(fillers="all", stammers="off", max_pause=1.0, pause=0.5,
                     note="fillers gone, stammers kept"),
    "clean":    dict(fillers="clean", stammers="clean", max_pause=0.7, pause=0.4,
                     note="only cuts that land in silence at both ends"),
    "medium":   dict(fillers="all", stammers="all", max_pause=0.7, pause=0.4,
                     note="the default: every filler and stammer, pauses over 0.7s closed"),
    "tight":    dict(fillers="all", stammers="all", max_pause=0.5, pause=0.35,
                     note="medium, with shorter pauses"),
    "tightest": dict(fillers="all", stammers="all", max_pause=0.45, pause=0.3, extra=["--gap-clear", "0.06"],
                     note="everything the rules can find, untokened sounds closer to words, pauses shortest"),
}


def run_variant(slug, name, conf, d, extra):
    vdir = d / "out" / "variants"
    vdir.mkdir(parents=True, exist_ok=True)
    edl = vdir / f"{name}.edl.json"
    cmd = [TOOLS / "cut.py", slug, "--out", edl, "--fillers", conf["fillers"],
           "--stammers", conf["stammers"], "--max-pause", conf["max_pause"],
           "--pause", conf["pause"], *conf.get("extra", []), *extra]
    if (d / "chapters.json").exists() and "--chapters" not in extra:
        cmd += ["--chapters", d / "chapters.json"]
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        return name, None, r.stdout + r.stderr
    r2 = subprocess.run([str(c) for c in [TOOLS / "assemble.py", slug, "--edl", edl, "--tag", name]],
                        capture_output=True, text=True)
    if r2.returncode != 0:
        return name, None, r2.stdout + r2.stderr
    return name, load_json(vdir / f"{name}.json"), r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--only", nargs="*", help="variant names")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--choose", metavar="TAG",
                    help="record this variant's settings in episode.json as the episode's cut, "
                         "so cut.py, assemble.py and clips.py reproduce it")
    ap.add_argument("--table-only", action="store_true", help="rebuild README.txt from the last run")
    ap.add_argument("extra", nargs="*", help="more cut.py arguments, after --")
    a = ap.parse_args()
    if a.choose:
        if a.choose not in VARIANTS:
            die(f"no variant {a.choose!r}; one of {', '.join(VARIANTS)}")
        d = ep_dir(a.slug)
        ep = load_json(d / "episode.json")
        v_ = VARIANTS[a.choose]
        cut = {k: v_[k] for k in ("fillers", "stammers", "max_pause", "pause") if k in v_}
        extra = v_.get("extra", [])
        for k, val in zip(extra[::2], extra[1::2]):
            cut[k.lstrip("-").replace("-", "_")] = float(val)
        if v_.get("no_erm"):
            cut["no_erm"] = True
        ep["cut"] = cut
        ep["variant"] = a.choose
        save_json(d / "episode.json", ep)
        print(f"episode.json: cut = {cut} ({a.choose}); now run cut, assemble and clips")
        return 0
    if a.list:
        for k, v in VARIANTS.items():
            print(f"  {k:<9} fillers {v['fillers']:<5} stammers {v['stammers']:<5} "
                  f"pause >{v['max_pause']}s -> {v['pause']}s   {v['note']}")
        return 0
    d = ep_dir(a.slug)
    names = a.only or list(VARIANTS)
    if a.table_only:
        results = {n: load_json(d / "out" / "variants" / f"{n}.json")
                   for n in names if (d / "out" / "variants" / f"{n}.json").exists()}
        names = [n for n in names if n in results]
        a.extra = []
    bad = [n for n in names if n not in VARIANTS]
    if bad:
        die(f"unknown variant: {', '.join(bad)}")

    if not a.table_only:
        # The dialogue bus is shared and cached by assemble.py; make it once,
        # alone, before the rest run in parallel over it.
        results = {}
        first, rest = names[0], names[1:]
        print(f"── {first}")
        name, rep, log = run_variant(a.slug, first, VARIANTS[first], d, a.extra)
        if rep is None:
            die(log[-2000:])
        results[name] = rep
        with ThreadPoolExecutor(max_workers=a.jobs) as ex:
            for name, rep, log in ex.map(lambda n: run_variant(a.slug, n, VARIANTS[n], d, a.extra), rest):
                print(f"── {name}" + ("" if rep else f" FAILED\n{log[-1500:]}"))
                if rep:
                    results[name] = rep

    rows = [f"{'variant':<9} {'length':>7} {'cuts':>5} {'removed':>8} {'fillers':>7} {'+ums':>5} {'stammers':>8} "
            f"{'pauses':>6} {'loud joins':>10} {'words':>11} {'LUFS':>6}  note"]
    for n in names:
        r = results.get(n)
        if not r:
            continue
        st = r["stats"]
        erm = (sum(v for k, v in st.get("erm", {}).items() if k in ("gap", "in", "long"))
               + st.get("verbatim", {}).get("cut", 0) + st.get("gap_sound", 0))
        rows.append(f"{n:<9} {r['duration']:>7.1f} {r['cuts']:>5} {r['removed_seconds']:>7.1f}s "
                    f"{st['fillers']:>7} {erm:>5} {st['stammers']:>8} {st['pauses']:>6} {st['joins_loud']:>10} "
                    f"{r['words_kept']:>5}/{r['words_total']:<5} {r['lufs'] or 0:>6.1f}  {VARIANTS[n]['note']}")
    table = "\n".join(rows)
    vdir = d / "out" / "variants"
    (vdir / "README.txt").write_text(
        "One mp3 per variant, with the real music and chapters. Each .txt lists what that\n"
        "variant cut, at the time it happens in that mp3. Pick one with\n"
        f"  ./tools/assemble.py {a.slug} --edl {vdir.relative_to(ROOT)}/<name>.edl.json\n\n" + table + "\n")
    print("\n" + table)
    print(f"\n{(vdir / 'README.txt').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
