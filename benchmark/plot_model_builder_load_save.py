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

The conversion step downloads a large model (~16 GB for ``Qwen/Qwen3-8B``)
and runs the model builder, so it is skipped automatically when the produced
ONNX file is already present, or when the required tools are not installed.

The model to convert is controlled by the ``MODEL_ID`` environment variable
and defaults to ``Qwen/Qwen3-0.6B`` to keep the example cheap to run. Set it
to any Hugging Face model id supported by the ``onnxruntime-genai`` model
builder, for example::

    MODEL_ID=Qwen/Qwen3-8B python benchmark/plot_model_builder_load_save.py

The output directory is derived from ``MODEL_ID`` and written under
``temp_plot_model_builder_load_save/<model-name>``.
"""

import os
import subprocess
import sys
import time

MODEL_ID = os.environ.get("MODEL_ID", "Qwen/Qwen3-0.6B")
ROOT = os.environ.get("ROOT", ".")
OUTPUT_DIR = f"{ROOT}/temp_plot_model_builder_load_save/{MODEL_ID.split('/')[-1]}"


###################################################
# Step 1: convert model to CPU int4 with mbext.
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


def measure_onnx(path: str) -> tuple[float, float]:
    import onnx

    t0 = time.perf_counter()
    model = onnx.load(path, load_external_data=True)
    load_time = time.perf_counter() - t0

    out_path = path + ".onnx-resave.onnx"
    t0 = time.perf_counter()
    onnx.save(model, out_path, save_as_external_data=True)
    save_time = time.perf_counter() - t0

    return load_time, save_time


def measure_onnx_light(path: str) -> tuple[float, float]:
    import onnx_light.onnx as onnxl

    t0 = time.perf_counter()
    model = onnxl.load(path, load_external_data=True)
    load_time = time.perf_counter() - t0

    out_path = path + ".onnx-resave.onnx"
    t0 = time.perf_counter()
    onnxl.save(model, out_path, save_as_external_data=True)
    save_time = time.perf_counter() - t0

    return load_time, save_time


def measure_onnx_ir(path: str) -> tuple[float, float]:
    import onnx_ir as ir
    from onnx_ir import external_data

    t0 = time.perf_counter()
    model = ir.load(path)
    external_data.load_to_model(model)
    load_time = time.perf_counter() - t0

    out_path = path + ".onnx-ir-resave.onnx"
    t0 = time.perf_counter()
    ir.save(model, out_path, external_data=os.path.split(path)[-1] + ".onnx-ir-resave.data")
    save_time = time.perf_counter() - t0

    return load_time, save_time


###################################################
# Run the benchmarks and print a small report.
# --------------------------------------------

if os.path.exists(onnx_path):
    results: dict[str, tuple[float, float]] = {}
    for name, fn in (
        ("onnx.1", measure_onnx),
        ("onnx_light.1", measure_onnx_light),
        ("onnx_ir.1", measure_onnx_ir),
        ("onnx.2", measure_onnx),
        ("onnx_light.2", measure_onnx_light),
        ("onnx_ir.2", measure_onnx_ir),
        ("onnx.3", measure_onnx),
        ("onnx_light.3", measure_onnx_light),
        ("onnx_ir.3", measure_onnx_ir),
    ):
        try:
            results[name] = fn(onnx_path)
        except ImportError as exc:
            print(f"Skipping {name}: {exc}")

    print()
    print(f"Benchmark results for {onnx_path}")
    print(f"{'library':<12} {'load (s)':>12} {'save (s)':>12}")
    for name, (load_s, save_s) in sorted(results.items()):
        print(f"{name:<12} {load_s:>12.3f} {save_s:>12.3f}")
else:
    print(f"ONNX model not found at {onnx_path}; run the conversion step above to produce it.")
