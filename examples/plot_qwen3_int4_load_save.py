"""
Convert Qwen3-8B to CPU int4 and benchmark ONNX I/O
====================================================

This example shows how to:

1. Convert `Qwen/Qwen3-8B <https://huggingface.co/Qwen/Qwen3-8B>`_ to an ONNX
   model quantized to ``int4`` for CPU execution using the
   :epkg:`onnxruntime-genai` model builder (also known as ``mbext``).
2. Measure the time needed to **load** and **save** the resulting ONNX
   model with the standard :epkg:`onnx` package.
3. Measure the time needed to **load** and **save** the same ONNX model
   with the lightweight :epkg:`onnx_ir` package (``onnx-light``).

The conversion step downloads a large model (~16 GB) and runs the model
builder, so it is skipped automatically when the produced ONNX file is
already present, or when the required tools are not installed.
"""

import os
import subprocess
import sys
import time

MODEL_ID = "Qwen/Qwen3-8B"
OUTPUT_DIR = os.environ.get("QWEN3_INT4_DIR", "qwen3-8b-int4-cpu")


###################################################
# Step 1: convert Qwen3-8B to CPU int4 with mbext.
# -----------------------------------------------
#
# The ``onnxruntime-genai`` package ships a *model builder* which exports a
# Hugging Face checkpoint to an ONNX model with the requested precision and
# execution provider. The CLI is equivalent to::
#
#     python -m onnxruntime_genai.models.builder \
#         -m Qwen/Qwen3-8B \
#         -o qwen3-8b-int4-cpu \
#         -p int4 \
#         -e cpu
#
# We only run the conversion when the output ``model.onnx`` is missing so the
# example can be re-run cheaply.


def convert_with_mbext(model_id: str, output_dir: str) -> str:
    """Convert *model_id* to CPU int4 ONNX using onnxruntime-genai.

    Returns the path to the generated ``model.onnx`` file.
    """
    onnx_path = os.path.join(output_dir, "model.onnx")
    if os.path.exists(onnx_path):
        print(f"Reusing existing ONNX model at {onnx_path}")
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
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return onnx_path


try:
    onnx_path = convert_with_mbext(MODEL_ID, OUTPUT_DIR)
except (FileNotFoundError, subprocess.CalledProcessError, ImportError) as exc:
    print(f"Skipping conversion: {exc}")
    onnx_path = os.path.join(OUTPUT_DIR, "model.onnx")


###################################################
# Step 2: load and save with ``onnx``.
# ------------------------------------
#
# The standard :epkg:`onnx` package parses the protobuf into Python objects,
# which is convenient but also relatively expensive for very large models.


def measure_onnx(path: str) -> tuple[float, float]:
    import onnx

    t0 = time.perf_counter()
    model = onnx.load(path)
    load_time = time.perf_counter() - t0

    out_path = path + ".onnx-resave.onnx"
    t0 = time.perf_counter()
    onnx.save(model, out_path)
    save_time = time.perf_counter() - t0

    return load_time, save_time


###################################################
# Step 3: load and save with ``onnx_ir`` (onnx-light).
# ----------------------------------------------------
#
# :epkg:`onnx_ir` exposes a lighter intermediate representation that avoids
# materializing the full protobuf object tree, which is typically faster for
# large models.


def measure_onnx_ir(path: str) -> tuple[float, float]:
    import onnx_ir as ir

    t0 = time.perf_counter()
    model = ir.load(path)
    load_time = time.perf_counter() - t0

    out_path = path + ".onnx-ir-resave.onnx"
    t0 = time.perf_counter()
    ir.save(model, out_path)
    save_time = time.perf_counter() - t0

    return load_time, save_time


###################################################
# Run the benchmarks and print a small report.
# --------------------------------------------

if os.path.exists(onnx_path):
    results: dict[str, tuple[float, float]] = {}
    for name, fn in (("onnx", measure_onnx), ("onnx_ir", measure_onnx_ir)):
        try:
            results[name] = fn(onnx_path)
        except ImportError as exc:
            print(f"Skipping {name}: {exc}")

    print()
    print(f"Benchmark results for {onnx_path}")
    print(f"{'library':<10} {'load (s)':>12} {'save (s)':>12}")
    for name, (load_s, save_s) in results.items():
        print(f"{name:<10} {load_s:>12.3f} {save_s:>12.3f}")
else:
    print(f"ONNX model not found at {onnx_path}; run the conversion step above to produce it.")
