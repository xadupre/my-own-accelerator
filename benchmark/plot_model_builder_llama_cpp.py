"""
Compare modelbuilder and llama.cpp on arnir0/Tiny-LLM
======================================================

This example benchmarks inference throughput on
`arnir0/Tiny-LLM <https://huggingface.co/arnir0/Tiny-LLM>`_ using two backends:

1. **modelbuilder** – converts the checkpoint to ONNX int4 CPU via
   :epkg:`onnxruntime-genai` and runs inference through the
   ``onnxruntime_genai`` Python API.
2. **llama.cpp** – converts the checkpoint to GGUF format via
   ``llama.cpp`` ``convert_hf_to_gguf.py`` (or downloads a pre-built GGUF
   from HuggingFace if available) and runs inference through the
   ``llama-cpp-python`` package,
   ``pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu``

For each backend the script measures:

* **first-token latency** (seconds) – time until the first generated token.
* **throughput** (tokens / second) – new tokens generated per second after
  the first token.
* **total time** (seconds) – wall-clock time for the full generation.

The model to benchmark is controlled by ``MODEL_ID`` (default
``arnir0/Tiny-LLM``).  Override via::

    MODEL_ID=arnir0/Tiny-LLM python benchmark/plot_model_builder_llama_cpp.py

Generated artefacts are written under
``temp_plot_model_builder_llama_cpp/<model-name>/``.
"""

import os
import subprocess
import sys
import time
import traceback

MODEL_ID = os.environ.get("MODEL_ID", "arnir0/Tiny-LLM")
ROOT = os.environ.get("ROOT", ".")
MODEL_NAME = MODEL_ID.split("/")[-1]
OUTPUT_DIR = f"{ROOT}/temp_plot_model_builder_llama_cpp/{MODEL_NAME}"

PROMPT = "Once upon a time in a land far away,"
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "50"))
N_RUNS = int(os.environ.get("N_RUNS", "3"))


def _log(msg: str) -> None:
    print(f"[bench {time.strftime('%H:%M:%S')}] {msg}", flush=True)


###################################################
# Step 1: convert model to CPU int4 with modelbuilder.
# ----------------------------------------------------
#
# The ``onnxruntime-genai`` model builder converts a Hugging Face checkpoint
# to an ONNX model quantized to ``int4`` for CPU execution.

MBEXT_DIR = os.path.join(OUTPUT_DIR, "mbext")


def convert_with_mbext(model_id: str, output_dir: str) -> str:
    """Convert *model_id* to CPU int4 ONNX using onnxruntime-genai model builder.

    Returns the path to the generated ``model.onnx`` file.
    """
    onnx_path = os.path.join(output_dir, "model.onnx")
    if os.path.exists(onnx_path):
        _log(f"mbext: reusing existing ONNX model at {onnx_path}")
        return onnx_path

    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "onnxruntime_genai.models.builder",
        "-m",
        model_id,
        "-o",
        output_dir,
        "-p",
        "int4",
        "-e",
        "cpu",
    ]
    _log("mbext: running " + " ".join(cmd))
    subprocess.run(cmd, check=True)
    return onnx_path


###################################################
# Step 2: convert model to GGUF for llama.cpp.
# --------------------------------------------
#
# ``llama.cpp`` requires models in GGUF format.  We use the bundled
# ``convert_hf_to_gguf.py`` script from the ``llama-cpp-python`` package to
# produce a ``Q4_0`` GGUF file from the original Hugging Face checkpoint.
# If the GGUF already exists the conversion is skipped.

GGUF_DIR = os.path.join(OUTPUT_DIR, "gguf")
GGUF_PATH = os.path.join(GGUF_DIR, f"{MODEL_NAME}-Q4_0.gguf")


def _find_llama_convert_script() -> str | None:
    """Return the path to convert_hf_to_gguf.py from llama-cpp-python, or None.

    Some older builds of llama-cpp-python bundle the conversion script inside
    the package directory.  Newer builds no longer include it; in that case the
    caller should install a standalone copy of llama.cpp and point
    ``convert_hf_to_gguf.py`` to the model manually.
    """
    try:
        import llama_cpp  # noqa: F401

        pkg_dir = os.path.dirname(llama_cpp.__file__)
        # Script location varies across llama-cpp-python releases.
        for rel in ("convert_hf_to_gguf.py", os.path.join("llava", "convert_hf_to_gguf.py")):
            candidate = os.path.join(pkg_dir, rel)
            if os.path.exists(candidate):
                return candidate
    except ImportError:
        traceback.print_exc()
    return None


def download_hf_model(model_id: str, local_dir: str) -> str:
    """Download a Hugging Face model to *local_dir* using huggingface_hub.

    Returns *local_dir*.
    """
    from huggingface_hub import snapshot_download

    _log(f"gguf: downloading {model_id} to {local_dir}")
    snapshot_download(repo_id=model_id, local_dir=local_dir)
    return local_dir


def convert_to_gguf(model_id: str, gguf_path: str) -> str:
    """Download *model_id* and convert to GGUF Q4_0.

    Returns the path to the GGUF file.
    """
    if os.path.exists(gguf_path):
        _log(f"gguf: reusing existing GGUF at {gguf_path}")
        return gguf_path

    os.makedirs(os.path.dirname(gguf_path), exist_ok=True)

    # Download the HF checkpoint first.
    hf_dir = os.path.join(OUTPUT_DIR, "hf_cache")
    download_hf_model(model_id, hf_dir)

    # Try to use the bundled conversion script.
    script = _find_llama_convert_script()
    if script is None:
        raise FileNotFoundError(
            "convert_hf_to_gguf.py not found inside llama-cpp-python package. "
            "Please install a version of llama-cpp-python that bundles the script, "
            "or place a pre-built GGUF at: " + gguf_path
        )

    cmd = [
        sys.executable,
        script,
        hf_dir,
        "--outfile",
        gguf_path,
        "--outtype",
        "q4_0",
    ]
    _log("gguf: running " + " ".join(cmd))
    subprocess.run(cmd, check=True)
    return gguf_path


###################################################
# Step 3: benchmark helpers.
# --------------------------


def benchmark_mbext(onnx_dir: str, prompt: str, max_new_tokens: int) -> dict[str, float]:
    """Run inference with onnxruntime-genai and return timing metrics."""
    import onnxruntime_genai as og

    _log(f"mbext: loading model from {onnx_dir}")
    t_load = time.perf_counter()
    model = og.Model(onnx_dir)
    tokenizer = og.Tokenizer(model)
    load_time = time.perf_counter() - t_load
    _log(f"mbext: model loaded in {load_time:.3f} s")

    input_tokens = tokenizer.encode(prompt)
    params = og.GeneratorParams(model)
    params.max_length = len(input_tokens) + max_new_tokens
    params.input_ids = input_tokens

    _log(f"mbext: generating up to {max_new_tokens} new tokens …")
    t0 = time.perf_counter()
    generator = og.Generator(model, params)

    first_token_time: float | None = None
    n_new = 0
    while not generator.is_done():
        generator.compute_logits()
        generator.generate_next_token()
        if first_token_time is None:
            first_token_time = time.perf_counter() - t0
        n_new += 1

    total_time = time.perf_counter() - t0
    ttft = first_token_time if first_token_time is not None else float("nan")
    throughput = (
        (n_new - 1) / (total_time - ttft) if total_time > ttft and n_new > 1 else float("nan")
    )
    _log(
        f"mbext: {n_new} tokens in {total_time:.3f} s (ttft={ttft:.3f} s, {throughput:.1f} tok/s)"
    )
    return {
        "load_s": load_time,
        "ttft_s": ttft,
        "total_s": total_time,
        "n_tokens": float(n_new),
        "throughput_tok_s": throughput,
    }


def benchmark_llama_cpp(gguf_path: str, prompt: str, max_new_tokens: int) -> dict[str, float]:
    """Run inference with llama-cpp-python and return timing metrics."""
    from llama_cpp import Llama

    _log(f"llama.cpp: loading model from {gguf_path}")
    t_load = time.perf_counter()
    llm = Llama(model_path=gguf_path, n_ctx=512, n_threads=os.cpu_count() or 4, verbose=False)
    load_time = time.perf_counter() - t_load
    _log(f"llama.cpp: model loaded in {load_time:.3f} s")

    _log(f"llama.cpp: generating up to {max_new_tokens} new tokens …")
    t0 = time.perf_counter()
    first_token_time: float | None = None
    n_new = 0

    stream = llm(
        prompt,
        max_tokens=max_new_tokens,
        stream=True,
        echo=False,
    )
    for chunk in stream:
        if first_token_time is None:
            first_token_time = time.perf_counter() - t0
        n_new += 1

    total_time = time.perf_counter() - t0
    ttft = first_token_time if first_token_time is not None else float("nan")
    throughput = (
        (n_new - 1) / (total_time - ttft) if total_time > ttft and n_new > 1 else float("nan")
    )
    _log(
        f"llama.cpp: {n_new} tokens in {total_time:.3f} s "
        f"(ttft={ttft:.3f} s, {throughput:.1f} tok/s)"
    )
    return {
        "load_s": load_time,
        "ttft_s": ttft,
        "total_s": total_time,
        "n_tokens": float(n_new),
        "throughput_tok_s": throughput,
    }


###################################################
# Step 4: run conversions and benchmarks.
# ----------------------------------------

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- modelbuilder ---
mbext_onnx_path = os.path.join(MBEXT_DIR, "model.onnx")
try:
    mbext_onnx_path = convert_with_mbext(MODEL_ID, MBEXT_DIR)
except (FileNotFoundError, subprocess.CalledProcessError, ImportError, OSError) as exc:
    _log(f"mbext: skipping conversion – {exc}")
    traceback.print_exc()

# --- llama.cpp GGUF ---
gguf_path = GGUF_PATH
try:
    gguf_path = convert_to_gguf(MODEL_ID, GGUF_PATH)
except (FileNotFoundError, subprocess.CalledProcessError, ImportError, OSError) as exc:
    _log(f"gguf: skipping conversion – {exc}")
    traceback.print_exc()

###################################################
# Step 5: run benchmarks and print report.
# -----------------------------------------

results: dict[str, list[dict[str, float]]] = {}

for run in range(1, N_RUNS + 1):
    _log(f"--- run {run}/{N_RUNS} ---")

    # modelbuilder
    if os.path.exists(MBEXT_DIR):
        key = "mbext"
        try:
            metrics = benchmark_mbext(MBEXT_DIR, PROMPT, MAX_NEW_TOKENS)
            results.setdefault(key, []).append(metrics)
        except ImportError as exc:
            _log(f"mbext: benchmark skipped (missing dependency) – {exc}")
            traceback.print_exc()
        except (RuntimeError, ValueError, OSError) as exc:
            _log(f"mbext: benchmark failed – {exc}")
            traceback.print_exc()
    else:
        _log(f"mbext: skipping benchmark – model dir not found: {MBEXT_DIR}")

    # llama.cpp
    if os.path.exists(gguf_path):
        key = "llama.cpp"
        try:
            metrics = benchmark_llama_cpp(gguf_path, PROMPT, MAX_NEW_TOKENS)
            results.setdefault(key, []).append(metrics)
        except ImportError as exc:
            _log(f"llama.cpp: benchmark skipped (missing dependency) – {exc}")
            traceback.print_exc()
        except (RuntimeError, ValueError, OSError) as exc:
            _log(f"llama.cpp: benchmark failed – {exc}")
            traceback.print_exc()
    else:
        _log(f"llama.cpp: skipping benchmark – GGUF not found: {gguf_path}")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


print()
print(f"Benchmark results for model: {MODEL_ID}")
print(f"Prompt: {PROMPT!r}")
print(f"Max new tokens: {MAX_NEW_TOKENS}  |  Runs: {N_RUNS}")
print()
cols = ("backend", "load (s)", "ttft (s)", "total (s)", "tok/s", "n_tokens")
header = f"{cols[0]:<14} {cols[1]:>10} {cols[2]:>10} {cols[3]:>10} {cols[4]:>10} {cols[5]:>10}"
print(header)
print("-" * len(header))
for backend, runs in sorted(results.items()):
    avg_load = _mean([r["load_s"] for r in runs])
    avg_ttft = _mean([r["ttft_s"] for r in runs])
    avg_total = _mean([r["total_s"] for r in runs])
    avg_tput = _mean([r["throughput_tok_s"] for r in runs])
    avg_ntok = _mean([r["n_tokens"] for r in runs])
    print(
        f"{backend:<14} {avg_load:>10.3f} {avg_ttft:>10.3f} {avg_total:>10.3f}"
        f" {avg_tput:>10.1f} {avg_ntok:>10.0f}"
    )

if not results:
    print("No benchmark results collected. Install onnxruntime-genai and/or llama-cpp-python.")
