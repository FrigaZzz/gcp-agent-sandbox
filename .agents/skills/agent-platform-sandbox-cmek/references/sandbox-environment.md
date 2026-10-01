# Sandbox environment: what is inside and what to plan for

Measured on a **code sandbox** (`--kind code`, the default) in `europe-west8` on 2026-10-01 by running
probe scripts through `agent-sandbox run`. The image is Google-managed and can change: treat versions as a
snapshot and re-check with the snippet at the end if something behaves differently. Anything marked
*(not tested)* is inferred, not measured.

## At a glance

| | |
|---|---|
| OS | Debian 12 (bookworm), x86_64, running under **gVisor** (`Linux 4.19.0-gvisor`), runs as `root` |
| Python | **3.12.5** (`/usr/local/bin/python3`, also `python`, `python3.12`), pip 24.2 |
| CPU | 8 vCPU visible; see "Parallelism" below for what you actually get |
| Memory | **2 GiB** (`MemTotal 2097152 kB`), no swap |
| Working dir | `/home/bard` (also `$SANDBOX_INPUT_OUTPUT_DIRECTORY`); `HOME` is unset |
| Network | **None.** Not even IP: `Network is unreachable`; DNS fails; `pip install` fails |
| GPU | None (TensorFlow reports CPU only; `cuInit` error is logged and harmless) |
| Locale / time | `C.UTF-8`, UTC |
| Round trip | create + run + delete takes about 3–5 s for a trivial script |

## Resource limits (measured)

| Limit | Result |
|---|---|
| RAM | 1.2 GB array allocated and touched: fine. 3 GB: process **killed**, `exit_status: -1`, **empty stdout and stderr, even for lines printed (and flushed) before the kill**. A silent `-1` with no output means out-of-memory. Stay under ~1.5 GB peak |
| Disk | `/` overlay reports 504 GB free, `/home/bard` is a 9p mount, `/tmp` is tmpfs. 256 MB written in 0.9 s; 311 MB of output files in one call worked. Larger sizes *(not tested)*. `/tmp` is memory-backed *(not tested)*, so keep big scratch files in `/home/bard` |
| Returned files | 1, 10, 50, 100 and **150 MB** files all came back from one `run` (150 MB took ~28 s end to end). No limit found up to 150 MB |
| Upload | 100 MB per request (CLI limit) |
| Wall clock | 200 s single call: fine (see "Long runs" below for the upper bound) |
| ulimits | open files 25000, processes ~1M, stack 8 MB, everything else unlimited, core dumps off |
| Sandbox lifetime | `--ttl` default 900 s, hard cap 3600 s, enforced server-side |

### Parallelism

`nproc` says 8, but 8 worker processes each doing 3 M-iteration pure-Python loops finished in 1.58 s versus
3.16 s serial: about **2x**, not 8x. Plan for roughly 2 effective cores for CPU-bound work. Reference speed:
10 M-iteration Python `for` loop ≈ 2.0 s; 2000×2000 float64 `numpy` matmul ≈ 0.28 s.

### I/O speed

Writing 64 MB with fsync: 0.31 s in `/home/bard` (9p), 0.26 s in `/tmp`. 2000 tiny files created in 0.6 s. Fast
enough that disk is rarely the bottleneck.

## Installed Python packages (140 distributions)

The exact `name==version` list is in `python-packages.txt` (same folder). The table below is a grouped summary.

| Area | Packages (version) |
|---|---|
| Numerics / data | numpy 2.1.3, pandas 2.2.3, scipy 1.15.2, scikit-learn 1.6.1, statsmodels 0.14.6, numba 0.64.0, numexpr, sympy 1.13.3, networkx 3.6.1, ortools 9.14, joblib, patsy, pyarrow 18.1, h5py 3.16, toolz |
| ML | tensorflow 2.20.0 (CPU), keras 3.14.0, xgboost 3.2.0, tensorboard 2.20.0 |
| NLP | spacy 3.8.14 (+thinc, blis, srsly …), nltk 3.9.1, textblob 0.19.0, regex |
| Plotting | matplotlib 3.10.1 (backend `agg`), seaborn 0.13.2, plotly 6.1.2, bokeh 3.8.2, mizani, matplotlib-venn |
| Images / geo | pillow 11.1.0, opencv-python 4.11.0.86 (`cv2`), scikit-image 0.25.2, imageio, tifffile, geopandas 1.0.1, shapely 2.1.2, pyproj 3.7.2, pyogrio |
| Documents | openpyxl 3.1.5, XlsxWriter, xlrd 2.0.1, python-docx 1.1.2, python-pptx 1.0.2, PyPDF2 3.0.1, reportlab 4.3.1, fpdf 1.7.2, PyLaTeX, Markdown, striprtf |
| Web / utils | requests, httpx, urllib3, pydantic 2.13, jsonschema, Jinja2, PyYAML, lxml 5.3.1, rich, typer, click, tqdm, tabulate, Pygments |
| Misc | chess 1.11.2, grpcio, protobuf 6.31.1, absl-py, setuptools, wheel |

**Not installed:** `torch`, `jax`, `sqlalchemy`, `pytest`, `black`, `ruff`, `uv`, any LLM/cloud SDK.
`requests`/`httpx` import fine but cannot reach anything.

**Installed but empty:** spaCy has **no models** (`spacy.util.get_installed_models()` returns `[]`), and there is no
`nltk_data`, so `spacy.load("en_core_web_sm")`, `nltk.word_tokenize` and similar fail. Upload what you need
(model dirs are subject to the 100 MB per-upload limit, so split or tar) or avoid them.

### Import cost (cold, one process)

`tensorflow` 12.7 s · `xgboost` 4.0 s · `scikit-learn` 3.2 s · `spacy` 2.9 s · `geopandas` 2.0 s · `pandas` 1.7 s ·
`matplotlib.pyplot` 1.5 s · `cv2` 0.5 s · `numpy` / `scipy` 0.4 s · `plotly` 0.2 s. You pay this on every one-shot
`run`, and billing is per second, so avoid TensorFlow unless you need it.

## Command-line tools

**Present:** `bash`, `sh`, `git`, `curl`, `wget` (no network, so they only help for local files), `tar`, `gzip`, `xz`,
`zip`, `unzip`, `make`, `gcc`, `g++`, `cc`, `perl`, `sed`, `awk`, `grep`, `find`, `ssh`, `openssl`, `pip`.

**Missing:** `bzip2`, `clang`, `cmake`, `rustc`/`cargo`, `go`, `node`/`npm`, `java`, `ruby`, `php`, `R`, `julia`, `lua`,
`sqlite3` CLI (the Python `sqlite3` module works, SQLite 3.40.1), `psql`, `ffmpeg`, `convert` (ImageMagick),
`pdftotext`, `libreoffice`, `tesseract`, `pandoc`, LaTeX, `jq`, `vim`/`nano`, `rsync`, `docker`, `uv`, `gcloud`.

`gcc`/`g++`/`make` exist, so you can compile small C/C++ programs from uploaded or inline source. Fonts: only 18
installed (DejaVu Sans/Serif/Mono, Bitstream Charter, Courier 10 Pitch, Quicksand), which matters for rendered charts
and PDFs.

## Execution semantics (things that surprised or will surprise you)

* **`exec` is a stateful interpreter.** In a started sandbox, Python variables defined in one `exec` call are still
  defined in the next (a `global_marker = 42` set in call 1 was readable in call 2), like a notebook kernel. `run`
  is a single execution, so this only matters for `start` + several `exec` calls.
* **Files and processes persist within one sandbox** across `exec` and `bash` calls: files written by one are visible
  to the other, and a background process (`subprocess.Popen("sleep 600")`) was still alive in later calls. Everything
  disappears when the sandbox is deleted or expires.
* **Output files:** only files **created** by that call, under the working directory, **not hidden** (`.hidden.txt`
  was not returned) are sent back. Nested paths are preserved (`sub/dir/b.txt`). Uploaded inputs are not echoed
  back. Details in `troubleshooting.md`.
* **Failure codes seen:** `106` uncaught Python exception, the command's own status for `--bash` (an `exit 7` gave
  `exit_status: 7`), `-1` killed by the platform (out of memory). A failed run can still return `ok: false` with
  stderr empty, so also check for `-1`.
* **`multiprocessing` from `--code` / `exec` fails to pickle functions** (`attribute lookup f on __main__ failed`)
  because the code is run inside the interpreter's own `__main__`. Put the code in a file with an
  `if __name__ == "__main__":` guard, upload it and run it with `--bash 'python3 script.py'`:
  `agent-sandbox run --bash 'python3 par.py' --upload par.py`.
* **Harmless noise on stderr/log:** `OpenBLAS WARNING - could not determine the L2 cache size` and TensorFlow's
  `failed call to cuInit` lines. Do not treat them as failures.
* **Environment:** `PYTHONPATH=:/usr/bin/entry`, `MPLCONFIGDIR=/tmp/matplotlib_config_dir`, matplotlib uses the
  non-interactive `agg` backend: save figures with `plt.savefig("fig.png")`, never `plt.show()`. PID 1 is the
  platform's `python3 /usr/bin/entry/entry_point`; do not kill it. Environment variable names seen (values were not
  inspected): `BORG_CONTAINER_RUNTIME`, `GPG_KEY`, `LANG`, `MPLCONFIGDIR`, `PATH`, `PYTHONPATH`, `PYTHON_*`,
  `SANDBOX_INPUT_OUTPUT_DIRECTORY`, `XBOX_PARALLEL_SOCKET`. None looks like a credential, and with no network the
  sandbox cannot call Google APIs with your identity anyway.

## Long runs

* A single call that slept for **200 s** (printing every 50 s) completed normally.
* A single call meant to run **480 s** (one `run`, `--ttl 15m`) ended with
  `ServerError: 503 UNAVAILABLE ... The service is currently unavailable.` and no result. It is not known whether
  this was a per-call time limit or a transient service error, because it was not repeated. The sandbox was
  already gone afterwards (`agent-sandbox status` showed 0 running).
* So: treat calls up to ~200 s as safe, and for anything longer split the work into several `exec` calls in a
  started sandbox (files and background processes persist between calls), writing checkpoints to files. Do not
  count on a single call surviving several minutes. A 503 is not a code failure: retry once before debugging.

## Planning checklist

1. **Fits in ~1.5 GB RAM?** If you process a big CSV, read it in chunks or select columns. A silent `-1` is OOM.
2. **Needs the internet or a package that is not in the table above?** It will not work. Vendor pure-Python wheels
   or source by uploading them and `pip install --no-index --find-links . pkg` *(not tested)*, or pick another tool.
3. **Needs `torch`, GPU, ffmpeg, LaTeX, Node, Java, R?** Not available in this sandbox.
4. **CPU-bound and parallel?** Expect ~2x, not 8x. Use scripts (not `--code`) for `multiprocessing`.
5. **Heavy imports?** TensorFlow alone costs ~13 s of billed time per `run`. Use `start` + several `exec` calls to
   pay it once, then `stop`.
6. **Results written to a visible file in the working directory?** Otherwise they are not returned.
7. **NLP with pretrained models?** spaCy models and NLTK data are absent; upload them or skip.

## Re-measuring

```bash
cat > libs.py <<'EOF'
import sys
from importlib import metadata
print(sys.version)
for n, v in sorted((d.metadata["Name"], d.version) for d in metadata.distributions()):
    print(f"{n}=={v}")
EOF
agent-sandbox run --file libs.py --max-output-chars 0
agent-sandbox run --bash 'nproc; grep MemTotal /proc/meminfo; df -h /home/bard /tmp; python3 --version'
```
